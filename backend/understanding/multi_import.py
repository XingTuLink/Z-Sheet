"""Multi-file / multi-sheet import.

Several uploaded workbooks are parsed and understood as one run so links can
span files. Two things happen on top of the single-workbook pipeline:

* sheets that describe the same entity are merged into one virtual sheet with
  columns aligned by name (NFKC + whitespace/case insensitive);
* conflicts that only appear when merging are surfaced with a Chinese
  explanation and a set of choices the user must pick from.

The virtual sheet keeps the existing EntityPlan/assembly pipeline untouched:
downstream code still sees one ProfiledParsedSheet per entity.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from backend.domain.serialization import model_to_dict, model_to_yaml
from backend.ingestion.parser import ParseError, parse_workbook
from backend.ingestion.schemas import ParsedSheet, ParsedWorkbook
from backend.storage.repositories.understanding_repository import SESSION_SCHEMA_VERSION
from backend.understanding.link_inference import infer_links
from backend.understanding.model_assembly import EntityPlan, assemble_model
from backend.understanding.naming import (
    keys_for_columns,
    keys_for_sheet,
    unique_entity_keys,
)
from backend.understanding.profiling import _USE_SETTINGS_LLM, add_profiles, profile_sheet
from backend.understanding.schemas import (
    InferredEntity,
    InferredField,
    InferredLink,
    ProfiledParsedSheet,
    ProfiledParsedWorkbook,
)

MAX_FILES = 10
MAX_ENUM_VALUES = 20

_CONFLICT_PK = "primary_key_duplicate"
_CONFLICT_TYPE = "field_type_mismatch"
_CONFLICT_ENUM = "enum_values_expanded"

_NUMERIC_TYPES = {"number", "money"}


class ImportError(ValueError):
    """A user-facing multi-import failure (422)."""


class MultiFileInfo(BaseModel):
    name: str
    sheet_count: int
    row_count: int


class ImportSheet(BaseModel):
    """One sheet as the review page shows it (profile stays lightweight)."""

    id: str
    file_name: str
    name: str
    display_name: str
    is_empty: bool
    columns: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    profile: dict[str, int] = Field(default_factory=dict)
    inferred_entity: InferredEntity | None = None


class MergeSuggestion(BaseModel):
    entity_key: str
    entity_name: str
    sheet_ids: list[str]
    row_count: int
    # False when the sheets cannot actually be aligned on a primary key.
    alignable: bool = True


class ConflictOption(BaseModel):
    value: str
    label: str


class ImportConflict(BaseModel):
    id: str
    kind: Literal[
        "primary_key_duplicate", "field_type_mismatch", "enum_values_expanded"
    ]
    entity_key: str
    entity_name: str
    field: str | None = None
    message: str
    options: list[ConflictOption]
    default: str


class MultiImportResult(BaseModel):
    file_name: str
    file_type: str
    files: list[MultiFileInfo]
    sheets: list[ImportSheet]
    merges: list[MergeSuggestion]
    conflicts: list[ImportConflict]
    assembly_notes: list[str] = Field(default_factory=list)
    inferred_links: list[InferredLink] = Field(default_factory=list)
    business_model: dict[str, object] | None = None
    business_model_yaml: str | None = None
    understanding_engine: Literal["llm", "rules"] = "rules"
    understanding_notes: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class SheetSlot:
    id: str
    sheet: ProfiledParsedSheet


def normalize_column(name: str) -> str:
    """Column identity used for cross-sheet alignment."""
    return unicodedata.normalize("NFKC", name).strip().casefold()


def session_key(files: Sequence[tuple[str, bytes]]) -> str:
    """Cache key for one multi-file run (order-independent).

    The pipeline version is part of the key so re-uploading an unchanged file
    after a parser upgrade cannot reuse a stale understanding snapshot.
    """
    digest = hashlib.sha256()
    digest.update(SESSION_SCHEMA_VERSION.encode("utf-8"))
    digest.update(b"\x00")
    for name, content in sorted(files, key=lambda item: item[0]):
        digest.update(name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(content)
        digest.update(b"\x00")
    return digest.hexdigest()


def slots_of(understanding: ProfiledParsedWorkbook) -> list[SheetSlot]:
    return [
        SheetSlot(id=f"s{index}", sheet=sheet)
        for index, sheet in enumerate(understanding.sheets)
    ]


def _unique_sheet_name(name: str, file_name: str, used: set[str]) -> str:
    if name not in used:
        used.add(name)
        return name
    stem = Path(file_name).stem.strip() or file_name
    candidate = f"{stem} · {name}"
    suffix = 2
    while candidate in used:
        candidate = f"{stem} · {name} ({suffix})"
        suffix += 1
    used.add(candidate)
    return candidate


def merge_workbooks(
    parsed: Sequence[ParsedWorkbook],
) -> ParsedWorkbook:
    """Combine parsed workbooks into one, keeping sheet names unique."""
    used: set[str] = set()
    sheets: list[ParsedSheet] = []
    for workbook in parsed:
        for sheet in workbook.sheets:
            sheets.append(
                sheet.model_copy(
                    update={
                        "name": _unique_sheet_name(
                            sheet.name, workbook.file_name, used
                        ),
                        "source_file": workbook.file_name,
                    }
                )
            )
    file_types = {workbook.file_type for workbook in parsed}
    file_type = parsed[0].file_type if len(file_types) == 1 else "mixed"
    # The merged name keeps the first file's stem (it drives the app name) but
    # states how many uploads it stands for.
    merged_name = (
        parsed[0].file_name
        if len(parsed) == 1
        else f"{Path(parsed[0].file_name).stem} 等 {len(parsed)} 个文件"
    )
    return ParsedWorkbook(
        file_name=merged_name, file_type=file_type, sheets=sheets
    )


def parse_workbooks(files: Sequence[tuple[str, bytes]]) -> ParsedWorkbook:
    if not files:
        raise ImportError("请至少选择一个文件")
    if len(files) > MAX_FILES:
        raise ImportError(f"一次最多上传 {MAX_FILES} 个文件，请分批导入")
    parsed: list[ParsedWorkbook] = []
    for name, content in files:
        try:
            parsed.append(parse_workbook(name, content))
        except ParseError as exc:
            raise ImportError(f"「{name}」解析失败：{exc}") from exc
    return merge_workbooks(parsed)


def is_assemblable(slot: SheetSlot) -> bool:
    """Whether a sheet can become an entity (non-empty, known kind, has key)."""
    sheet = slot.sheet
    entity = sheet.inferred_entity
    return bool(
        not sheet.is_empty
        and entity is not None
        and entity.key != "unknown"
        and entity.key_field
    )


def default_groups(slots: Sequence[SheetSlot]) -> tuple[list[list[SheetSlot]], list[str]]:
    """Group sheets by entity key, splitting groups whose keys cannot align."""
    buckets: dict[str, list[SheetSlot]] = {}
    for slot in slots:
        if not is_assemblable(slot):
            continue
        entity = slot.sheet.inferred_entity
        assert entity is not None and entity.key_field is not None
        buckets.setdefault(entity.key, []).append(slot)

    groups: list[list[SheetSlot]] = []
    notes: list[str] = []
    for members in buckets.values():
        if len(members) == 1:
            groups.append(members)
            continue
        keys = [
            normalize_column(
                (slot.sheet.inferred_entity.key_field if slot.sheet.inferred_entity else "") or ""
            )
            for slot in members
        ]
        anchor, _count = Counter(keys).most_common(1)[0]
        kept = [slot for slot, key in zip(members, keys, strict=True) if key == anchor]
        dropped = [
            slot for slot, key in zip(members, keys, strict=True) if key != anchor
        ]
        groups.append(kept)
        for slot in dropped:
            groups.append([slot])
            entity = slot.sheet.inferred_entity
            name = entity.name if entity is not None else slot.sheet.name
            notes.append(
                f"工作表「{slot.sheet.name}」的主键列与其他「{name}」表不一致，"
                "已作为独立实体处理"
            )
    return groups, notes


def _align_columns(
    group: Sequence[SheetSlot],
) -> tuple[list[str], list[dict[int, int]]]:
    """Return merged columns plus per-sheet source->merged position maps."""
    columns: list[str] = []
    positions: dict[str, int] = {}
    maps: list[dict[int, int]] = []
    for slot in group:
        mapping: dict[int, int] = {}
        for source_pos, column in enumerate(slot.sheet.columns):
            key = normalize_column(column)
            target = positions.get(key)
            if target is None:
                target = len(columns)
                positions[key] = target
                columns.append(column)
            mapping[source_pos] = target
        maps.append(mapping)
    return columns, maps


def _merged_rows(
    group: Sequence[SheetSlot], width: int, maps: Sequence[dict[int, int]]
) -> list[list[str | None]]:
    rows: list[list[str | None]] = []
    for slot, mapping in zip(group, maps, strict=True):
        for row in slot.sheet.rows:
            merged: list[str | None] = [None] * width
            for source_pos, target in mapping.items():
                if source_pos < len(row):
                    merged[target] = row[source_pos]
            rows.append(merged)
    return rows


def _conflict_options(kind: str) -> list[ConflictOption]:
    if kind == _CONFLICT_PK:
        return [
            ConflictOption(value="skip", label="跳过重复项，保留首次出现"),
            ConflictOption(value="overwrite", label="用后出现的记录覆盖先出现的"),
            ConflictOption(value="keep", label="全部保留，重复主键自动加序号"),
        ]
    if kind == _CONFLICT_TYPE:
        return [
            ConflictOption(value="string", label="统一按文本处理"),
            ConflictOption(value="first", label="沿用第一张表的字段类型"),
        ]
    return [
        ConflictOption(value="merge", label="合并所有取值"),
        ConflictOption(value="string", label="降级为文本，不做枚举限制"),
    ]


def _default_choice(kind: str) -> str:
    return "skip" if kind == _CONFLICT_PK else (
        "string" if kind == _CONFLICT_TYPE else "merge"
    )


def _field_types(
    columns: Sequence[str], group: Sequence[SheetSlot]
) -> dict[str, list[InferredField]]:
    """Merged column name -> field definitions found in the group."""
    by_column: dict[str, list[InferredField]] = {column: [] for column in columns}
    normalized = {normalize_column(column): column for column in columns}
    for slot in group:
        for field in slot.sheet.inferred_fields:
            column = normalized.get(normalize_column(field.column))
            if column is not None:
                by_column[column].append(field)
    return by_column


def _merge_field(
    definitions: Sequence[InferredField],
    *,
    conflict_kind: str | None,
    choice: str,
) -> InferredField:
    """Align one column's definitions according to the chosen conflict rule."""
    base = definitions[0]
    types = {field.type for field in definitions}
    enum_fields = [field for field in definitions if field.type == "enum"]

    if conflict_kind is None and len(types) > 1:
        conflict_kind = _CONFLICT_TYPE

    if conflict_kind == _CONFLICT_TYPE:
        picked = choice if choice in {"string", "first"} else "string"
        if picked == "string":
            base = base.model_copy(
                update={
                    "type": "string",
                    "enum_values": [],
                    "needs_review": True,
                    "role": "dimension" if base.role == "enum" else base.role,
                }
            )
    elif conflict_kind == _CONFLICT_ENUM:
        merged_values = list(
            dict.fromkeys(
                value for field in enum_fields for value in (field.enum_values or [])
            )
        )
        if choice == "string" or len(merged_values) > MAX_ENUM_VALUES:
            base = base.model_copy(
                update={
                    "type": "string",
                    "enum_values": [],
                    "needs_review": True,
                    "role": "dimension" if base.role == "enum" else base.role,
                }
            )
        else:
            base = base.model_copy(
                update={"type": "enum", "enum_values": merged_values}
            )
    return base


