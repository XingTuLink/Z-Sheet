"""Profile structures produced over a parsed sheet (Day 6).

Counts and ratios only. No type inference yet (Days 7-8 consume these
profiles; enum inference in particular reads top_values directly).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.ingestion.schemas import Cell, ParsedSheet

DEFAULT_SAMPLE_SIZE = 100
TOP_VALUES_LIMIT = 5

# Confidence tiers frozen in design doc section 10.1 (V0.1).
CONFIDENCE_HIGH = 0.85
CONFIDENCE_MEDIUM = 0.60

InferredFieldType = Literal["string", "number", "money", "date", "enum", "phone"]
InferredFieldRole = Literal[
    "identifier", "dimension", "measure", "time", "enum", "text"
]
# Rule inference only emits customer/order/product/unknown; the LLM layer may
# propose arbitrary semantic keys (reviewer, asset, project…), so the wire
# type is a plain snake_case string. Constants below name the rule outputs.
InferredEntityKind = str
RULE_ENTITY_KINDS = ("customer", "order", "product", "unknown")


class ValueCount(BaseModel):
    value: str
    count: int


class ColumnProfile(BaseModel):
    name: str
    total_count: int = 0
    null_count: int = 0
    non_null_count: int = 0
    null_ratio: float = 0.0
    # Number of distinct non-null values.
    distinct_count: int = 0
    # Non-null values that occur exactly once.
    unique_value_count: int = 0
    # Cells whose value occurs more than once (non-null - unique cells).
    duplicate_cell_count: int = 0
    # True only when every non-null cell differs: the key/identifier candidate
    # signal later entity/relation inference builds on. All-null/empty => False.
    is_unique: bool = False
    unique_ratio: float = 0.0
    # Most frequent non-null values, frequency-descending then value for ties.
    top_values: list[ValueCount] = Field(default_factory=list)


class SheetProfile(BaseModel):
    row_count: int = 0
    # Rows beyond the first occurrence of every fully-duplicate row.
    duplicate_row_count: int = 0
    columns: list[ColumnProfile] = Field(default_factory=list)
    # Deterministic uniform-stride sample for preview/inference.
    sample_rows: list[list[Cell]] = Field(default_factory=list)
    sample_size_requested: int = DEFAULT_SAMPLE_SIZE
    sample_is_full: bool = True
    sample_strategy: str = "uniform-stride"


class InferredField(BaseModel):
    column: str
    type: InferredFieldType
    confidence: float
    # Auto-derived from the frozen tiers; medium/low must enter the review
    # queue. High-confidence items may be forced to review, never the reverse.
    needs_review: bool
    signals: list[str] = Field(default_factory=list)
    enum_values: list[str] = Field(default_factory=list)
    # Semantic role (Day 9) is a second inference dimension with its own
    # confidence; Day 11 model assembly combines the two.
    role: InferredFieldRole = "text"
    role_confidence: float = 0.0
    role_needs_review: bool = True
    role_signals: list[str] = Field(default_factory=list)


class InferredEntity(BaseModel):
    """One entity candidate inferred from a single sheet (Day 10).

    Candidate only: cross-sheet dedupe, key namespacing and link discovery
    are Day 11. `unknown` is the honest result for sheets whose composition
    matches none of the three V0.1 kinds (design 10-15).
    """

    source_sheet: str
    key: InferredEntityKind
    name: str
    key_field: str | None
    confidence: float
    needs_review: bool
    signals: list[str] = Field(default_factory=list)


class ProfiledParsedSheet(ParsedSheet):
    profile: SheetProfile
    inferred_fields: list[InferredField] = Field(default_factory=list)
    inferred_entity: InferredEntity | None = None


class InferredMetricDefinition(BaseModel):
    """AI-proposed business definition (口径) for one deterministic metric.

    Metric *selection* stays deterministic (a count per entity plus sums over
    flow-money fields); the LLM only supplies the human-facing definition and
    a confidence, so it cannot invent metrics on unknown fields. References
    use the sheet name and the original column name and are resolved against
    assembled plans, exactly like link proposals. Persisted with the
    understanding session because Confirm re-assembles the model.
    """

    source_sheet: str
    op: Literal["count", "sum"]
    # Original column name for sum; None for entity-wide count.
    field: str | None = None
    business_definition: str = Field(min_length=1)
    confidence: float


class InferredLink(BaseModel):
    """One-to-many link candidate between two assembled entities (Day 11).

    Direction: from_entity is the unique/parent side, to_entity the repeating
    /child side. Design 11.3 evidence order: same field name, value-set
    overlap, uniqueness, distribution, field semantics.
    """

    key: str
    from_entity: str
    to_entity: str
    on_from: str
    on_to: str
    confidence: float
    needs_review: bool
    review_reason: str | None = None
    signals: list[str] = Field(default_factory=list)
    overlap_recall: float
    overlap_precision: float


class ProfiledParsedWorkbook(BaseModel):
    file_name: str
    file_type: str
    sheets: list[ProfiledParsedSheet]
    inferred_links: list[InferredLink] = Field(default_factory=list)
    # Validated Business Model (design chapter 7) plus its YAML form; None
    # when no sheet could be assembled into an entity.
    business_model: dict[str, object] | None = None
    business_model_yaml: str | None = None
    # Human-readable reasons sheets/links were dropped during assembly.
    assembly_notes: list[str] = Field(default_factory=list)
    # Day 21: which engine produced the understanding ("llm" or the
    # deterministic "rules" fallback) plus human-readable LLM notes.
    understanding_engine: Literal["llm", "rules"] = "rules"
    understanding_notes: list[str] = Field(default_factory=list)
    # AI-proposed business definitions (口径) for the deterministic metrics;
    # serialized on purpose so Confirm re-assembly (which rebuilds metrics)
    # consumes exactly the text the user reviewed.
    metric_definitions: list[InferredMetricDefinition] = Field(default_factory=list)
    # Internal staging of LLM link proposals between entity mapping and the
    # post-plan field-key resolution; not part of the API contract.
    llm_link_specs: list[dict[str, object]] = Field(default_factory=list, exclude=True)
