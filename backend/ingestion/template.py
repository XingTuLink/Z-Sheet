"""Downloadable standard workbook template (Day 15 follow-up).

When the understanding pipeline honestly reports ``unknown`` for every
sheet (no customer/product/order-style business entity), the UI offers this
template so users can see the exact structure Z-Sheet recognizes: one header
row, a real business key per sheet, no title rows / merged cells / totals.

The template ships pre-filled with valid example rows — it must itself pass
the full understanding pipeline (entity recognition + link inference), so it
doubles as executable documentation of the expected input shape.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

import openpyxl
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

TEMPLATE_FILENAME = "Z-Sheet标准模板.xlsx"

# openpyxl cell values used in the template are plain text or numbers.
Row = Sequence[str | int]

# (sheet name, header, rows). Kept in sync with the known-good regression
# fixture shape: 6 customers / 3 products / 8 orders, repeated enum values.
_CUSTOMER_HEADER = ["客户名称", "区域", "联系电话"]
_CUSTOMER_ROWS = [
    ["杭州云栖", "华东", "13800000001"],
    ["深圳前海", "华南", "13800000002"],
    ["北京中关村", "华北", "13800000003"],
    ["广州天河", "华南", "13800000004"],
    ["成都高新", "华东", "13800000005"],
    ["武汉光谷", "华北", "13800000006"],
]

_PRODUCT_HEADER = ["商品名称", "单价"]
_PRODUCT_ROWS = [
    ["云主机", 199],
    ["对象存储", 49],
    ["带宽包", 89],
]

_ORDER_HEADER = ["订单编号", "客户名称", "商品名称", "金额", "下单日期"]
_ORDER_ROWS = [
    ["SO-1001", "杭州云栖", "云主机", 199, "2026-01-03"],
    ["SO-1002", "深圳前海", "云主机", 199, "2026-01-05"],
    ["SO-1003", "北京中关村", "带宽包", 89, "2026-01-08"],
    ["SO-1004", "广州天河", "对象存储", 49, "2026-01-09"],
    ["SO-1005", "成都高新", "云主机", 199, "2026-02-01"],
    ["SO-1006", "武汉光谷", "带宽包", 89, "2026-02-02"],
    ["SO-1007", "杭州云栖", "对象存储", 49, "2026-02-03"],
    ["SO-1008", "深圳前海", "云主机", 199, "2026-02-04"],
]


def build_template_workbook() -> io.BytesIO:
    """Build the standard .xlsx template with valid example data."""
    wb = openpyxl.Workbook()
    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="EAF4FF")

    def fill_sheet(
        ws: Worksheet,
        header: Sequence[str],
        rows: Sequence[Row],
    ) -> None:
        ws.append(list(header))
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
        for row in rows:
            ws.append(list(row))
        ws.freeze_panes = "A2"
        for index, column_cells in enumerate(ws.columns, start=1):
            width = max(
                len(str(cell.value)) for cell in column_cells if cell.value is not None
            )
            ws.column_dimensions[get_column_letter(index)].width = min(width + 4, 40)

    customers = wb.worksheets[0]
    customers.title = "客户"
    fill_sheet(customers, _CUSTOMER_HEADER, _CUSTOMER_ROWS)

    fill_sheet(wb.create_sheet("商品"), _PRODUCT_HEADER, _PRODUCT_ROWS)
    fill_sheet(wb.create_sheet("订单"), _ORDER_HEADER, _ORDER_ROWS)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
