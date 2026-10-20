"""Evaluate Excel formulas that carry no cached result.

``openpyxl`` reads a formula cell as ``None`` under ``data_only=True`` when the
file was produced by a writer that never calculated results. That silently
empties whole amount columns, so the parser calls this module to recompute the
subset of formulas it can evaluate deterministically: arithmetic over cell
references (same or other sheets) plus the SUM / SUMIF / AVERAGE / MIN / MAX
family. Anything outside that subset is left empty and reported to the user.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .schemas import Cell

# Cells may hold display text such as "¥1,200.50" or "1 200 元".
_CURRENCY_RE = re.compile(r"[¥￥$€￠£,\s]|元|块")
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")

# A single formula must not scan an unbounded range: one SUMIF over a whole
# column is cheap, fifty thousand of them are not.
_RANGE_BUDGET = 2_000_000

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<number>\d+(?:\.\d+)?)
  | (?P<string>"[^"]*")
  | (?P<func>[A-Za-z][A-Za-z0-9.]*)\s*(?=\()
  | (?P<ref>
        (?:(?:'(?P<qsheet>(?:[^']|'')+)'|(?P<sheet>[^\s!(),'+\-*/&<>]+))!)?
        \$?(?P<c1>[A-Z]{1,3})
        (?:
            \$?(?P<r1>\d+)
            (?::\$?(?P<c2>[A-Z]{1,3})\$?(?P<r2>\d+))?
          | :\$?(?P<c2b>[A-Z]{1,3})(?::\$?(?P<r3>\d+))?
        )
    )
  | (?P<op>[+\-*/(),])
    """,
    re.VERBOSE,
)

FormulaMap = dict[str, dict[tuple[int, int], str]]
"""sheet name -> (0-based row, 0-based column) -> formula text."""

# Evaluated values: a scalar cell/expression result, or a range of cells.
Value = float | str | None
Operand = Value | list[Value]

# ``lastgroup`` reports the innermost group that matched, so a cell/range
# reference would otherwise look like "r1"/"c1" instead of "ref".
_REF_GROUPS = frozenset({"qsheet", "sheet", "c1", "r1", "c2", "r2", "c2b", "r3"})


def _kind(token: re.Match[str]) -> str | None:
    name = token.lastgroup
    return "ref" if name in _REF_GROUPS else name


class FormulaError(Exception):
    """A formula this evaluator cannot (or must not) compute."""


def to_number(value: object) -> float | None:
    """Numeric view of a raw cell, or None when it is not a number."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _CURRENCY_RE.sub("", str(value).strip())
    if not text or not _NUMBER_RE.match(text):
        return None
    return float(text)


def number_to_text(value: float) -> str:
    """Render a computed number the way Excel's general format would."""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _column_index(letters: str) -> int:
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _tokenize(formula: str) -> list[re.Match[str]]:
    text = formula[1:] if formula.startswith("=") else formula
    tokens: list[re.Match[str]] = []
    position = 0
    while position < len(text):
        match = _TOKEN_RE.match(text, position)
        if match is None:
            raise FormulaError(f"无法识别的公式片段：{text[position:]!r}")
        position = match.end()
        if _kind(match) != "ws":
            tokens.append(match)
    return tokens


