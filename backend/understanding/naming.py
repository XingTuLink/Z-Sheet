"""Deterministic snake_case key generation for Business Model assembly (Day 11).

Domain keys must match ^[a-z][a-z0-9_]*$. Inputs are Chinese business column
names, so V0.1 ships a fixed sales-domain glossary: common columns get stable
English keys matching design example 7.1 (order_no, customer_name, amount...),
unknown columns fall back to their 1-based position. No transliteration
dependency; the fallback guarantees uniqueness and the review queue surfaces
glossary misses for a human to rename.
"""

from __future__ import annotations

# Chinese/English business column glossary for the V0.1 sales scenario.
# Match is exact after stripping whitespace, case-insensitive for ASCII.
FIELD_NAME_TO_KEY: dict[str, str] = {
    "订单编号": "order_no", "订单号": "order_no", "单号": "order_no",
    "客户编号": "customer_id", "客户名称": "customer_name", "客户名": "customer_name",
    "客户": "customer_name",
    "商品编号": "product_id", "产品编号": "product_id",
    "商品名称": "product_name", "产品名称": "product_name", "商品名": "product_name",
    "供应商名称": "supplier_name", "供应商": "supplier_name",
    "编号": "id", "id": "id",
    "名称": "name", "姓名": "name",
    "金额": "amount", "总金额": "amount", "订单金额": "amount",
    "单价": "unit_price", "价格": "price", "售价": "price",
    "数量": "quantity", "库存数量": "stock_quantity", "库存": "stock",
    "下单日期": "order_date", "付款日期": "payment_date",
    "日期": "date", "时间": "time",
    "区域": "region", "地区": "region", "所属区域": "region",
    "状态": "status", "类型": "type", "类别": "category", "分类": "category",
    "性别": "gender", "级别": "level", "等级": "level",
    "负责人": "owner", "联系人": "contact",
    "联系电话": "phone", "电话": "phone", "手机": "phone", "手机号": "phone",
    "地址": "address", "收货地址": "address",
    "备注": "remark", "描述": "description", "说明": "note",
    "规格": "spec", "型号": "model", "单位": "unit", "品牌": "brand",
}


def field_key(column_name: str, position: int) -> tuple[str, bool]:
    """Return (snake_case key, glossary_hit). position is 0-based."""
    normalized = column_name.strip()
    mapped = FIELD_NAME_TO_KEY.get(normalized)
    if mapped is None:
        mapped = FIELD_NAME_TO_KEY.get(normalized.lower())
    if mapped is not None:
        return mapped, True
    return f"field_{position + 1}", False


def unique_field_keys(columns: list[str]) -> tuple[list[str], list[bool]]:
    """Glossary map every column, de-duplicating collisions within one entity."""
    keys: list[str] = []
    hits: list[bool] = []
    used: set[str] = set()
    for position, column in enumerate(columns):
        key, hit = field_key(column, position)
        if key in used:
            suffix = 2
            while f"{key}_{suffix}" in used:
                suffix += 1
            key = f"{key}_{suffix}"
            hit = False  # collision-derived key is not glossary-authoritative
        used.add(key)
        keys.append(key)
        hits.append(hit)
    return keys, hits


def unique_entity_keys(base_keys: list[str]) -> list[str]:
    """Namespace duplicate kinds across sheets: order, then order_2, ..."""
    result: list[str] = []
    used: set[str] = set()
    counts: dict[str, int] = {}
    for base in base_keys:
        if base not in used:
            used.add(base)
            counts[base] = 1
            result.append(base)
            continue
        counts[base] += 1
        candidate = f"{base}_{counts[base]}"
        while candidate in used:
            counts[base] += 1
            candidate = f"{base}_{counts[base]}"
        used.add(candidate)
        result.append(candidate)
    return result