def build_group(
    key: str,
    name: str,
    group: Sequence[SheetSlot],
    choices: dict[str, str],
) -> tuple[ProfiledParsedSheet, list[ImportConflict]]:
    """Build one virtual sheet from the group and detect merge conflicts."""
    columns, maps = _align_columns(group)
    rows = _merged_rows(group, len(columns), maps)
    definitions = _field_types(columns, group)
    # Prefer the key the model proposed for each column so merged sheets keep
    # semantic keys; conflicts and the virtual sheet use the same mapping.
    proposed = {}
    for column in columns:
        for definition in definitions.get(column) or []:
            if definition.key:
                proposed[column] = definition.key
                break
    field_key_list, _ = keys_for_columns(columns, proposed)
    key_by_column = dict(zip(columns, field_key_list, strict=True))

    base_entity = group[0].sheet.inferred_entity
    assert base_entity is not None and base_entity.key_field is not None
    key_column = base_entity.key_field
    key_index = columns.index(key_column)

    merged_fields: list[InferredField] = []
    conflicts: list[ImportConflict] = []

    for column in columns:
        defs = definitions.get(column) or []
        if not defs:
            continue
        types = {field.type for field in defs}
        conflict_kind: str | None = None
        conflict_id = f"type:{key}:{key_by_column[column]}"
        if len(types) > 1:
            # number/money differences are a precision nuance, not a mismatch.
            if not types <= _NUMERIC_TYPES:
                conflict_kind = _CONFLICT_TYPE
        if conflict_kind is None and len(defs) > 1:
            enum_sets = {tuple(field.enum_values or []) for field in defs if field.type == "enum"}
            if len(enum_sets) > 1:
                conflict_kind = _CONFLICT_ENUM
                conflict_id = f"enum:{key}:{key_by_column[column]}"
        choice = choices.get(conflict_id, _default_choice(conflict_kind or ""))
        field = _merge_field(
            defs,
            conflict_kind=conflict_kind,
            choice=choice,
        ).model_copy(update={"key": key_by_column[column]})
        merged_fields.append(field)

        if conflict_kind == _CONFLICT_TYPE:
            conflicts.append(
                ImportConflict(
                    id=conflict_id,
                    kind=_CONFLICT_TYPE,
                    entity_key=key,
                    entity_name=name,
                    field=key_by_column[column],
                    message=(
                        f"「{name}」的字段「{column}」在不同表格中类型不一致"
                        f"（{'、'.join(sorted(types))}）"
                    ),
                    options=_conflict_options(_CONFLICT_TYPE),
                    default=_default_choice(_CONFLICT_TYPE),
                )
            )
        elif conflict_kind == _CONFLICT_ENUM:
            conflicts.append(
                ImportConflict(
                    id=conflict_id,
                    kind=_CONFLICT_ENUM,
                    entity_key=key,
                    entity_name=name,
                    field=key_by_column[column],
                    message=(
                        f"「{name}」的字段「{column}」在不同表格中的可选值不一致，"
                        "合并后会包含全部取值"
                    ),
                    options=_conflict_options(_CONFLICT_ENUM),
                    default=_default_choice(_CONFLICT_ENUM),
                )
            )

    duplicates: dict[str, int] = {}
    for row in rows:
        value = row[key_index] if key_index < len(row) else None
        if value is None or not str(value).strip():
            continue
        duplicates[str(value).strip()] = duplicates.get(str(value).strip(), 0) + 1
    repeated = {value: count for value, count in duplicates.items() if count > 1}
    if repeated:
        examples = "、".join(list(repeated)[:3])
        count = sum(repeated.values()) - len(repeated)
        conflicts.append(
            ImportConflict(
                id=f"pk:{key}",
                kind=_CONFLICT_PK,
                entity_key=key,
                entity_name=name,
                field=key_by_column.get(key_column),
                message=(
                    f"「{name}」合并后有 {count} 条记录的主键重复"
                    f"（例如 {examples}），请选择处理方式"
                ),
                options=_conflict_options(_CONFLICT_PK),
                default=_default_choice(_CONFLICT_PK),
            )
        )

    merged_warnings = list(
        dict.fromkeys(warning for slot in group for warning in slot.sheet.warnings)
    )
    profile = profile_sheet(
        ParsedSheet(
            name=group[0].sheet.name,
            source_file=group[0].sheet.source_file,
            is_empty=not rows,
            header_row_number=group[0].sheet.header_row_number,
            columns=columns,
            rows=rows,
            data_row_count=len(rows),
            warnings=merged_warnings,
        )
    )
    virtual = ProfiledParsedSheet(
        name=group[0].sheet.name,
        source_file=group[0].sheet.source_file,
        is_empty=not rows,
        header_row_number=group[0].sheet.header_row_number,
        columns=columns,
        rows=rows,
        data_row_count=len(rows),
        warnings=merged_warnings,
        profile=profile,
        inferred_fields=merged_fields,
        inferred_entity=base_entity.model_copy(update={"key_field": key_column}),
    )
    return virtual, conflicts


