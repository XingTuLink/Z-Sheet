"""Deterministic column/table profiling and sampling (Day 6).

Pure functions over ParsedSheet rows. "Deterministic" matters twice here:
the same file must always yield the same profile (tests and later inference
depend on it), and sampling must not hide regions of the sheet — hence a
fixed uniform stride rather than a random sample.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from backend.ingestion.schemas import Cell, ParsedSheet, ParsedWorkbook

from .schemas import (
    DEFAULT_SAMPLE_SIZE,
    TOP_VALUES_LIMIT,
    ColumnProfile,
    ProfiledParsedSheet,
    ProfiledParsedWorkbook,
    SheetProfile,
    ValueCount,
)

# Pipeline stage keys emitted through the optional progress callback.
# "sheets" is emitted by the API route right after parsing; the understanding
# stages below are emitted by add_profiles as real work completes. Every key
# maps to a user-visible line in the Parsing Progress UI (Day 12).
STAGE_SHEETS = "sheets"
STAGE_FIELDS = "fields"
STAGE_ENTITIES = "entities"
STAGE_RELATIONS = "relations"
STAGE_ASSEMBLE = "assemble"

# (stage_key, count) -> None. The "assemble" stage carries count=0 and works
# as an "in progress" marker; the final result delivery marks it complete.
StageCallback = Callable[[str, int], None]


def _profile_column(name: str, values: list[Cell], total: int) -> ColumnProfile:
    non_null = [value for value in values if value is not None]
    null_count = total - len(non_null)

    counter = Counter(non_null)
    distinct_count = len(counter)
    unique_value_count = sum(1 for count in counter.values() if count == 1)
    duplicate_cell_count = len(non_null) - unique_value_count

    # Ties break alphabetically so output is stable regardless of dict order.
    top = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:TOP_VALUES_LIMIT]

    is_unique = bool(non_null) and unique_value_count == len(non_null)
    return ColumnProfile(
        name=name,
        total_count=total,
        null_count=null_count,
        non_null_count=len(non_null),
        null_ratio=round(null_count / total, 4) if total else 0.0,
        distinct_count=distinct_count,
        unique_value_count=unique_value_count,
        duplicate_cell_count=duplicate_cell_count,
        is_unique=is_unique,
        unique_ratio=round(distinct_count / len(non_null), 4) if non_null else 0.0,
        top_values=[ValueCount(value=value, count=count) for value, count in top],
    )


def uniform_sample(
    rows: list[list[Cell]], size: int = DEFAULT_SAMPLE_SIZE
) -> tuple[list[list[Cell]], bool]:
    """Return up to `size` rows at equal spacing covering the whole sheet.

    Full rows are returned when their count fits the budget. Otherwise exactly
    `size` indices are picked from 0 to n-1 at equal intervals (rounded), so
    the sample always includes both the first and last rows and never contains
    duplicates. Pure arithmetic — no RNG, so output is fully deterministic.
    """
    if size < 1:
        raise ValueError("sample size must be at least 1")
    n = len(rows)
    if n <= size:
        return list(rows), True
    if size == 1:
        return [rows[0]], False

    last = size - 1
    indices = [round(i * (n - 1) / last) for i in range(size)]
    return [rows[index] for index in indices], False


def profile_sheet(sheet: ParsedSheet, sample_size: int = DEFAULT_SAMPLE_SIZE) -> SheetProfile:
    rows = sheet.rows
    total = len(rows)

    # Column-oriented scan without materializing a full transpose.
    column_profiles: list[ColumnProfile] = []
    for pos, name in enumerate(sheet.columns):
        values = [row[pos] if pos < len(row) else None for row in rows]
        column_profiles.append(_profile_column(name, values, total))

    # Whole-row duplication. Ingestion aligns every data row to header width,
    # so rows are directly hashable tuples.
    seen: set[tuple[Cell, ...]] = set()
    duplicate_row_count = 0
    for row in rows:
        key = tuple(row)
        if key in seen:
            duplicate_row_count += 1
        else:
            seen.add(key)

    sample, is_full = uniform_sample(rows, sample_size)
    return SheetProfile(
        row_count=total,
        duplicate_row_count=duplicate_row_count,
        columns=column_profiles,
        sample_rows=sample,
        sample_size_requested=sample_size,
        sample_is_full=is_full,
    )


def add_profiles(
    workbook: ParsedWorkbook,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    on_stage: StageCallback | None = None,
) -> ProfiledParsedWorkbook:
    """Run the full understanding pipeline and assemble a Business Model.

    When ``on_stage`` is given it receives real progress events
    (fields/entities/relations/assemble with the count produced at that
    stage), backing the Parsing Progress UI instead of a fake spinner.
    """
    from backend.domain.serialization import model_to_dict, model_to_yaml

    from .entity_inference import infer_entity
    from .link_inference import infer_links
    from .model_assembly import assemble_model, build_entity_plans
    from .role_inference import annotate_roles
    from .type_inference import infer_sheet

    def _emit(key: str, count: int) -> None:
        if on_stage is not None:
            on_stage(key, count)

    enriched: list[ProfiledParsedSheet] = []
    for sheet in workbook.sheets:
        profile = profile_sheet(sheet, sample_size)
        inferred = infer_sheet(sheet, profile) if not sheet.is_empty else []
        if inferred:
            annotate_roles(inferred, profile)
        entity = infer_entity(sheet, inferred) if inferred else None
        enriched.append(
            ProfiledParsedSheet(
                **sheet.model_dump(),
                profile=profile,
                inferred_fields=inferred,
                inferred_entity=entity,
            )
        )

    # Field recognition finished: one entry per column of a non-empty sheet.
    field_count = sum(len(sheet.inferred_fields) for sheet in enriched)
    _emit(STAGE_FIELDS, field_count)

    result = ProfiledParsedWorkbook(
        file_name=workbook.file_name,
        file_type=workbook.file_type,
        sheets=enriched,
    )

    # Workbook-level stage: entity plans -> links -> validated Business Model.
    plans, notes = build_entity_plans(result)
    result.assembly_notes = notes
    _emit(STAGE_ENTITIES, len(plans))
    result.inferred_links = infer_links(result, plans)
    _emit(STAGE_RELATIONS, len(result.inferred_links))
    _emit(STAGE_ASSEMBLE, 0)
    model = assemble_model(result, plans, result.inferred_links)
    if model is not None:
        result.business_model = model_to_dict(model)
        result.business_model_yaml = model_to_yaml(model)
    return result
