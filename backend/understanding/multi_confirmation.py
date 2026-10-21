"""Confirm a multi-file import: merge groups, resolve conflicts, persist."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.domain.models import BusinessModel
from backend.logging_config import get_logger
from backend.storage.record_store import get_record_store
from backend.storage.repositories import (
    model_repository,
    understanding_repository,
)
from backend.understanding import confirmation, multi_import
from backend.understanding.link_inference import infer_links
from backend.understanding.model_assembly import assemble_model
from backend.understanding.profiling import _USE_SETTINGS_LLM
from backend.understanding.schemas import ProfiledParsedWorkbook

CONFIRM_OPERATOR = "multi-confirm"

logger = get_logger("multi_import")


class SheetGroup(BaseModel):
    sheets: list[str] = Field(default_factory=list)


class MultiDecisions(confirmation.ConfirmDecisions):
    # Empty means "every sheet"; ids are the stable ``s0``/``s1`` handles the
    # parse response returned.
    sheets: list[str] = Field(default_factory=list)
    # Explicit merge groups. Sheets not mentioned stay as their own entity.
    groups: list[SheetGroup] = Field(default_factory=list)
    # conflict id -> chosen option (skip/overwrite/keep/string/first/merge)
    conflicts: dict[str, str] = Field(default_factory=dict)


def understand_files(
    db: Session,
    files: Sequence[tuple[str, bytes]],
    *,
    llm_client: object | None = _USE_SETTINGS_LLM,
) -> ProfiledParsedWorkbook:
    """Reviewed understanding for these uploads, cache first."""
    key = multi_import.session_key(files)
    cached = understanding_repository.load_understanding(db, key)
    if cached is not None:
        return cached
    return multi_import.understand(files, llm_client=llm_client)


def _selected(
    slots: Sequence[multi_import.SheetSlot], ids: Sequence[str]
) -> tuple[list[multi_import.SheetSlot], list[str]]:
    try:
        return multi_import.select_slots(slots, ids)
    except multi_import.ImportError as exc:
        raise confirmation.ConfirmError(str(exc)) from exc


def groups_for(
    slots: Sequence[multi_import.SheetSlot],
    groups: Sequence[SheetGroup],
) -> list[list[multi_import.SheetSlot]]:
    try:
        return multi_import.group_slots(slots, [group.sheets for group in groups])
    except multi_import.ImportError as exc:
        raise confirmation.ConfirmError(str(exc)) from exc


def _apply_key_strategy(
    rows: list[dict[str, Any]], key_field: str, strategy: str
) -> list[dict[str, Any]]:
    """Resolve duplicate primary keys inside one merged entity."""
    seen: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get(key_field))
        if key not in seen:
            seen[key] = len(kept)
            kept.append(row)
            continue
        if strategy == "overwrite":
            kept[seen[key]] = row
            continue
        if strategy == "keep":
            suffix = 2
            candidate = f"{key}-{suffix}"
            while candidate in seen:
                suffix += 1
                candidate = f"{key}-{suffix}"
            seen[candidate] = len(kept)
            kept.append({**row, key_field: candidate})
        # "skip" (and any unknown value) drops the later duplicate.
    return kept


def confirm_multi(
    db: Session,
    files: Sequence[tuple[str, bytes]],
    raw_decisions: dict[str, Any],
    *,
    llm_client: object | None = _USE_SETTINGS_LLM,
    app_key: str | None = None,
) -> confirmation.ConfirmSummary:
    try:
        decisions = MultiDecisions.model_validate(raw_decisions)
    except Exception as exc:
        raise confirmation.ConfirmError(f"审查决策格式不正确：{exc}") from exc

    target_app_key = confirmation.resolve_app_key(db, app_key)
    understanding = understand_files(db, files, llm_client=llm_client)
    slots = multi_import.slots_of(understanding)
    selected, skipped = _selected(slots, decisions.sheets)
    groups = groups_for(selected, decisions.groups)
    plans, conflicts, notes = multi_import.build_plans(groups, decisions.conflicts)

    links = infer_links(understanding, plans)
    current_keys = {link.key for link in links}
    link_decisions = {
        key: value
        for key, value in decisions.links.items()
        if key in current_keys
    }
    missing = confirmation.missing_link_decisions(links, link_decisions)
    if missing:
        raise confirmation.ConfirmError(
            f"还有 {len(missing)} 个关联未确认，全部处理后才能生成系统",
            unresolved=missing,
        )
    kept_links, accepted, rejected = confirmation.apply_link_decisions(
        links, link_decisions
    )
    try:
        model = assemble_model(understanding, plans, kept_links)
    except Exception as exc:
        raise confirmation.ConfirmError(f"按审查决策重组模型失败：{exc}") from exc
    assert model is not None

    unresolved = confirmation.unresolved_review_items(plans, model, decisions)
    if unresolved:
        raise confirmation.ConfirmError(
            f"还有 {len(unresolved)} 项审查未处理，全部确认后才能生成系统",
            unresolved=unresolved,
        )

    records = confirmation.extract_records(plans, model)
    _resolve_duplicate_keys(records, model, conflicts, decisions.conflicts)
    record_counts = {entity_key: len(rows) for entity_key, rows in records.items()}

    try:
        record = model_repository.upsert_model(
            db,
            target_app_key,
            model,
            operator=CONFIRM_OPERATOR,
            source_request=", ".join(name for name, _content in files),
            commit=False,
        )
        entities = {entity.key: entity for entity in model.entities}
        rows = [
            (entity_key, str(row[entities[entity_key].key_field]), row)
            for entity_key, entity_rows in records.items()
            for row in entity_rows
        ]
        get_record_store(db).replace_app(
            target_app_key, rows, record.version, commit=False
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    summary = confirmation.ConfirmSummary(
        app_key=target_app_key,
        version=record.version,
        app_name=model.app.name,
        links_accepted=accepted,
        links_rejected=rejected,
        record_counts=record_counts,
    )
    logger.info(
        "多文件确认完成：文件 %d、实体 %d、跳过工作表 %s、合并说明 %s",
        len(files),
        len(model.entities),
        skipped,
        notes,
    )
    return summary


def _resolve_duplicate_keys(
    records: dict[str, list[dict[str, Any]]],
    model: BusinessModel,
    conflicts: Sequence[multi_import.ImportConflict],
    choices: dict[str, str],
) -> None:
    defaults = {
        conflict.id: conflict.default
        for conflict in conflicts
        if conflict.kind == "primary_key_duplicate"
    }
    for entity in model.entities:
        rows = records.get(entity.key)
        if not rows or len(rows) < 2:
            continue
        conflict_id = f"pk:{entity.key}"
        strategy = choices.get(
            conflict_id, defaults.get(conflict_id, "skip")
        )
        records[entity.key] = _apply_key_strategy(
            rows, entity.key_field, strategy
        )


__all__ = [
    "MultiDecisions",
    "SheetGroup",
    "confirm_multi",
    "groups_for",
    "understand_files",
]
