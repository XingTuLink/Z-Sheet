"""Recognise conventional "missing value" placeholders in spreadsheets.

Real-world workbooks (and especially scientific metadata exports such as
NCBI/SRA) mark absent measurements with controlled vocabulary instead of
leaving cells blank: ``not provided``, ``not collected``, ``Not found``...
These tokens are *missing data*, not literal strings: they must not drive
type inference, pollute enum sets, or block record import as "invalid date".

Two shapes are recognised:

* **Bare markers** — an exact controlled token (``not provided``, ``N/A``,
  ``无``, ``-``).
* **Marker phrases** — a bare marker immediately followed by an inline
  explanation introduced with a colon or a paired parenthesis:
  ``missing: lab stock`` (an official INSDC/SRA controlled-value form),
  ``Not applicable (egg samples)``, ``未提供：样品损坏``. The delimiter must
  touch the marker, so ordinary remarks such as ``暂无库存（下月补采）`` or
  ``Brazil: Rio de Janeiro`` are never mistaken for missing data.

The policy is deliberately conservative:

* Matching is case/whitespace-insensitive.
* Callers decide what to do with a match. Only *typed* columns coerce a
  token to null (see confirmation._coerce_cell); free-text columns keep the
  literal value, because words like "无" / "none" can be genuine remarks.
"""

from __future__ import annotations

import re

from backend.ingestion.schemas import Cell

# English / INSDC-style markers (lower-cased before matching).
_EN_TOKENS = frozenset(
    {
        "not provided",
        "not found",
        "not collected",
        "not available",
        "not applicable",
        "not determined",
        "not reported",
        "not recorded",
        "not specified",
        "restricted access",
        "missing",
        "unknown",
        "none",
        "null",
        "nil",
        "n/a",
        "n.a",
        "n.a.",
        "na",
        "n/a.",
    }
)

# Chinese business-convention markers.
_ZH_TOKENS = frozenset(
    {
        "无",
        "暂无",
        "无数据",
        "未提供",
        "未填写",
        "未采集",
        "未记录",
        "未知",
        "不详",
        "缺失",
        "空",
    }
)

# Pure-symbol placeholders ("-", "--", "—", "/", "."). A whole row of these is
# already filtered as a pseudo-row by the parser; inside a typed column they
# universally mean "no value". They never open a phrase ("-: foo" is text).
_SYMBOL_TOKENS = frozenset({"-", "--", "—", "－－", "/", "."})

_ALL_TOKENS = _EN_TOKENS | _ZH_TOKENS | _SYMBOL_TOKENS

_WS_RE = re.compile(r"\s+")

# Marker phrases: "<marker>: <reason>" or "<marker> (<reason>)". The delimiter
# has to follow the marker directly (apart from whitespace), so a remark that
# merely starts with the same characters ("暂无库存（…）", "none of the
# above") does not match. Longest-first alternation keeps "not applicable"
# winning over any shorter shared prefix.
_PHRASE_OPENERS = sorted(
    (_EN_TOKENS | _ZH_TOKENS), key=len, reverse=True
)
_PHRASE_RE = re.compile(
    r"^(?:{openers})\s*(?:[:：].*|[(（].*[)）])\s*$".format(
        openers="|".join(re.escape(token) for token in _PHRASE_OPENERS)
    ),
    re.IGNORECASE,
)


def is_missing_token(value: Cell) -> bool:
    """Return True when a cell text is a conventional missing-value marker."""
    if value is None:
        return True
    text = _WS_RE.sub(" ", value.strip())
    if text == "":
        return True
    if text in _ALL_TOKENS:
        return True
    if text.lower() in _EN_TOKENS:
        return True
    return bool(_PHRASE_RE.match(text))
