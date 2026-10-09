"""LLM-powered understanding of parsed workbooks (Day 21).

The deterministic engine (type/role/entity/link inference) only recognizes a
frozen set of sales-style patterns. This module asks an OpenAI-compatible LLM
to read sheet names, headers and sample rows and propose the Business Model
understanding (entities, key fields, column types/roles, one-to-many links) as
strict JSON.

Safety properties:

- every LLM proposal is validated against the *actually parsed* workbook:
  sheets and columns must exist, types/roles must be from the frozen
  vocabulary, links must resolve to real entities and columns;
- columns the model omits fall back to the deterministic inference already
  attached to the sheet, so one bad proposal never loses a column;
- entity keys are normalized to the assembler's ``[a-z][a-z0-9_]*`` namespace;
- nothing here touches the database or the frontend contract — the output is
  the same Inferred* structures the deterministic pipeline produces.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.ai.provider import ChatClient, LLMError

from .model_assembly import EntityPlan
from .naming import unique_field_keys
from .schemas import (
    CONFIDENCE_HIGH,
    InferredEntity,
    InferredField,
    InferredLink,
    ProfiledParsedWorkbook,
)

FIELD_TYPES: frozenset[str] = frozenset(
    {"string", "number", "money", "date", "enum", "phone"}
)
FIELD_ROLES: frozenset[str] = frozenset(
    {"identifier", "dimension", "measure", "time", "enum", "text"}
)
MAX_SAMPLE_ROWS = 20
MAX_CELL_LENGTH = 80
MAX_ENUM_VALUES = 20
_ENTITY_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")

SYSTEM_PROMPT = """\
你是 Z-Sheet 的业务建模引擎。用户上传 Excel/CSV，你会收到每个非空工作表的名称、表头列与样例数据。
你的职责：把表格理解成业务模型。只输出一个 JSON 对象，不要输出任何解释、注释或 Markdown 代码块。

理解规则：
1. 每个非空工作表对应一个实体。
   - key：英文小写 snake_case，语义化（例如 reviewer、employee、asset、project、supplier）；
   - name：准确的中文业务名（例如 外审人员、固定资产、项目、供应商）；
   - 只有表格确实是客户/订单/商品时，才使用 customer/order/product。
2. key_field 是业务主键列：能唯一标识一行人/物/事项的名称或编号列。
   “序号”“行号”“No.”这类纯流水号不是业务主键，除非没有任何其他唯一列。
   每个实体有且仅有一个 role=identifier 的列，就是 key_field；
   电话、身份证号、银行账号等虽然唯一，但不是业务主键，role 用 text/dimension。
3. fields 必须覆盖工作表的每一列：
   - type 取值：string | number | money | date | enum | phone
     · money：金额、价格、费用、成本、工资等货币数值
     · date：日期/时间列（样例形如 2026-01-01、2026/1/1）
     · phone：手机、电话
     · enum：只有少量固定取值的分类列，必须给出 enum_values
     · number：年龄、数量等非货币数值
     · 其余为 string
   - role 取值：identifier | dimension | measure | time | enum | text
     · identifier：业务主键列；dimension：分类/标签/另一张表的名称；measure：数值或金额
     · time：日期；enum：代码式枚举；text：备注、地址、描述等长文本
   - confidence：0 到 1；拿不准的列给 0.6 以下并设 needs_review 为 true。
4. links 只填有把握的一对多关联：from_entity 是“一”方（主表，其 on_from 列值唯一），
   to_entity 是“多”方子表，on_from/on_to 都填列名；没有把握就给空数组。
