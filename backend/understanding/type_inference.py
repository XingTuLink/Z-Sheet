"""Deterministic field type inference for the six V0.1 types (Days 7-8).

Types: string / number / money / date / enum / phone.

Inputs allowed by design 11.2: column name, data sample, value distribution,
format features. Cross-sheet information is explicitly out of scope here
(relation inference arrives on Day 10). No AI: every rule in this module is
fixed and tested. Confidence uses the tiers frozen in design 10.1
(0.85 high / 0.60 medium); needs_review is derived mechanically.

Evidence is gathered from the deterministic sample; enum cardinality and the
exact enum candidate values are computed over all rows via the profile.
"""

from __future__ import annotations

import datetime as dt
import re

from backend.ingestion.schemas import Cell, ParsedSheet

from .schemas import CONFIDENCE_HIGH, InferredField, SheetProfile

# --- name keyword groups (matched case-insensitently as substrings) --------
MONEY_NAME_KEYWORDS = (
    "金额", "价格", "单价", "总价", "费用", "成本", "收入",
    "工资", "薪资", "奖金", "报销", "amount", "price", "cost", "fee",
    "salary", "income", "revenue", "total",
)
DATE_NAME_KEYWORDS = ("日期", "时间", "date", "time", "day")
PHONE_NAME_KEYWORDS = ("电话", "手机", "联系", "phone", "mobile", "tel")
CATEGORY_NAME_KEYWORDS = (
    "状态", "类型", "类别", "分类", "区域", "地区", "级别", "等级", "性别",
    "status", "state", "type", "category", "region", "level", "grade", "gender",
)

_CURRENCY_CHARS = "¥￥$€￠£"
_PLAIN_NUMBER_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_THOUSANDS_NUMBER_RE = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_MONEY_RE = re.compile(
    rf"^[{re.escape(_CURRENCY_CHARS)}]?\s*[+-]?(?:\d{{1,3}}(?:,\d{{3}})*|\d+)(?:\.\d+)?\s*"
    rf"[{re.escape(_CURRENCY_CHARS)}元块]?$"
)
_MOBILE_RE = re.compile(r"^(?:\+?86)?1[3-9]\d{9}$")
_LANDLINE_RE = re.compile(r"^0\d{2,3}\d{7,8}$")

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%Y年%m月%d日",
)

ENUM_MAX_DISTINCT = 20
ENUM_MAX_RATIO = 0.5
ENUM_MIN_NON_NULL = 3
MIN_ACCEPT_RATIO = 0.60