def _plan_for(
    virtual: ProfiledParsedSheet, key: str, name: str
) -> EntityPlan:
    field_key_list, _ = keys_for_sheet(virtual)
    field_keys = dict(zip(virtual.columns, field_key_list, strict=True))
    entity = virtual.inferred_entity
    assert entity is not None and entity.key_field is not None
    return EntityPlan(
        sheet=virtual,
        key=key,
        name=name,
        key_field_column=entity.key_field,
        key_field_key=field_keys[entity.key_field],
        field_keys=field_keys,
    )


def build_plans(
    groups: Sequence[Sequence[SheetSlot]],
    choices: dict[str, str],
) -> tuple[list[EntityPlan], list[ImportConflict], list[str]]:
    """Turn groups into EntityPlans, merging each group into one sheet."""
    usable = [group for group in groups if group]
    keys = unique_entity_keys(
        [
            group[0].sheet.inferred_entity.key  # type: ignore[union-attr]
            for group in usable
        ]
    )
    plans: list[EntityPlan] = []
    conflicts: list[ImportConflict] = []
    notes: list[str] = []
    for group, key in zip(usable, keys, strict=True):
        entity = group[0].sheet.inferred_entity
        assert entity is not None
        virtual, group_conflicts = build_group(key, entity.name, group, choices)
        plans.append(_plan_for(virtual, key, entity.name))
        conflicts.extend(group_conflicts)
        if len(group) > 1:
            notes.append(
                f"「{entity.name}」由 {len(group)} 张工作表合并："
                + "、".join(slot.sheet.name for slot in group)
            )
    return plans, conflicts, notes