5. 忽略明显无业务意义的占位行（整行只有“.”“-”“/”或空格）。
6. 输出必须严格符合以下结构：
{
  "entities": [
    {
      "source_sheet": "工作表名",
      "key": "reviewer",
      "name": "外审人员",
      "key_field": "姓名",
      "confidence": 0.9,
      "needs_review": false,
      "reason": "一句话中文理由",
      "fields": [
        {"column": "姓名", "type": "string", "role": "identifier",
         "confidence": 0.9, "needs_review": false, "enum_values": [], "reason": ""}
      ]
    }
  ],
  "links": [
    {"from_entity": "customer", "to_entity": "order",
     "on_from": "客户名称", "on_to": "客户名称", "confidence": 0.8, "reason": ""}
  ]
}"""


# --- raw LLM JSON shapes (lenient; semantic validation happens against workbook) ---------


class LLMFieldSpec(BaseModel):
    column: str
    type: str
    role: str = "text"
    confidence: float = 0.8
    needs_review: bool = False
    enum_values: list[str] = Field(default_factory=list)
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return max(0.0, min(1.0, value))


class LLMEntitySpec(BaseModel):
    source_sheet: str
    key: str
    name: str
    key_field: str | None = None
    confidence: float = 0.8
    needs_review: bool = False
    reason: str = ""
    fields: list[LLMFieldSpec] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return max(0.0, min(1.0, value))


class LLMLinkSpec(BaseModel):
    from_entity: str
    to_entity: str
    on_from: str
    on_to: str
    confidence: float = 0.7
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return max(0.0, min(1.0, value))


class LLMUnderstanding(BaseModel):
    entities: list[LLMEntitySpec] = Field(default_factory=list)
    links: list[LLMLinkSpec] = Field(default_factory=list)


def _clip(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > MAX_CELL_LENGTH:
        return text[:MAX_CELL_LENGTH] + "…"
    return text


def build_workbook_digest(result: ProfiledParsedWorkbook) -> dict[str, object]:
    """Serialize workbook structure for the prompt (headers + sample rows)."""
    sheets: list[dict[str, object]] = []
    for sheet in result.sheets:
        if sheet.is_empty:
            continue
        sample_rows: list[list[str | None]] = []
        for row in sheet.rows[:MAX_SAMPLE_ROWS]:
            sample_rows.append(
                [_clip(row[pos] if pos < len(row) else None) for pos in range(len(sheet.columns))]
            )
        sheets.append(
            {
                "name": sheet.name,
                "columns": list(sheet.columns),
                "data_row_count": sheet.data_row_count,
                "sample_rows": sample_rows,
            }
        )
    return {"file_name": result.file_name, "sheets": sheets}


def build_messages(result: ProfiledParsedWorkbook) -> list[dict[str, str]]:
    import json

    digest = json.dumps(build_workbook_digest(result), ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"请理解以下工作簿并输出模型 JSON：\n\n{digest}",
        },
    ]


def _normalize_entity_key(raw: str, used: set[str]) -> str:
    """Force an arbitrary proposal into the assembler key namespace."""
    key = raw.strip().lower()
    key = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    if not key or key[0].isdigit():
        key = f"entity_{key}" if key else "entity"
    base = key
    suffix = 2
    while key in used:
        key = f"{base}_{suffix}"
        suffix += 1
    used.add(key)
    return key


def _is_enum_viable(spec: LLMFieldSpec) -> bool:
    values = [value.strip() for value in spec.enum_values if value.strip()]
    return bool(values) and len(set(values)) <= MAX_ENUM_VALUES


def _merge_field(
    column: str,
    fallback: InferredField,
    spec: LLMFieldSpec | None,
) -> InferredField:
    if spec is None:
        # Model omitted this column — keep deterministic inference, tagged so
        # the UI can still tell LLM vs rule provenance.
        fallback.signals = [*fallback.signals, "llm:column_fallback"]
        return fallback

    ftype = spec.type if spec.type in FIELD_TYPES else fallback.type
    role = spec.role if spec.role in FIELD_ROLES else fallback.role
    if ftype == "enum" and not _is_enum_viable(spec):
        # Same defensive downgrade the assembler applies: enum without a
        # bounded value set is invalid; keep it as plain string.
        ftype = "string"
        if role == "enum":
            role = "dimension"
    enum_values = list(dict.fromkeys(spec.enum_values))[:MAX_ENUM_VALUES]
    confidence = spec.confidence
    needs_review = spec.needs_review or confidence < CONFIDENCE_HIGH
    signals = ["llm"]
    if spec.reason:
        signals.append(f"llm_reason:{spec.reason[:80]}")
    return InferredField(
        column=column,
        type=ftype,  # type: ignore[arg-type]
        confidence=confidence,
        needs_review=needs_review,
        signals=signals,
        enum_values=enum_values if ftype == "enum" else [],
        role=role,  # type: ignore[arg-type]
        role_confidence=confidence,
        role_needs_review=needs_review,
        role_signals=["llm"],
    )


def apply_llm_understanding(
    result: ProfiledParsedWorkbook,
    client: ChatClient,
) -> tuple[Literal["llm", "rules"], list[str]]:
    """Call the LLM and merge its proposal into the profiled workbook.

    Returns the engine actually used and human-readable notes. On any LLM or
    validation failure the caller keeps the deterministic results untouched.
    """
    notes: list[str] = []
    try:
        payload = client.complete_json(build_messages(result))
        proposal = LLMUnderstanding.model_validate(payload)
    except (LLMError, ValidationError) as exc:
        notes.append(f"LLM 理解失败，已使用规则引擎结果：{exc}")
        return "rules", notes

    sheets_by_name = {sheet.name.strip(): sheet for sheet in result.sheets}
    used_keys: set[str] = set()
    # Proposed links reference source entity keys; remember sheet -> final key.
    sheet_to_entity_key: dict[str, str] = {}
    matched_sheets: set[str] = set()

    for entity_spec in proposal.entities:
        sheet_name = entity_spec.source_sheet.strip()
        sheet = sheets_by_name.get(sheet_name)
        if sheet is None:
            notes.append(
                f"LLM 提出的实体「{entity_spec.name}」找不到工作表"
                f"「{sheet_name}」，已忽略"
            )
            continue
        matched_sheets.add(sheet.name)

        final_key = _normalize_entity_key(entity_spec.key, used_keys)
        sheet_to_entity_key[sheet.name] = final_key

        specs_by_column = {
            spec.column.strip(): spec for spec in entity_spec.fields if spec.column.strip()
        }
        merged_fields = [
            _merge_field(column, fallback, specs_by_column.get(column.strip()))
            for column, fallback in zip(
                sheet.columns, sheet.inferred_fields, strict=False
            )
        ]

        # Resolve the business key: explicit key_field, else the column the
        # model marked identifier; else leave None (assembler will reject).
        key_field = entity_spec.key_field.strip() if entity_spec.key_field else None
        if key_field is not None and key_field not in sheet.columns:
            notes.append(
                f"实体「{entity_spec.name}」的主键列「{key_field}」不存在，"
                "已尝试改用 identifier 列"
            )
            key_field = None
        if key_field is None:
            identifier_specs = [
                spec
                for spec in entity_spec.fields
                if spec.role == "identifier" and spec.column.strip() in sheet.columns
            ]
            if identifier_specs:
                key_field = identifier_specs[0].column.strip()

        # Align the key field's role to identifier in the merged fields.
        if key_field is not None:
            key_pos = sheet.columns.index(key_field)
            key_field_model = merged_fields[key_pos]
            if key_field_model.role != "identifier":
                key_field_model.role = "identifier"  # type: ignore[assignment]
                key_field_model.role_signals = ["llm:promoted_from_key_field"]

        needs_review = (
            entity_spec.needs_review
            or entity_spec.confidence < CONFIDENCE_HIGH
            or key_field is None
        )
        signals = ["llm"]
        if entity_spec.reason:
            signals.append(f"llm_reason:{entity_spec.reason[:80]}")
        if key_field is None:
            signals.append("no_identifier")

        sheet.inferred_fields = merged_fields
        sheet.inferred_entity = InferredEntity(
            source_sheet=sheet.name,
            key=final_key,
            name=entity_spec.name.strip() or sheet.name,
            key_field=key_field,
            confidence=entity_spec.confidence,
            needs_review=needs_review,
            signals=signals,
        )

    for sheet in result.sheets:
        if not sheet.is_empty and sheet.name not in matched_sheets:
            notes.append(f"工作表「{sheet.name}」未被 LLM 识别为实体，保留规则引擎结果")

    # Stash link specs (raw dicts; schemas must stay LLM-package agnostic) for
    # the post-plan stage where columns can be mapped to field keys.
    result.llm_link_specs = [spec.model_dump() for spec in proposal.links]
    result.understanding_engine = "llm"
    return "llm", notes


def build_llm_links(
    specs: list[LLMLinkSpec],
    plans: list[EntityPlan],
) -> tuple[list[InferredLink], list[str]]:
    """Validate LLM link proposals against assembled plans and real data."""
    notes: list[str] = []
    plans_by_key = {plan.key: plan for plan in plans}
    links: list[InferredLink] = []

    for spec in specs:
        from_key = spec.from_entity.strip()
        to_key = spec.to_entity.strip()
        # Tolerate proposals that used a sheet name or a pre-normalization key.
        if from_key not in plans_by_key:
            for plan in plans:
                if plan.sheet.name.strip() == from_key:
                    from_key = plan.key
                    break
        if to_key not in plans_by_key:
            for plan in plans:
                if plan.sheet.name.strip() == to_key:
                    to_key = plan.key
                    break
        parent = plans_by_key.get(from_key)
        child = plans_by_key.get(to_key)
        if parent is None or child is None or parent.key == child.key:
            notes.append(f"LLM 关联「{spec.from_entity}→{spec.to_entity}」实体无法对应，已忽略")
            continue
        if spec.on_from.strip() not in parent.sheet.columns:
            notes.append(f"LLM 关联的主表列「{spec.on_from}」不存在，已忽略该关联")
            continue
        if spec.on_to.strip() not in child.sheet.columns:
            notes.append(f"LLM 关联的子表列「{spec.on_to}」不存在，已忽略该关联")
            continue
        on_from_column = spec.on_from.strip()
        on_to_column = spec.on_to.strip()
        if on_to_column == child.key_field_column:
            notes.append("LLM 关联使用了子表自身主键作为外键，已忽略")
            continue

        field_keys = unique_field_keys(child.sheet.columns)[0]
        on_to_field = dict(zip(child.sheet.columns, field_keys, strict=True))[
            on_to_column
        ]

        # Objective evidence from the actual values, same as rule inference.
        def values_of(plan: EntityPlan, column: str) -> list[str]:
            pos = plan.sheet.columns.index(column)
            out: list[str] = []
            for row in plan.sheet.rows:
                if pos >= len(row):
                    continue
                value = row[pos]
                if value is not None and value.strip():
                    out.append(value.strip())
            return out

        parent_values = values_of(parent, on_from_column)
        child_values = values_of(child, on_to_column)
        parent_set, child_set = set(parent_values), set(child_values)
        overlap = parent_set & child_set
        recall = len(overlap) / len(parent_set) if parent_set else 0.0
        precision = len(overlap) / len(child_set) if child_set else 0.0

        # LLM links are still spreadsheet guesses: always review-gated, and
        # unverified overlaps cannot be presented as high confidence.
        confidence = min(spec.confidence, 0.8 if overlap else 0.6)
        signals = ["llm"]
        if overlap:
            signals.append("value_set_overlap")
        else:
            signals.append("value_set_not_verified")
        links.append(
            InferredLink(
                key=f"{parent.key}_to_{child.key}",
                from_entity=parent.key,
                to_entity=child.key,
                on_from=parent.key_field_key,
                on_to=on_to_field,
                confidence=confidence,
                needs_review=True,
                review_reason=(
                    "LLM 推断的关联，源数据没有外键定义，需人工确认"
                    + ("" if overlap else "（值集合未验证）")
                ),
                signals=signals,
                overlap_recall=round(recall, 4),
                overlap_precision=round(precision, 4),
            )
        )
    return links, notes