class _Parser:
    """Recursive-descent parser for the supported formula subset."""

    def __init__(self, tokens: list[re.Match[str]], book: _Evaluator, sheet: str) -> None:
        self._tokens = tokens
        self._position = 0
        self._book = book
        self._sheet = sheet

    def parse(self) -> float | str:
        value = self._expression()
        if self._position != len(self._tokens):
            raise FormulaError("公式末尾存在无法解析的内容")
        if isinstance(value, list) or value is None:
            raise FormulaError("公式结果不是单个值")
        return value

    def _peek(self) -> re.Match[str] | None:
        if self._position >= len(self._tokens):
            return None
        return self._tokens[self._position]

    def _take(self, kind: str | None = None) -> re.Match[str]:
        token = self._peek()
        if token is None or (kind is not None and _kind(token) != kind):
            raise FormulaError("公式结构不完整")
        self._position += 1
        return token

    def _expression(self) -> Operand:
        value = self._term()
        while True:
            token = self._peek()
            if token is None or _kind(token) != "op" or token.group() not in "+-":
                return value
            operator = self._take("op").group()
            right = self._term()
            left_number = self._arithmetic(value)
            right_number = self._arithmetic(right)
            value = (
                left_number + right_number
                if operator == "+"
                else left_number - right_number
            )

    def _term(self) -> Operand:
        value = self._unary()
        while True:
            token = self._peek()
            if token is None or _kind(token) != "op" or token.group() not in "*/":
                return value
            operator = self._take("op").group()
            right = self._unary()
            left_number = self._arithmetic(value)
            right_number = self._arithmetic(right)
            if operator == "/":
                if right_number == 0:
                    raise FormulaError("公式出现除以零")
                value = left_number / right_number
            else:
                value = left_number * right_number

    def _unary(self) -> Operand:
        token = self._peek()
        if token is not None and _kind(token) == "op" and token.group() in "+-":
            operator = self._take("op").group()
            value = self._unary()
            number = self._arithmetic(value)
            return -number if operator == "-" else number
        return self._primary()

    def _primary(self) -> Operand:
        token = self._take()
        kind = _kind(token)
        if kind == "number":
            return float(token.group())
        if kind == "string":
            return token.group()[1:-1].replace('""', '"')
        if kind == "func":
            return self._call(token.group().upper())
        if kind == "ref":
            return self._reference(token)
        if kind == "op" and token.group() == "(":
            value = self._expression()
            closing = self._take("op")
            if closing.group() != ")":
                raise FormulaError("公式括号不匹配")
            return value
        raise FormulaError(f"不支持的公式成分：{token.group()!r}")

    def _call(self, name: str) -> Operand:
        self._take("op")
        arguments: list[Operand] = []
        peek = self._peek()
        if peek is not None and peek.group() == ")":
            self._take("op")
        else:
            while True:
                arguments.append(self._expression())
                token = self._take("op")
                if token.group() == ")":
                    break
                if token.group() != ",":
                    raise FormulaError("函数参数之间缺少逗号")

        if name == "SUM":
            return sum(self._numbers(arguments))
        if name in ("AVERAGE", "AVG"):
            numbers = self._numbers(arguments)
            if not numbers:
                raise FormulaError("AVERAGE 没有可计算的数值")
            return sum(numbers) / len(numbers)
        if name == "MIN":
            numbers = self._numbers(arguments)
            return min(numbers) if numbers else 0.0
        if name == "MAX":
            numbers = self._numbers(arguments)
            return max(numbers) if numbers else 0.0
        if name == "SUMIF":
            return self._sumif(arguments)
        raise FormulaError(f"不支持的函数：{name}")

    @staticmethod
    def _arithmetic(value: Operand) -> float:
        if isinstance(value, list):
            raise FormulaError("区域不能直接参与四则运算")
        number = to_number(value)
        # Excel treats an empty referenced cell as zero in arithmetic.
        if number is None and (value is None or value == ""):
            return 0.0
        if number is None:
            raise FormulaError("公式引用了非数值单元格")
        return number

    def _numbers(self, arguments: Sequence[Operand]) -> list[float]:
        numbers: list[float] = []
        for argument in arguments:
            if isinstance(argument, list):
                for item in argument:
                    number = to_number(item)
                    if number is not None:
                        numbers.append(number)
                continue
            number = to_number(argument)
            if number is not None:
                numbers.append(number)
        return numbers

    def _sumif(self, arguments: Sequence[Operand]) -> float:
        if len(arguments) != 3:
            raise FormulaError("SUMIF 需要 3 个参数")
        criteria_range, criteria, sum_range = arguments
        if not isinstance(criteria_range, list) or not isinstance(sum_range, list):
            raise FormulaError("SUMIF 的参数必须是区域")
        if isinstance(criteria, list):
            raise FormulaError("SUMIF 的条件不能是区域")
        total = 0.0
        for position, item in enumerate(criteria_range):
            if position >= len(sum_range):
                break
            if _matches(item, criteria):
                number = to_number(sum_range[position])
                if number is not None:
                    total += number
        return total

    def _reference(
        self, token: re.Match[str]
    ) -> Operand:
        sheet = token.group("qsheet") or token.group("sheet") or self._sheet
        sheet = sheet.replace("''", "'")
        first_column = _column_index(token.group("c1"))
        first_row_text = token.group("r1")
        last_column_text = token.group("c2") or token.group("c2b")
        last_row_text = token.group("r2") or token.group("r3")

        if last_column_text is None:
            assert first_row_text is not None
            return self._book.cell(sheet, int(first_row_text) - 1, first_column)

        last_column = _column_index(last_column_text)
        if first_row_text is None:
            start_row = 0
            end_row = self._book.max_row(sheet)
        else:
            start_row = int(first_row_text) - 1
            end_row = int(last_row_text) - 1 if last_row_text else start_row
        if (start_row, first_column) > (end_row, last_column):
            start_row, end_row = end_row, start_row
        return self._book.range_values(sheet, first_column, start_row, last_column, end_row)


