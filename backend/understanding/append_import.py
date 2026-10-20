"""Append Excel/CSV rows to an entity of an already generated system.

The flow mirrors workbook Confirm: a preview is cached server-side under a
random batch id (the client only sends that id plus the duplicate-key
strategy, never row data), confirming writes model changes and rows in one
transaction, and every applied batch keeps enough information to be rolled
back.

Column matching is deterministic: header names are normalized (NFKC, case
folded, punctuation/whitespace removed) and matched against existing field
names first, then field keys. Unmatched columns become new fields whose type
and role come from the same deterministic inference used at import time; any
new field advances the model to a new version through the regular JSON Patch
validation and snapshot path.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.domain.enums import FieldRole, FieldType
from backend.domain.models import BusinessField, BusinessModel, Entity
from backend.ingestion.parser import ParseError, parse_workbook
from backend.ingestion.schemas import ParsedSheet
from backend.logging_config import get_logger
from backend.storage.record_store import get_record_store
from backend.storage.repositories import append_repository, model_repository
from backend.understanding.confirmation import (
    WORKSPACE_APP_KEY,
    ConfirmError,
    _coerce_cell,
)
from backend.understanding.naming import keys_for_columns
from backend.understanding.profiling import profile_sheet
from backend.understanding.role_inference import annotate_roles
from backend.understanding.type_inference import infer_sheet

APPEND_OPERATOR = "append"

Strategy = Literal["skip", "overwrite", "keep"]

STRATEGIES: tuple[dict[str, str], ...] = (
    {
        "value": "skip",
        "label": "跳过重复",
        "description": "主键已存在的行不导入，只追加新行",
    },
    {
        "value": "overwrite",
        "label": "覆盖旧数据",
        "description": "主键已存在的行用新内容覆盖，可整体回滚",
    },
    {
        "value": "keep",
        "label": "保留为两条",
        "description": "重复主键自动加序号后缀，两条数据都保留",
    },
)

logger = get_logger("append_import")


class AppendError(Exception):
    """A user-facing append failure (status code included)."""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


class AppendFieldRef(BaseModel):
    column: str
    key: str
    name: str
    type: str
    is_key: bool = False
    is_new: bool = False


class AppendPreviewResult(BaseModel):
    batch_id: str
    app_key: str
    entity_key: str
    entity_name: str
    base_version: int
    file_name: str
    sheet_name: str
    available_sheets: list[str]
    total_rows: int
    valid_rows: int
    new_rows: int
    duplicate_rows: int
    invalid_rows: int
    matched_fields: list[AppendFieldRef]
    new_fields: list[AppendFieldRef]
    duplicate_sample: list[str]
    invalid_messages: list[str]
    warnings: list[str]
    strategies: list[dict[str, str]]


class AppendResult(BaseModel):
    batch_id: str
    app_key: str
    entity_key: str
    status: str
    version: int
    inserted: int
    overwritten: int
    skipped: int
    duplicate_keys: list[str]


class AppendBatchMeta(BaseModel):
    id: str
    app_key: str
    entity_key: str
    entity_name: str
    file_name: str
    sheet_name: str
    base_version: int
    new_version: int | None
    strategy: str | None
    status: str
    inserted: int
    overwritten: int
    created_at: datetime


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).strip().casefold()
    return re.sub(r"[\W_]+", "", folded, flags=re.UNICODE)


def _field_lookup(entity: Entity) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for field in entity.fields:
        lookup.setdefault(_norm(field.name), field.key)
    for field in entity.fields:
        lookup.setdefault(_norm(field.key), field.key)
    return lookup


def _map_columns(
    entity: Entity, columns: Sequence[str]
) -> tuple[list[str | None], list[str]]:
    """Map header positions onto existing field keys (None = new column)."""
    lookup = _field_lookup(entity)
    mapping: list[str | None] = []
    warnings: list[str] = []
    used: set[str] = set()
    for column in columns:
        key = lookup.get(_norm(column))
        if key is None:
            mapping.append(None)
        elif key in used:
            warnings.append(f"列「{column}」与前面某列对应同一字段，已忽略该列")
            mapping.append(None)
        else:
            mapping.append(key)
            used.add(key)
    return mapping, warnings


def _sheet_score(entity: Entity, sheet: ParsedSheet) -> int:
    lookup = _field_lookup(entity)
    used: set[str] = set()
    for column in sheet.columns:
        key = lookup.get(_norm(column))
        if key is not None:
            used.add(key)
    return len(used) + (2 if entity.key_field in used else 0)


def _select_sheet(
    sheets: Sequence[ParsedSheet], entity: Entity, sheet_name: str | None
) -> tuple[ParsedSheet, list[str]]:
    candidates = [sheet for sheet in sheets if not sheet.is_empty and sheet.rows]
    if not candidates:
        raise AppendError("文件里没有可导入的数据行")
    names = [sheet.name for sheet in candidates]
    if sheet_name:
        selected = next(
            (sheet for sheet in candidates if sheet.name == sheet_name), None
        )
        if selected is None:
            raise AppendError(
                f"文件里没有名为「{sheet_name}」的非空工作表，可选：{'、'.join(names)}"
            )
        return selected, names
    # Pick the sheet that shares the most fields with the entity (key column
    # counts double), so a multi-sheet workbook needs no manual selection.
    best = max(candidates, key=lambda sheet: _sheet_score(entity, sheet))
    return best, names


def _build_new_fields(
    entity: Entity, sheet: ParsedSheet, mapping: list[str | None]
) -> tuple[list[BusinessField], list[str]]:
    """Infer the unmatched columns and fill their keys into ``mapping``."""
    positions = [index for index, key in enumerate(mapping) if key is None]
    unique_positions: list[int] = []
    seen: set[str] = set()
    warnings: list[str] = []
    for position in positions:
        normalized = _norm(sheet.columns[position])
        if normalized in seen:
            warnings.append(f"列「{sheet.columns[position]}」名称重复，已忽略该列")
            continue
        seen.add(normalized)
        unique_positions.append(position)
    if not unique_positions:
        return [], warnings

    columns = [sheet.columns[position] for position in unique_positions]
    rows = [
        [row[position] if position < len(row) else None for position in unique_positions]
        for row in sheet.rows
    ]
    parsed = ParsedSheet(name=sheet.name, columns=columns, rows=rows)
    profile = profile_sheet(parsed)
    inferred = infer_sheet(parsed, profile)
    annotate_roles(inferred, profile)

    proposals: dict[str, str | None] = {
        field.column: field.key for field in inferred if field.key
    }
    base_keys, _ = keys_for_columns(columns, proposals)
    taken = set(entity.field_keys())
    fields: list[BusinessField] = []
    for offset, column in enumerate(columns):
        key = base_keys[offset]
        if key in taken:
            suffix = 2
            while f"{key}_{suffix}" in taken:
                suffix += 1
            key = f"{key}_{suffix}"
        taken.add(key)

        proposal = inferred[offset]
        ftype = proposal.type
        values = list(proposal.enum_values) if ftype == "enum" else None
        if ftype == "enum" and not values:
            ftype = "string"
            values = None
        # Appending data must never introduce a second identity: a column that
        # happens to be unique in this batch becomes a dimension, not a key.
        role = "dimension" if proposal.role == "identifier" else proposal.role
        field = BusinessField(
            key=key,
            name=column,
            type=FieldType(ftype),
            role=FieldRole(role),
            values=values,
            confidence=proposal.confidence,
            review_reason="追加数据时新增的字段",
        )
        fields.append(field)
        mapping[unique_positions[offset]] = key
    return fields, warnings


def _coerce_rows(
    sheet: ParsedSheet,
    mapping: Sequence[str | None],
    fields: dict[str, BusinessField],
    key_field: BusinessField,
) -> tuple[list[dict[str, Any]], list[str], int]:
    """Convert every data row into typed values; invalid rows are reported."""
    rows: list[dict[str, Any]] = []
    messages: list[str] = []
    invalid = 0
    for row_index, row in enumerate(sheet.rows):
        if all(cell is None or str(cell).strip() == "" for cell in row):
            continue
        out: dict[str, Any] = {}
        try:
            for position, field_key in enumerate(mapping):
                if field_key is None:
                    continue
                cell = row[position] if position < len(row) else None
                where = (
                    f"第 {row_index + 2} 行「{sheet.columns[position]}」"
                )
                out[field_key] = _coerce_cell(fields[field_key], cell, where)
        except ConfirmError as exc:
            invalid += 1
            if len(messages) < 5:
                messages.append(str(exc))
            continue
        key_value = out.get(key_field.key)
        if key_value is None or str(key_value).strip() == "":
            invalid += 1
            if len(messages) < 5:
                messages.append(
                    f"第 {row_index + 2} 行：主键字段「{key_field.name}」为空"
                )
            continue
        out[key_field.key] = key_value
        rows.append(out)
    if invalid > len(messages):
        messages.append(f"另有 {invalid - len(messages)} 行存在同类问题，未逐条列出")
    return rows, messages, invalid


def _load_model(
    db: Session, app_key: str
) -> tuple[Any, BusinessModel]:
    current = model_repository.get_current(db, app_key, raise_if_missing=False)
    if current is None:
        raise AppendError(
            f"系统 {app_key} 还不存在，请先生成系统", status_code=404
        )
    return current, BusinessModel.model_validate(current.snapshot)


def _find_entity(model: BusinessModel, entity_key: str) -> Entity:
    entity = next((item for item in model.entities if item.key == entity_key), None)
    if entity is None:
        raise AppendError(f"系统里没有实体「{entity_key}」", status_code=404)
    return entity


def _fields_for(
    entity: Entity, new_fields: Sequence[dict[str, Any]]
) -> dict[str, BusinessField]:
    fields = {field.key: field for field in entity.fields}
    for raw in new_fields:
        field = BusinessField.model_validate(raw)
        fields[field.key] = field
    return fields


def preview_append(
    db: Session,
    *,
    app_key: str,
    entity_key: str,
    filename: str,
    content: bytes,
    sheet_name: str | None = None,
) -> AppendPreviewResult:
    current, model = _load_model(db, app_key)
    entity = _find_entity(model, entity_key)
    try:
        workbook = parse_workbook(filename, content)
    except ParseError as exc:
        raise AppendError(str(exc)) from exc

    sheet, available = _select_sheet(workbook.sheets, entity, sheet_name)
    mapping, warnings = _map_columns(entity, sheet.columns)
    if entity.key_field not in mapping:
        key_field = next(
            field for field in entity.fields if field.key == entity.key_field
        )
        raise AppendError(
            f"文件里找不到主键列「{key_field.name}」。"
            f"追加已有实体时必须带上这一列，才能和已有数据对齐。"
        )

    new_fields, field_warnings = _build_new_fields(entity, sheet, mapping)
    warnings.extend(field_warnings)
    fields = {field.key: field for field in entity.fields}
    fields.update({field.key: field for field in new_fields})

    # Keep only columns that participate; unused duplicate headers would only
    # bloat the cached payload.
    used_positions = [index for index, key in enumerate(mapping) if key is not None]
    used_columns = [sheet.columns[position] for position in used_positions]
    used_rows = [
        [row[position] if position < len(row) else None for position in used_positions]
        for row in sheet.rows
    ]
    used_mapping = [mapping[position] for position in used_positions]
    projected = ParsedSheet(name=sheet.name, columns=used_columns, rows=used_rows)

    key_field = fields[entity.key_field]
    rows, invalid_messages, invalid_count = _coerce_rows(
        projected, used_mapping, fields, key_field
    )
    if not rows:
        detail = invalid_messages[0] if invalid_messages else "文件里没有可导入的数据行"
        raise AppendError(f"没有可导入的有效数据行：{detail}")

    store = get_record_store(db)
    existing = store.row_keys(app_key, entity_key)
    seen: set[str] = set()
    duplicates: list[str] = []
    for row in rows:
        key = str(row[entity.key_field])
        if key in existing or key in seen:
            duplicates.append(key)
        seen.add(key)

    payload = {
        "file_name": filename,
        "sheet_name": sheet.name,
        "columns": used_columns,
        "rows": used_rows,
        "mapping": used_mapping,
        "new_fields": [field.model_dump(mode="json") for field in new_fields],
        "warnings": warnings,
    }
    batch = append_repository.create(
        db,
        app_key=app_key,
        entity_key=entity_key,
        base_version=current.version,
        payload=payload,
    )

    matched_fields = [
        AppendFieldRef(
            column=column,
            key=key,
            name=fields[key].name,
            type=str(fields[key].type),
            is_key=key == entity.key_field,
        )
        for column, key in zip(used_columns, used_mapping, strict=True)
        if key is not None and key not in {field.key for field in new_fields}
    ]
    new_refs = [
        AppendFieldRef(
            column=field.name,
            key=field.key,
            name=field.name,
            type=str(field.type),
            is_new=True,
        )
        for field in new_fields
    ]
    logger.info(
        "追加数据预览（app=%s 实体=%s 文件=%s 行=%d 重复=%d 新字段=%d）",
        app_key,
        entity_key,
        filename,
        len(rows),
        len(duplicates),
        len(new_fields),
    )
    return AppendPreviewResult(
        batch_id=batch.id,
        app_key=app_key,
        entity_key=entity_key,
        entity_name=entity.name,
        base_version=current.version,
        file_name=filename,
        sheet_name=sheet.name,
        available_sheets=available,
        total_rows=len(rows) + invalid_count,
        valid_rows=len(rows),
        new_rows=len(rows) - len(duplicates),
        duplicate_rows=len(duplicates),
        invalid_rows=invalid_count,
        matched_fields=matched_fields,
        new_fields=new_refs,
        duplicate_sample=duplicates[:10],
        invalid_messages=invalid_messages,
        warnings=warnings,
        strategies=[dict(option) for option in STRATEGIES],
    )


def _suffixed_key(base: str, taken: set[str]) -> str:
    """Same suffix convention as multi-file import's "keep" strategy."""
    suffix = 2
    candidate = f"{base}-{suffix}"
    while candidate in taken:
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def _field_patch(
    snapshot: dict[str, Any], entity_key: str, new_fields: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    entities = snapshot.get("entities") or []
    entity_index = next(
        (
            index
            for index, item in enumerate(entities)
            if isinstance(item, dict) and item.get("key") == entity_key
        ),
        None,
    )
    if entity_index is None:
        raise AppendError("系统结构已变化，找不到目标实体", status_code=409)

    ops: list[dict[str, Any]] = [
        {
            "op": "add",
            "path": f"/entities/{entity_index}/fields/-",
            "value": field,
        }
        for field in new_fields
    ]
    for view_index, view in enumerate(snapshot.get("views") or []):
        if (
            isinstance(view, dict)
            and view.get("kind") == "list"
            and view.get("entity") == entity_key
        ):
            ops.extend(
                {
                    "op": "add",
                    "path": f"/views/{view_index}/columns/-",
                    "value": field["key"],
                }
                for field in new_fields
            )
    return ops


def confirm_append(
    db: Session, batch_id: str, strategy: str
) -> AppendResult:
    if strategy not in ("skip", "overwrite", "keep"):
        raise AppendError(f"未知的重复处理方式：{strategy}")
    batch = append_repository.get(db, batch_id)
    if batch is None:
        raise AppendError("追加批次不存在或已过期，请重新上传预览", status_code=404)
    if batch.status != "pending":
        raise AppendError("该追加批次已处理，请重新上传预览", status_code=409)

    current, _model = _load_model(db, batch.app_key)
    if current.version != batch.base_version:
        raise model_repository.VersionConflictError(
            model_repository.VERSION_CONFLICT_MESSAGE
        )
    entity = _find_entity(
        BusinessModel.model_validate(current.snapshot), batch.entity_key
    )

    payload = batch.payload
    new_fields = payload.get("new_fields") or []
    fields = _fields_for(entity, new_fields)
    sheet = ParsedSheet(
        name=str(payload["sheet_name"]),
        columns=list(payload["columns"]),
        rows=list(payload["rows"]),
    )
    key_field = fields[entity.key_field]
    rows, _messages, _invalid = _coerce_rows(
        sheet, list(payload["mapping"]), fields, key_field
    )
    if not rows:
        raise AppendError("没有可追加的有效数据行，请重新上传")

    store = get_record_store(db)
    existing = store.row_keys(batch.app_key, batch.entity_key)
    taken = set(existing)
    planned: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    overwrites: list[str] = []
    skipped = 0
    duplicates: list[str] = []
    for row in rows:
        key = str(row[entity.key_field])
        if key in taken:
            duplicates.append(key)
            if strategy == "skip":
                skipped += 1
                continue
            if strategy == "overwrite":
                if key in positions:
                    planned[positions[key]] = row
                else:
                    positions[key] = len(planned)
                    planned.append(row)
                    overwrites.append(key)
                continue
            # keep: the incoming row becomes its own identity.
            new_key = _suffixed_key(key, taken)
            taken.add(new_key)
            positions[new_key] = len(planned)
            planned.append({**row, entity.key_field: new_key})
            continue
        taken.add(key)
        positions[key] = len(planned)
        planned.append(row)

    backups = {
        key: store.get(batch.app_key, batch.entity_key, key) for key in overwrites
    }

    # Claim the batch atomically so two concurrent confirms cannot both apply.
    if not append_repository.transition(
        db, batch.id, expected="pending", new_status="applying"
    ):
        raise AppendError("该追加批次已处理，请重新上传预览", status_code=409)

    new_version = current.version
    try:
        if new_fields:
            ops = _field_patch(current.snapshot, batch.entity_key, new_fields)
            patched = model_repository.apply_patch_as_new_version(
                db,
                batch.app_key,
                ops,
                source_request=f"追加数据：{payload.get('file_name') or ''}",
                operator=APPEND_OPERATOR,
                enforce_business_safety=True,
                commit=False,
            )
            new_version = patched.version
        else:
            # Still flush the row writes below through the same transaction.
            new_version = current.version

        overwrite_set = set(overwrites)
        inserted_keys: list[str] = []
        for row in planned:
            key = str(row[entity.key_field])
            if key in overwrite_set:
                store.update_row(
                    batch.app_key,
                    batch.entity_key,
                    key,
                    row,
                    new_version,
                    commit=False,
                )
            else:
                store.insert_row(
                    batch.app_key,
                    batch.entity_key,
                    key,
                    row,
                    new_version,
                    commit=False,
                )
                inserted_keys.append(key)

        result = {
            "inserted": inserted_keys,
            "overwritten": backups,
            "skipped": skipped,
            "duplicate_keys": sorted(set(duplicates)),
            "base_version": batch.base_version,
        }
        append_repository.mark_applied(
            db,
            batch,
            strategy=strategy,
            new_version=new_version,
            result=result,
        )
        db.commit()
    except Exception:
        db.rollback()
        append_repository.transition(
            db, batch.id, expected="applying", new_status="pending"
        )
        raise

    logger.info(
        "追加数据完成（app=%s 实体=%s 版本=%d 新增=%d 覆盖=%d 跳过=%d 策略=%s）",
        batch.app_key,
        batch.entity_key,
        new_version,
        len(inserted_keys),
        len(overwrites),
        skipped,
        strategy,
    )
    return AppendResult(
        batch_id=batch.id,
        app_key=batch.app_key,
        entity_key=batch.entity_key,
        status="applied",
        version=new_version,
        inserted=len(inserted_keys),
        overwritten=len(overwrites),
        skipped=skipped,
        duplicate_keys=sorted(set(duplicates))[:20],
    )


def rollback_append(db: Session, batch_id: str) -> AppendResult:
    batch = append_repository.get(db, batch_id)
    if batch is None:
        raise AppendError("追加批次不存在或已过期", status_code=404)
    if batch.status != "applied":
        raise AppendError("只有已应用的追加批次可以回滚", status_code=409)

    applied = [
        item
        for item in append_repository.list_for_app(db, batch.app_key)
        if item.status == "applied"
    ]
    if applied and applied[0].id != batch.id:
        raise AppendError(
            "只能直接回滚最近一次追加；更早的批次请到「历史版本」处理",
            status_code=409,
        )

    current, _model = _load_model(db, batch.app_key)
    if current.version != batch.new_version:
        raise AppendError(
            "该批次之后系统已有新的修改，无法直接回滚；"
            "请到「历史版本」回滚到追加之前的版本",
            status_code=409,
        )

    if not append_repository.transition(
        db, batch.id, expected="applied", new_status="rolling_back"
    ):
        raise AppendError("该追加批次已处理", status_code=409)

    store = get_record_store(db)
    result = batch.result or {}
    inserted = list(result.get("inserted") or [])
    overwritten = result.get("overwritten") or {}
    try:
        for key in inserted:
            store.delete_row(batch.app_key, batch.entity_key, key, commit=False)
        restored = 0
        for key, data in overwritten.items():
            if data is None:
                continue
            store.update_row(
                batch.app_key,
                batch.entity_key,
                key,
                data,
                batch.base_version,
                commit=False,
            )
            restored += 1
        version = batch.base_version
        if batch.new_version != batch.base_version:
            record = model_repository.restore_version(
                db,
                batch.app_key,
                batch.base_version,
                operator="append-rollback",
                commit=False,
            )
            version = record.version
        append_repository.mark_rolled_back(db, batch)
        db.commit()
    except Exception:
        db.rollback()
        append_repository.transition(
            db, batch.id, expected="rolling_back", new_status="applied"
        )
        raise

    logger.info(
        "追加数据回滚（app=%s 实体=%s 批次=%s 删除=%d 恢复=%d 版本=%d）",
        batch.app_key,
        batch.entity_key,
        batch.id,
        len(inserted),
        restored,
        version,
    )
    return AppendResult(
        batch_id=batch.id,
        app_key=batch.app_key,
        entity_key=batch.entity_key,
        status="rolled_back",
        version=version,
        inserted=0,
        overwritten=restored,
        skipped=0,
        duplicate_keys=[],
    )


def list_batches(
    db: Session, app_key: str, *, limit: int = 20
) -> list[AppendBatchMeta]:
    current = model_repository.get_current(db, app_key, raise_if_missing=False)
    model = (
        BusinessModel.model_validate(current.snapshot) if current is not None else None
    )
    names = (
        {entity.key: entity.name for entity in model.entities} if model else {}
    )
    metas: list[AppendBatchMeta] = []
    for batch in append_repository.list_for_app(db, app_key, limit=limit):
        payload = batch.payload or {}
        result = batch.result or {}
        metas.append(
            AppendBatchMeta(
                id=batch.id,
                app_key=batch.app_key,
                entity_key=batch.entity_key,
                entity_name=names.get(batch.entity_key, batch.entity_key),
                file_name=str(payload.get("file_name") or ""),
                sheet_name=str(payload.get("sheet_name") or ""),
                base_version=batch.base_version,
                new_version=batch.new_version,
                strategy=batch.strategy,
                status=batch.status,
                inserted=len(result.get("inserted") or []),
                overwritten=len(result.get("overwritten") or {}),
                created_at=batch.created_at,
            )
        )
    return metas


__all__ = [
    "APPEND_OPERATOR",
    "AppendBatchMeta",
    "AppendError",
    "AppendPreviewResult",
    "AppendResult",
    "STRATEGIES",
    "WORKSPACE_APP_KEY",
    "confirm_append",
    "list_batches",
    "preview_append",
    "rollback_append",
]
