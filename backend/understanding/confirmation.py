"""Day 15 Confirm: turn reviewed understanding output into a running app.

The SSE parse endpoint is stateless; Confirm is the first write. It:

1. reuses the understanding result cached server-side at parse time (keyed by
   file hash) and only re-runs the pipeline on a cache miss — it never trusts
   client-supplied inference;
2. applies the human's link decisions (accept/reject + on-field overrides)
   and re-assembles the Business Model, so views/metrics are rebuilt around
   the corrected links and full pydantic semantics run again;
3. refuses while any needs_review entity/field or inferred link is still
   unresolved (design 10.1: medium/low confidence must be acknowledged by a
   human before it becomes a system);
4. persists the model as a new version of the workspace app and extracts +
   validates the workbook's own rows, which the runtime bootstrap then
   serves — the generated app runs on the user's data, not the demo seed.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.domain.models import BusinessField, BusinessModel, FieldType
from backend.ingestion.parser import ParseError, parse_workbook
from backend.ingestion.schemas import Cell
from backend.runtime.seed import validate_records
from backend.storage.repositories import (
    app_data_repository,
    model_repository,
    understanding_repository,
)
from backend.understanding.model_assembly import EntityPlan, assemble_model, build_entity_plans
from backend.understanding.profiling import add_profiles
from backend.understanding.schemas import InferredLink, ProfiledParsedWorkbook
from backend.understanding.type_inference import _DATE_FORMATS

# V0.1 keeps a single user-generated app ("我的工作区"); re-confirming a new
# workbook produces a new version of this app.
WORKSPACE_APP_KEY = "workspace"
CONFIRM_OPERATOR = "confirm"

_CURRENCY_CHARS_RE = re.compile(r"[¥￥$€￠£,\s]|元|块")


class ConfirmError(Exception):
    """A 422-class failure with optional machine-readable unresolved items."""

    def __init__(self, message: str, *, unresolved: list[str] | None = None) -> None:
        super().__init__(message)
        self.unresolved = unresolved or []


class LinkDecisionPayload(BaseModel):
    decision: Literal["accepted", "rejected"]
    # None means "keep the inferred on-field"; any override is a field key on
    # the corresponding entity, checked again by BusinessModel validation.
    on_from: str | None = None
    on_to: str | None = None


class ConfirmDecisions(BaseModel):
    # Acknowledged review items: "entity_key" / "entity_key.field_key".
    acknowledged: list[str] = Field(default_factory=list)
    # link key -> human decision; every inferred link must be present.
    links: dict[str, LinkDecisionPayload] = Field(default_factory=dict)


class ConfirmSummary(BaseModel):
    app_key: str
    version: int
    app_name: str
    links_accepted: list[str]
    links_rejected: list[str]
    record_counts: dict[str, int]


def missing_link_decisions(
    links: list[InferredLink], decisions: dict[str, LinkDecisionPayload]
) -> list[str]:
    """All inferred links the human has not accepted or rejected yet."""
    return [
        f"relation:{link.key}" for link in links if link.key not in decisions
    ]


def apply_link_decisions(
    links: list[InferredLink], decisions: dict[str, LinkDecisionPayload]
) -> tuple[list[InferredLink], list[str], list[str]]:
    """Filter/correct inferred links per the human's decisions.

    Returns (kept links with on overrides applied, accepted keys, rejected
    keys). Callers must clear `missing_link_decisions` first; payloads
    referencing an unknown link are still rejected here.
    """
    kept: list[InferredLink] = []
    accepted: list[str] = []
    rejected: list[str] = []
    for link in links:
        decision = decisions[link.key]
        if decision.decision == "rejected":
            rejected.append(link.key)
            continue
        kept.append(
            link.model_copy(
                update={
                    "on_from": decision.on_from or link.on_from,
                    "on_to": decision.on_to or link.on_to,
                }
            )
        )
        accepted.append(link.key)

    extra = sorted(set(decisions) - {link.key for link in links})
    if extra:
        raise ConfirmError(f"提交了不存在的关联决策：{', '.join(extra)}")
    return kept, accepted, rejected


def unresolved_review_items(
    plans: list[EntityPlan],
    model: BusinessModel,
    decisions: ConfirmDecisions,
) -> list[str]:
    """Needs-review entities/fields still blocking Confirm (design 10.1)."""
    acknowledged = set(decisions.acknowledged)
    unresolved: list[str] = []

    entity_review = {
        plan.key: bool(plan.sheet.inferred_entity and plan.sheet.inferred_entity.needs_review)
        for plan in plans
    }
    for entity in model.entities:
        if entity_review.get(entity.key) and entity.key not in acknowledged:
            unresolved.append(f"entity:{entity.key}")
        for field in entity.fields:
            if field.needs_review and f"{entity.key}.{field.key}" not in acknowledged:
                unresolved.append(f"field:{entity.key}.{field.key}")
    return unresolved


def _normalize_date(text: str, where: str) -> str:
    candidate = text.strip()
    # Openpyxl hands datetimes back as "2026-01-03T00:00:00".
    iso_part = candidate[:10] if "T" in candidate or " " in candidate else candidate
    try:
        return dt.date.fromisoformat(iso_part).isoformat()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(candidate, fmt).date().isoformat()
        except ValueError:
            continue
    raise ConfirmError(f"{where}：日期 {text!r} 无法识别，请使用类似 2026-01-03 的格式")


def _coerce_cell(field: BusinessField, cell: Cell, where: str) -> Any:
    """Convert an ingested text cell into the typed value records validation expects."""
    if cell is None:
        return None
    text = cell.strip()
    if text == "":
        return None

    ftype = field.type
    if ftype in (FieldType.STRING, FieldType.PHONE, FieldType.ENUM):
        return text
    if ftype in (FieldType.NUMBER, FieldType.MONEY):
        cleaned = _CURRENCY_CHARS_RE.sub("", text)
        try:
            number = float(cleaned)
        except ValueError as exc:
            raise ConfirmError(
                f"{where}：值 {text!r} 无法识别为数字"
            ) from exc
        return int(number) if number.is_integer() else number
    if ftype is FieldType.DATE:
        return _normalize_date(text, where)
    raise ConfirmError(f"{where}：暂不支持的字段类型 {ftype}")


def extract_records(
    plans: list[EntityPlan], model: BusinessModel
) -> dict[str, list[dict[str, Any]]]:
    """Map every assembled sheet's own rows onto the model's entity fields.

    Original column names go through the same EntityPlan field_keys map used
    at assembly time, so rows line up with the namespaced model even when
    several sheets share a column name. Full seed-style validation (key
    presence, six field types, enum membership) runs afterwards.
    """
    entities = {entity.key: entity for entity in model.entities}
    raw: dict[str, list[dict[str, Any]]] = {}
    for plan in plans:
        entity = entities[plan.key]
        field_map = {field.key: field for field in entity.fields}
        sheet = plan.sheet
        rows: list[dict[str, Any]] = []
        for row_index, row in enumerate(sheet.rows):
            if all(cell is None or cell.strip() == "" for cell in row):
                continue  # parser-aligned blank trailing row
            out: dict[str, Any] = {}
            for position, column in enumerate(sheet.columns):
                field_key = plan.field_keys[column]
                cell = row[position] if position < len(row) else None
                where = f"工作表「{sheet.name}」第 {row_index + 2} 行字段「{column}」"
                out[field_key] = _coerce_cell(field_map[field_key], cell, where)
            rows.append(out)
        raw[plan.key] = rows
    try:
        return validate_records(model, raw)
    except ValueError as exc:
        # Key presence / type / enum problems with the workbook's own rows:
        # a reviewable 422 with the offending location, never a raw 500.
        raise ConfirmError(f"表格数据未通过模型校验：{exc}") from exc


def confirm_workbook(
    db: Session,
    filename: str,
    content: bytes,
    raw_decisions: dict[str, Any],
) -> ConfirmSummary:
    """Run the full Confirm pipeline and persist model + rows."""
    try:
        decisions = ConfirmDecisions.model_validate(raw_decisions)
    except Exception as exc:  # malformed decisions payload -> 422
        raise ConfirmError(f"审查决策格式不正确：{exc}") from exc

    try:
        workbook = parse_workbook(filename, content)
    except ParseError as exc:
        raise ConfirmError(str(exc)) from exc

    # Anti-forgery core: consume the exact understanding result the user
    # reviewed (stored server-side by file hash). The client never sends
    # model/rows, only decisions, so it cannot smuggle structure into the app,
    # and a second non-deterministic LLM call cannot disagree with the review.
    # Cache miss (e.g. server restarted between parse and confirm) falls back
    # to re-running the pipeline; with the rules engine this is identical.
    result: ProfiledParsedWorkbook | None = understanding_repository.load_understanding(
        db, understanding_repository.session_key(content)
    )
    if result is None:
        result = add_profiles(workbook)
    plans, _notes = build_entity_plans(result)
    if not plans:
        raise ConfirmError("文件中没有可生成系统的业务实体（缺少可识别的工作表或标识字段）")

    missing = missing_link_decisions(result.inferred_links, decisions.links)
    if missing:
        raise ConfirmError(
            f"还有 {len(missing)} 个关联未确认，全部处理后才能生成系统",
            unresolved=missing,
        )
    kept_links, accepted, rejected = apply_link_decisions(
        result.inferred_links, decisions.links
    )
    try:
        model = assemble_model(result, plans, kept_links)
    except Exception as exc:  # BusinessModel semantic validation
        raise ConfirmError(f"按审查决策重组模型失败：{exc}") from exc
    # assemble_model only returns None for empty plans, already handled above.
    assert model is not None

    # Field/entity review state is evaluated on the re-assembled model:
    # rejecting a link removes that child column's forced FK review flag.
    unresolved = unresolved_review_items(plans, model, decisions)
    if unresolved:
        raise ConfirmError(
            f"还有 {len(unresolved)} 项审查未处理，全部确认后才能生成系统",
            unresolved=unresolved,
        )

    records = extract_records(plans, model)
    record_counts = {entity_key: len(rows) for entity_key, rows in records.items()}

    record = model_repository.upsert_model(
        db,
        WORKSPACE_APP_KEY,
        model,
        operator=CONFIRM_OPERATOR,
        source_request=filename,
    )
    app_data_repository.upsert_records(db, WORKSPACE_APP_KEY, records)

    return ConfirmSummary(
        app_key=WORKSPACE_APP_KEY,
        version=record.version,
        app_name=model.app.name,
        links_accepted=accepted,
        links_rejected=rejected,
        record_counts=record_counts,
    )


__all__ = [
    "CONFIRM_OPERATOR",
    "WORKSPACE_APP_KEY",
    "ConfirmDecisions",
    "ConfirmError",
    "ConfirmSummary",
    "LinkDecisionPayload",
    "apply_link_decisions",
    "confirm_workbook",
    "extract_records",
    "missing_link_decisions",
    "unresolved_review_items",
]