def _matches(item: Value, criteria: Value) -> bool:
    if isinstance(criteria, list):
        raise FormulaError("SUMIF 的条件不能是区域")
    if criteria is None:
        return item is None or item == ""
    criteria_number = to_number(criteria)
    if criteria_number is not None:
        item_number = to_number(item)
        return item_number is not None and abs(item_number - criteria_number) < 1e-9
    return str(item).strip() == str(criteria).strip()


class _Evaluator:
    """Resolves cell reads, including formula cells on demand."""

    def __init__(self, grids: dict[str, list[list[Cell]]], formulas: FormulaMap) -> None:
        self._grids = grids
        self._formulas = formulas
        self._cache: dict[tuple[str, int, int], Value] = {}
        self._active: set[tuple[str, int, int]] = set()
        self._budget = _RANGE_BUDGET

    def max_row(self, sheet: str) -> int:
        grid = self._grids.get(sheet)
        return len(grid) - 1 if grid else -1

    def cell(self, sheet: str, row: int, column: int) -> Value:
        key = (sheet, row, column)
        if key in self._cache:
            return self._cache[key]
        grid = self._grids.get(sheet)
        text: Cell = None
        if grid is not None and 0 <= row < len(grid):
            cells = grid[row]
            if 0 <= column < len(cells):
                text = cells[column]

        formula = self._formulas.get(sheet, {}).get((row, column))
        if text is None and formula:
            if key in self._active:
                raise FormulaError("公式存在循环引用")
            self._active.add(key)
            try:
                value = _Parser(_tokenize(formula), self, sheet).parse()
            finally:
                self._active.discard(key)
            self._cache[key] = value
            return value

        self._cache[key] = text
        return text

    def range_values(
        self,
        sheet: str,
        first_column: int,
        first_row: int,
        last_column: int,
        last_row: int,
    ) -> list[Value]:
        values: list[Value] = []
        for row in range(first_row, last_row + 1):
            for column in range(first_column, last_column + 1):
                self._budget -= 1
                if self._budget < 0:
                    raise FormulaError("公式引用的区域过大，放弃计算")
                values.append(self.cell(sheet, row, column))
        return values


def evaluate_formulas(
    grids: dict[str, list[list[Cell]]],
    formulas: FormulaMap,
) -> dict[str, tuple[int, int]]:
    """Fill empty formula cells in place.

    Returns, per sheet, ``(resolved, unresolved)`` counts. ``unresolved``
    cells stay empty so the caller can warn about them.
    """
    counts: dict[str, tuple[int, int]] = {}
    for sheet, cells in formulas.items():
        grid = grids.get(sheet)
        if not grid:
            counts[sheet] = (0, 0)
            continue
        evaluator = _Evaluator(grids, formulas)
        resolved = 0
        unresolved = 0
        for row, column in sorted(cells):
            if row >= len(grid):
                unresolved += 1
                continue
            line = grid[row]
            if column < len(line) and line[column] is not None:
                continue
            if column >= len(line):
                line.extend([None] * (column + 1 - len(line)))
            try:
                value = evaluator.cell(sheet, row, column)
            except FormulaError:
                unresolved += 1
                continue
            if value is None:
                unresolved += 1
                continue
            number = to_number(value)
            line[column] = (
                number_to_text(number) if number is not None else str(value).strip() or None
            )
            resolved += 1
        counts[sheet] = (resolved, unresolved)
    return counts