def import_sheets(understanding: ProfiledParsedWorkbook) -> list[ImportSheet]:
    return [
        ImportSheet(
            id=f"s{index}",
            file_name=sheet.source_file or understanding.file_name,
            name=sheet.name,
            display_name=sheet.name,
            is_empty=sheet.is_empty,
            columns=list(sheet.columns),
            warnings=list(sheet.warnings),
            profile={"row_count": sheet.profile.row_count},
            inferred_entity=sheet.inferred_entity,
        )
        for index, sheet in enumerate(understanding.sheets)
    ]


def _merge_suggestions(groups: Sequence[Sequence[SheetSlot]]) -> list[MergeSuggestion]:
    suggestions: list[MergeSuggestion] = []
    for group in groups:
        if len(group) < 2:
            continue
        entity = group[0].sheet.inferred_entity
        if entity is None:
            continue
        suggestions.append(
            MergeSuggestion(
                entity_key=entity.key,
                entity_name=entity.name,
                sheet_ids=[slot.id for slot in group],
                row_count=sum(
                    slot.sheet.profile.row_count for slot in group
                ),
                alignable=True,
            )
        )
    return suggestions


def parse_multi(
    files: Sequence[tuple[str, bytes]],
    *,
    llm_client: object | None = _USE_SETTINGS_LLM,
) -> MultiImportResult:
    """Parse and understand several uploads as one import."""
    understanding = understand(files, llm_client=llm_client)
    return build_result(understanding)