def _has_keyword(name: str, keywords: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def is_number(value: str) -> bool:
    return bool(_PLAIN_NUMBER_RE.match(value) or _THOUSANDS_NUMBER_RE.match(value))


def is_money(value: str) -> bool:
    """A numeric value carrying an explicit currency marker."""
    text = value.strip()
    if not _MONEY_RE.match(text):
        return False
    return any(char in text for char in _CURRENCY_CHARS) or text.endswith(("元", "块"))


def is_phone(value: str) -> bool:
    digits = re.sub(r"[\s\-]", "", value)
    return bool(_MOBILE_RE.match(digits) or _LANDLINE_RE.match(digits))


def is_date(value: str) -> bool:
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            dt.datetime.strptime(text, fmt)
            return True
        except ValueError:
            continue
    return False


def _ratio(matches: int, total: int) -> float:
    return matches / total if total else 0.0


def _confidence_band(ratio: float) -> float:
    """Map a match ratio to one of the frozen confidence bands."""
    if ratio >= 0.98:
        return 0.98
    if ratio >= 0.95:
        return 0.95
    if ratio >= 0.90:
        return 0.90
    if ratio >= 0.80:
        return 0.75
    return 0.61  # anything >= MIN_ACCEPT_RATIO lands in low confidence


def _guess(
    column: str,
    ftype: str,
    confidence: float,
    signals: list[str],
    enum_values: list[str] | None = None,
) -> InferredField:
    return InferredField(
        column=column,
        type=ftype,  # type: ignore[arg-type]
        confidence=confidence,
        needs_review=confidence < CONFIDENCE_HIGH,
        signals=signals,
        enum_values=enum_values or [],
    )


def infer_field(
    column: str,
    values: list[Cell],
    distinct_count: int,
    non_null_total: int,
    enum_values: list[str],
) -> InferredField:
    """Infer one column's type.

    `values` are the sampled non-null strings; distribution figures come from
    the full-column profile; `enum_values` is the full distinct set (already
    known to be small) used when the result is enum.
    """
    sample = [value for value in values if value is not None]
    n = len(sample)

    if n == 0 or non_null_total == 0:
        return _guess(column, "string", 0.0, ["no_non_null_values"])

    r_date = _ratio(sum(is_date(v) for v in sample), n)
    r_phone = _ratio(sum(is_phone(v) for v in sample), n)
    r_money_symbol = _ratio(sum(is_money(v) for v in sample), n)
    r_number = _ratio(sum(is_number(v) for v in sample), n)

    distribution_fits_enum = (
        non_null_total >= ENUM_MIN_NON_NULL
        and distinct_count <= ENUM_MAX_DISTINCT
        and distinct_count / non_null_total <= ENUM_MAX_RATIO
    )
    category_name = _has_keyword(column, CATEGORY_NAME_KEYWORDS)
    text_majority = r_number < 0.5

    # Conflict priority: format-specific types first, then semantic/statistical.

    # 1. Date — calendar validation makes false positives extremely unlikely.
    if r_date >= MIN_ACCEPT_RATIO:
        signals = ["calendar_validated"]
        if _has_keyword(column, DATE_NAME_KEYWORDS):
            signals.append("name_keyword:date")
        if r_date < 0.90:
            signals.append("mixed_values")
        return _guess(column, "date", _confidence_band(r_date), signals)

    # 2. Phone — runs before number: 11-digit mobile numbers would otherwise be
    # swallowed by the numeric rule.
    if r_phone >= MIN_ACCEPT_RATIO:
        signals = ["phone_pattern"]
        if _has_keyword(column, PHONE_NAME_KEYWORDS):
            signals.append("name_keyword:phone")
        if r_phone < 0.90:
            signals.append("mixed_values")
        return _guess(column, "phone", _confidence_band(r_phone), signals)

    # 3. Enum hinted by a category-style column name. Numeric status/level
    # columns (e.g. 状态 = 0/1) would otherwise become plain numbers.
    if distribution_fits_enum and category_name:
        confidence = 0.88 if text_majority else 0.75
        return _guess(
            column, "enum", confidence,
            ["low_cardinality_distribution", "name_keyword:category"],
            enum_values,
        )

    # 4. Money — explicit currency markers, or numeric values under a money-ish
    # column name (the demo data has plain-number amounts).
    if r_money_symbol >= MIN_ACCEPT_RATIO:
        signals = ["currency_symbol"]
        if r_money_symbol < 0.90:
            signals.append("mixed_values")
        return _guess(column, "money", _confidence_band(r_money_symbol), signals)
    if r_number >= MIN_ACCEPT_RATIO and _has_keyword(column, MONEY_NAME_KEYWORDS):
        signals = ["name_keyword:money"]
        if r_number < 0.90:
            signals.append("mixed_values")
        return _guess(
            column, "money", min(0.98, _confidence_band(r_number) + 0.05),
            signals,
        )

    # 5. Number.
    if r_number >= MIN_ACCEPT_RATIO:
        signals = ["numeric_format"]
        if r_number < 0.90:
            signals.append("mixed_values")
        return _guess(column, "number", _confidence_band(r_number), signals)

    # 6. Enum by distribution alone — only when values are mostly non-numeric,
    # so low-cardinality measurement columns stay numbers.
    if distribution_fits_enum and text_majority:
        return _guess(column, "enum", 0.75, ["low_cardinality_distribution"], enum_values)

    # 7. String fallback. Cleanly non-parseable free text is high confidence;
    # values that partly match structured formats stay low for human review.
    structured = max(r_date, r_phone, r_money_symbol, r_number)
    confidence = 0.9 if structured < 0.1 else 0.55
    return _guess(
        column, "string", confidence,
        ["free_text"] if confidence >= 0.9 else ["ambiguous"],
    )


def infer_sheet(sheet: ParsedSheet, profile: SheetProfile) -> list[InferredField]:
    """Infer every column using the Day 6 profile.

    Format evidence comes from the deterministic sample; cardinality from the
    profile. Exact enum candidate sets require a full-column scan, so that
    scan happens only for columns already passing the enum distribution gate.
    """
    width = len(sheet.columns)
    profile_by_name = {column.name: column for column in profile.columns}

    enum_candidates: dict[int, list[str]] = {}
    for pos in range(width):
        stats = profile_by_name.get(sheet.columns[pos])
        if stats is None:
            continue
        fits = (
            stats.non_null_count >= ENUM_MIN_NON_NULL
            and stats.distinct_count <= ENUM_MAX_DISTINCT
            and stats.distinct_count / stats.non_null_count <= ENUM_MAX_RATIO
        )
        if fits:
            values: set[str] = set()
            for row in sheet.rows:
                if pos >= len(row):
                    continue
                value = row[pos]
                if value is not None:
                    values.add(value)
            enum_candidates[pos] = sorted(values)

    results: list[InferredField] = []
    for pos, column in enumerate(sheet.columns):
        stats = profile_by_name.get(column)
        sample_values: list[Cell] = [
            row[pos] if pos < len(row) else None for row in profile.sample_rows
        ]
        if stats is None:
            results.append(
                InferredField(
                    column=column, type="string", confidence=0.0,
                    needs_review=True, signals=["no_profile"],
                )
            )
            continue
        results.append(
            infer_field(
                column=column,
                values=sample_values,
                distinct_count=stats.distinct_count,
                non_null_total=stats.non_null_count,
                enum_values=enum_candidates.get(pos, []),
            )
        )
    return results
