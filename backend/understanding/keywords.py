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


def has_keyword(name: str, keywords: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(keyword.lower() in lowered for keyword in keywords)