def understand(
    files: Sequence[tuple[str, bytes]],
    *,
    llm_client: object | None = _USE_SETTINGS_LLM,
) -> ProfiledParsedWorkbook:
    """Parse the workbooks and run the per-sheet understanding pipeline."""
    workbook = parse_workbooks(files)
    return add_profiles(workbook, llm_client=llm_client)


def build_result(understanding: ProfiledParsedWorkbook) -> MultiImportResult:
    """Default merge plan for an understanding (no user choices yet)."""
    slots = slots_of(understanding)
    groups, group_notes = default_groups(slots)
    plans, conflicts, plan_notes = build_plans(groups, {})
    links = infer_links(understanding, plans)
    model = assemble_model(understanding, plans, links)

    understanding.inferred_links = links
    understanding.assembly_notes = [
        *understanding.assembly_notes,
        *group_notes,
        *plan_notes,
    ]
    if model is not None:
        understanding.business_model = model_to_dict(model)
        understanding.business_model_yaml = model_to_yaml(model)

    files_info: list[MultiFileInfo] = []
    for name in dict.fromkeys(
        slot.sheet.source_file or understanding.file_name for slot in slots
    ):
        sheets = [
            slot for slot in slots
            if (slot.sheet.source_file or understanding.file_name) == name
        ]
        files_info.append(
            MultiFileInfo(
                name=name,
                sheet_count=len(sheets),
                row_count=sum(slot.sheet.profile.row_count for slot in sheets),
            )
        )

    return MultiImportResult(
        file_name=understanding.file_name,
        file_type=understanding.file_type,
        files=files_info,
        sheets=import_sheets(understanding),
        merges=_merge_suggestions(groups),
        conflicts=conflicts,
        assembly_notes=understanding.assembly_notes,
        inferred_links=links,
        business_model=understanding.business_model,
        business_model_yaml=understanding.business_model_yaml,
        understanding_engine=understanding.understanding_engine,
        understanding_notes=understanding.understanding_notes,
    )


