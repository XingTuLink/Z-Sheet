"""Shared Chinese/English column-name keyword groups for understanding rules.

Substring matching, case-insensitive. Kept in one place so type inference
(Day 7-8) and role inference (Day 9) cannot drift apart.
"""

from __future__ import annotations

MONEY_NAME_KEYWORDS = (
    "金额", "价格", "单价", "总价", "费用", "成本", "收入",
    "工资", "薪资", "奖金", "报销", "amount", "price", "cost", "fee",
    "salary", "income", "revenue", "total",
)
DATE_NAME_KEYWORDS = ("日期", "时间", "date", "time", "day")
PHONE_NAME_KEYWORDS = ("电话", "手机", "联系", "phone", "mobile", "tel")
CATEGORY_NAME_KEYWORDS = (
    "状态", "类型", "类别", "分类", "区域", "地区", "级别", "等级", "性别",
    "status", "state", "type", "category", "region", "level", "grade", "gender",
)
IDENTIFIER_NAME_KEYWORDS = (
    "编号", "单号", "编码", "代码", "号", "ID", "Id", "id",
    "no", "code", "name", "名称",
)
LONG_TEXT_NAME_KEYWORDS = (
    "备注", "描述", "说明", "地址", "详情", "内容", "意见", "remark", "note",
    "description", "address", "comment",
)


# --- sheet-name keyword groups for Day 10 entity recognition ----------------
# Compound names such as "客户订单明细" describe a transactional sheet, so the
# order group is tested first by the entity engine.
ORDER_SHEET_KEYWORDS = (
    "订单", "销售", "采购", "出库", "入库", "送货", "发货", "退货", "交易",
    "流水", "明细", "台账", "记录", "order", "sales", "sale", "purchase",
    "transaction", "delivery",
)
CUSTOMER_SHEET_KEYWORDS = (
    "客户", "顾客", "供应商", "供货商", "会员", "员工", "职员", "联系人",
    "商家", "厂商", "customer", "client", "supplier", "vendor", "member",
    "employee", "contact",
)
PRODUCT_SHEET_KEYWORDS = (
    "商品", "产品", "物料", "货品", "货物", "单品", "product", "item",
    "material", "goods", "sku",
)

# Field-level hints used to tell entity kinds apart by field composition.
PRICE_NAME_KEYWORDS = ("单价", "价格", "售价", "price", "cost")
QUANTITY_NAME_KEYWORDS = ("数量", "件数", "quantity", "qty", "count")
CONTACT_NAME_KEYWORDS = (
    "地址", "联系人", "address", "contact",
) + PHONE_NAME_KEYWORDS
PRODUCT_ATTR_KEYWORDS = (
    "规格", "型号", "单位", "库存", "品牌", "条码", "spec", "model",
    "unit", "stock", "brand", "barcode",
)
PARTY_ID_NAME_KEYWORDS = ("名称", "姓名", "name")

# Default worksheet names carry no business evidence ("Sheet1", "表2").
DEFAULT_SHEET_NAME_RE_PARTS = (r"sheet\s*\d*", r"worksheet\s*\d*", r"表\d*")

ENTITY_KIND_LABELS = {
    "order": "订单",
    "customer": "客户",
    "product": "商品",
    "unknown": "未命名实体",
}


def has_keyword(name: str, keywords: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(keyword.lower() in lowered for keyword in keywords)
