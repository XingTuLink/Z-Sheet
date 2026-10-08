"""Deterministic entity recognition for the three V0.1 kinds (Day 10).

Kinds (design doc 10-15): customer / order / product, plus `unknown`.

V0.1 scope: one non-empty sheet produces exactly one entity candidate. No
splitting, no cross-sheet merging (Day 11). Evidence combines the sheet name
with field composition, following design example 7.1:
- order  = identifier + flow money + time
- product= identifier + unit price / product attributes, no time
- customer = identifier + contact fields, no flow money and no time

A sheet that matches none of these structurally and carries no business
sheet name becomes `unknown`; design 10.2 forbids fake certainty.
"""

from __future__ import annotations

import re

from backend.ingestion.schemas import ParsedSheet

from .keywords import (
    CONTACT_NAME_KEYWORDS,
    CUSTOMER_SHEET_KEYWORDS,
    DEFAULT_SHEET_NAME_RE_PARTS,
    ENTITY_KIND_LABELS,
    ORDER_SHEET_KEYWORDS,
    PARTY_ID_NAME_KEYWORDS,
    PRICE_NAME_KEYWORDS,
    PRODUCT_ATTR_KEYWORDS,
    PRODUCT_SHEET_KEYWORDS,
    QUANTITY_NAME_KEYWORDS,
    has_keyword,
)
from .schemas import InferredEntity, InferredField

_DEFAULT_SHEET_RE = re.compile(
    r"^(?:" + "|".join(DEFAULT_SHEET_NAME_RE_PARTS) + r")$", re.IGNORECASE
)


def _sheet_name_kind(sheet_name: str) -> str | None:
    """Business kind hinted by the sheet name; order wins for compound names."""
    name = sheet_name.strip()
    if has_keyword(name, ORDER_SHEET_KEYWORDS):
        return "order"
    if has_keyword(name, CUSTOMER_SHEET_KEYWORDS):
        return "customer"
    if has_keyword(name, PRODUCT_SHEET_KEYWORDS):
        return "product"
    return None


def _display_name(sheet_name: str, kind: str) -> str:
    name = sheet_name.strip()
    if not name or _DEFAULT_SHEET_RE.match(name):
        return ENTITY_KIND_LABELS[kind]
    # "订单表" -> "订单"; keep richer names like "客户信息表" -> "客户信息".
    if len(name) > 1 and name.endswith("表"):
        name = name[:-1]
    return name


def _structure_kind(
    fields: list[InferredField],
) -> tuple[str | None, str | None, list[str]]:
    """Return (strong_kind, weak_kind, signals) from field composition.

    strong_kind is high-reliability structural evidence; weak_kind is softer
    (mid-strength order shape). The caller treats strong evidence from a
    different kind than the sheet name as a conflict.
    """
    identifiers = [field for field in fields if field.role == "identifier"]
    has_id = bool(identifiers)
    has_time = any(field.role == "time" for field in fields)
    has_phone = any(field.type == "phone" for field in fields)
    has_contact = any(
        field.role == "text" and has_keyword(field.column, CONTACT_NAME_KEYWORDS)
        for field in fields
    )
    has_sku_hint = any(
        has_keyword(field.column, PRODUCT_ATTR_KEYWORDS) for field in fields
    )
    money_fields = [field for field in fields if field.type == "money"]
    price_money = any(
        has_keyword(field.column, PRICE_NAME_KEYWORDS) for field in money_fields
    )
    flow_money = any(
        not has_keyword(field.column, PRICE_NAME_KEYWORDS)
        for field in money_fields
    )
    has_quantity = any(
        field.type == "number"
        and field.role == "measure"
        and has_keyword(field.column, QUANTITY_NAME_KEYWORDS)
        for field in fields
    )
    party_name_id = any(
        has_keyword(field.column, PARTY_ID_NAME_KEYWORDS) for field in identifiers
    )

    signals: list[str] = []
    if has_id:
        signals.append("has_identifier")
    if has_time:
        signals.append("has_time_field")
    if flow_money:
        signals.append("has_flow_money")
    if price_money:
        signals.append("has_price_field")
    if has_phone or has_contact:
        signals.append("has_contact_field")
    if has_sku_hint:
        signals.append("has_product_attribute")
    if has_quantity:
        signals.append("has_quantity_field")

    if not has_id:
        return None, None, signals

    # Strong order: the transaction triad (id + flow amount + time).
    if has_time and flow_money:
        return "order", None, signals
    # Strong product: a priced/attribute catalog row without a time axis.
    if not has_time and (price_money or has_sku_hint):
        return "product", None, signals
    # Strong party: an identified directory row with contact information and
    # neither transaction amount nor time axis.
    if not has_time and not flow_money and (has_phone or has_contact):
        return "customer", None, signals
    # Mid order: id plus time axis, or flow amount plus quantity, but not the
    # full triad.
    if has_time or (flow_money and has_quantity):
        signals.append("partial_transaction_shape")
        return None, "order", signals
    # Mid customer: named party rows without contact columns.
    if not has_time and not flow_money and not price_money and party_name_id:
        signals.append("party_name_only")
        return None, "customer", signals
    return None, None, signals


def infer_entity(
    sheet: ParsedSheet, fields: list[InferredField]
) -> InferredEntity | None:
    if sheet.is_empty:
        return None

    identifiers = [field for field in fields if field.role == "identifier"]
    key_field = (
        sorted(
            identifiers,
            key=lambda field: field.role_confidence,
            reverse=True,
        )[0].column
        if identifiers
        else None
    )

    name_kind = _sheet_name_kind(sheet.name)
    strong_kind, weak_kind, structure_signals = _structure_kind(fields)

    signals: list[str]
    if name_kind is not None:
        kind = name_kind
        signals = [f"name_keyword:{name_kind}"]
        if strong_kind is not None and strong_kind != kind:
            # Sheet label says one thing, the full field triad says another.
            confidence = 0.61
            signals.append(f"structure_suggests:{strong_kind}")
            signals.append("name_structure_conflict")
        else:
            confidence = 0.95
            if strong_kind == kind:
                signals.append("structure_confirmed")
            elif weak_kind is not None and weak_kind != kind:
                signals.append(f"structure_hint:{weak_kind}")
        if key_field is None:
            # An entity without a key cannot be assembled on Day 11.
            confidence = min(confidence, 0.75)
            signals.append("no_identifier")
    elif strong_kind is not None:
        kind = strong_kind
        confidence = 0.85
        signals = [f"structure:{strong_kind}_strong"]
    elif weak_kind is not None:
        kind = weak_kind
        confidence = 0.75 if weak_kind == "order" else 0.70
        signals = [f"structure:{weak_kind}_weak"]
    else:
        kind = "unknown"
        confidence = 0.5
        signals = ["no_entity_evidence"]

    signals.extend(
        signal for signal in structure_signals if signal not in signals
    )

    return InferredEntity(
        source_sheet=sheet.name,
        key=kind,  # type: ignore[arg-type]
        name=_display_name(sheet.name, kind),
        key_field=key_field,
        confidence=confidence,
        needs_review=confidence < 0.85,
        signals=signals,
    )