class MultiPreview(BaseModel):
    """Recomputed plan after the review page changes sheet selection/merges."""

    sheets: list[ImportSheet]
    merges: list[MergeSuggestion]
    conflicts: list[ImportConflict]
    assembly_notes: list[str] = Field(default_factory=list)
    inferred_links: list[InferredLink] = Field(default_factory=list)
    business_model: dict[str, object] | None = None
    business_model_yaml: str | None = None


class MultiPreviewOptions(BaseModel):
    sheets: list[str] = Field(default_factory=list)
    groups: list[list[str]] = Field(default_factory=list)
    conflicts: dict[str, str] = Field(default_factory=dict)


def select_slots(
    slots: Sequence[SheetSlot], ids: Sequence[str]
) -> tuple[list[SheetSlot], list[str]]:
    """Sheets the user keeps, plus the names skipped as not assemblable."""
    if not ids:
        chosen = list(slots)
    else:
        by_id = {slot.id: slot for slot in slots}
        unknown = [sheet_id for sheet_id in ids if sheet_id not in by_id]
        if unknown:
            raise ImportError(f"选择了不存在的工作表：{', '.join(unknown)}")
        chosen = [by_id[sheet_id] for sheet_id in dict.fromkeys(ids)]
    selected = [slot for slot in chosen if is_assemblable(slot)]
    skipped = [slot.sheet.name for slot in chosen if not is_assemblable(slot)]
    if not selected:
        raise ImportError(
            "所选工作表中没有可生成系统的业务实体（缺少可识别的工作表或标识字段）"
        )
    return selected, skipped


def group_slots(
    slots: Sequence[SheetSlot], groups: Sequence[Sequence[str]]
) -> list[list[SheetSlot]]:
    """Apply explicit merge groups; unmentioned sheets become single groups."""
    by_id = {slot.id: slot for slot in slots}
    used: set[str] = set()
    result: list[list[SheetSlot]] = []
    for group in groups:
        members: list[SheetSlot] = []
        for sheet_id in group:
            if sheet_id not in by_id:
                raise ImportError(f"合并方案引用了未选择的工作表：{sheet_id}")
            if sheet_id in used:
                raise ImportError("同一张工作表不能出现在多个合并组中")
            used.add(sheet_id)
            members.append(by_id[sheet_id])
        if members:
            result.append(
                sorted(members, key=lambda slot: list(slots).index(slot))
            )
    for slot in slots:
        if slot.id not in used:
            result.append([slot])
    result.sort(key=lambda group: list(slots).index(group[0]))
    return result


def preview(
    understanding: ProfiledParsedWorkbook,
    *,
    sheets: Sequence[str] = (),
    groups: Sequence[Sequence[str]] = (),
    choices: dict[str, str] | None = None,
) -> MultiPreview:
    """Recompute plan/conflicts/links for a changed merge scheme."""
    slots = slots_of(understanding)
    selected, _skipped = select_slots(slots, sheets)
    merged_groups = group_slots(selected, groups)
    plans, conflicts, notes = build_plans(merged_groups, choices or {})
    links = infer_links(understanding, plans)
    model = assemble_model(understanding, plans, links)
    return MultiPreview(
        sheets=import_sheets(understanding),
        merges=_merge_suggestions(merged_groups),
        conflicts=conflicts,
        assembly_notes=[*understanding.assembly_notes, *notes],
        inferred_links=links,
        business_model=model_to_dict(model) if model is not None else None,
        business_model_yaml=model_to_yaml(model) if model is not None else None,
    )


def sheet_choices(understanding: ProfiledParsedWorkbook) -> list[SheetSlot]:
    """Slots in the stable order used by the review page."""
    return slots_of(understanding)


__all__ = [
    "ConflictOption",
    "ImportConflict",
    "ImportError",
    "ImportSheet",
    "MAX_FILES",
    "MergeSuggestion",
    "MultiFileInfo",
    "MultiImportResult",
    "MultiPreview",
    "MultiPreviewOptions",
    "SheetSlot",
    "build_plans",
    "build_result",
    "default_groups",
    "group_slots",
    "import_sheets",
    "is_assemblable",
    "merge_workbooks",
    "normalize_column",
    "parse_multi",
    "parse_workbooks",
    "preview",
    "select_slots",
    "session_key",
    "sheet_choices",
    "slots_of",
    "understand",
]
