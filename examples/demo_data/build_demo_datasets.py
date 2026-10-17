"""Build the Day 28 realistic demo datasets (Internal Alpha fixtures).

Four deliberately "real-world messy" workbooks that every Z-Sheet acceptance
run must understand:

1. 2026年销售台账.xlsx — 客户 / 订单 / (空说明页)：横幅标题行、空行、
   "."/"-" 标点伪行、合计行、¥/千分位脏金额、斜杠与中文日期、枚举值
   首尾空白、客户-订单一对多关联。
2. 2026库存管理.xlsx — 商品 / 入库记录：目录价与流水金额、数量列、
   缺失供应商单元格、小计行、商品-入库一对多关联。
3. 2026客户跟进记录.xlsx — 客户档案 / 跟进记录：长文本备注、多种跟进
   方式枚举、混杂日期格式（含 2026年9月12日）、空下次跟进日期、
   "/"伪行、客户-跟进记录一对多关联。
4. 外审人员登记表.xlsx — 非销售类单表人员登记：序号流水列（不可做主键）、
   姓名业务主键、性别枚举、身份证场景下的唯一电话/邮箱、缺失电话、
   "."伪行。规则引擎只能给出低置信候选，需 LLM 才能正确理解。

Run ``python examples/demo_data/build_demo_datasets.py`` to (re)write the
four ``.xlsx`` files next to this script. The acceptance tests import this
module directly, so the on-disk files and the tested bytes never drift.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook

Row = list[object]


@dataclass(frozen=True)
class SheetSpec:
    name: str
    rows: list[Row]


@dataclass(frozen=True)
class DatasetSpec:
    file_name: str
    sheets: list[SheetSpec]

    def sheet(self, name: str) -> SheetSpec:
        return next(sheet for sheet in self.sheets if sheet.name == name)


# --- 1. 销售台账 ----------------------------------------------------------------------

SALES_LEDGER = DatasetSpec(
    file_name="2026年销售台账.xlsx",
    sheets=[
        SheetSpec(
            name="客户",
            rows=[
                ["2026年度客户台账（华东大区）"],
                [],
                ["客户名称", "所属区域", "负责人", "联系电话", "客户等级"],
                ["示例科技A", "华东", "王敏", "13800000001", "A级"],
                ["示例贸易B", "华南", "李强", "13800000002", "B级"],
                ["示例信息C", "华北", "张伟", "13800000003", "A级"],
                ["示例零售D", "西南", "陈晓", "13800000004", "C级"],
                ["示例制造E", "华东", "周洁", "13800000005", "B级"],
                # 标点伪行：只有最后一列有一个 "."
                [None, None, None, None, "."],
                # 真实脏数据：枚举值带首尾空白（解析器 trim 后仍是「华南」）
                ["示例商贸F", " 华南 ", "李强", "13800000006", "B级"],
            ],
        ),
        SheetSpec(
            name="订单",
            rows=[
                ["订单编号", "客户名称", "金额", "下单日期", "状态"],
                ["SO-2026-001", "示例科技A", "¥12,800.00", "2026/1/8", "已完成"],
                ["SO-2026-002", "示例贸易B", 8600, "2026-01-15", "已完成"],
                ["SO-2026-003", "示例科技A", "15,300.50", "2026/2/3", "进行中"],
                ["SO-2026-004", "示例信息C", 7200, "2026-02-20", "已取消"],
                ["SO-2026-005", "示例零售D", 4300.5, "2026/3/11", "已完成"],
                [],
                ["-", "-", "-", "-", "-"],
                [None, None, "合计", 48200.5, None],
            ],
        ),
        SheetSpec(name="说明", rows=[]),
    ],
)

# --- 2. 库存管理 ----------------------------------------------------------------------

INVENTORY = DatasetSpec(
    file_name="2026库存管理.xlsx",
    sheets=[
        SheetSpec(
            name="商品",
            rows=[
                ["商品编码", "商品名称", "分类", "规格", "单位", "单价", "安全库存"],
                ["SKU-1001", "无尘布", "耗材", "30cm/包", "包", 25.00, 50],
                ["SKU-1002", "防静电手环", "耗材", "可调式", "个", 18.5, 100],
                ["SKU-1003", "标签打印机", "设备", "203dpi", "台", 899, 5],
                ["SKU-1004", "条码纸", "耗材", "60*40mm", "卷", 42, 80],
                [None, None, None, None, None, None, "."],
                ["SKU-1005", "打包胶带", "耗材", "48mm", "卷", 6.8, 200],
            ],
        ),
        SheetSpec(
            name="入库记录",
            rows=[
                ["入库单号", "商品名称", "入库数量", "入库单价", "入库金额",
                 "入库日期", "供应商"],
                ["RK-2026-001", "无尘布", 20, 22.5, 450, "2026/2/1", "示例包装厂"],
                ["RK-2026-002", "标签打印机", 2, 850, 1700, "2026-02-10",
                 "示例办公设备"],
                # 供应商缺失：真实空值
                ["RK-2026-003", "条码纸", 50, 38, 1900, "2026/3/5", None],
                ["RK-2026-004", "打包胶带", 100, 6.2, 620, "2026-03-18",
                 "示例包装厂"],
                [None, None, None, None, "小计", 4670, None],
            ],
        ),
    ],
)

# --- 3. 客户跟进 ----------------------------------------------------------------------

CUSTOMER_FOLLOWUP = DatasetSpec(
    file_name="2026客户跟进记录.xlsx",
    sheets=[
        SheetSpec(
            name="客户档案",
            rows=[
                ["客户名称", "客户等级", "行业", "联系电话", "负责人"],
                ["示例地产A", "A级", "房地产", "13800000001", "赵磊"],
                ["示例餐饮A", "B级", "餐饮", "13800000002", "孙莉"],
                # 联系电话缺失
                ["示例教育A", "C级", "教育", None, "王芳"],
                ["示例医疗A", "A级", "医疗", "13800000004", "赵磊"],
                [".", None, None, None, None],
                ["示例传媒A", "B级", "广告", "13800000005", "钱进"],
            ],
        ),
        SheetSpec(
            name="跟进记录",
            rows=[
                ["记录编号", "客户名称", "跟进方式", "跟进内容",
                 "跟进日期", "下次跟进日期"],
                ["GJ-001", "示例地产A", "电话",
                 "沟通Q4采购预算，客户反馈本周内部评审", "2026/9/2", "2026-09-09"],
                ["GJ-002", "示例餐饮A", "微信",
                 "发送产品报价单，等待客户确认", "2026-09-05", None],
                ["GJ-003", "示例地产A", "上门拜访",
                 "现场演示系统，参会3人", "2026年9月12日", "2026/9/20"],
                ["GJ-004", "示例医疗A", "电话",
                 "确认合同条款，法务审核中", "2026-09-18", "2026-10-08"],
                ["/", "-", "-", "-", "-", "-"],
                ["GJ-005", "示例传媒A", "微信",
                 "展会现场初步建立联系", "2026/9/25", None],
            ],
        ),
    ],
)

# --- 4. 非销售类：外审人员登记 ----------------------------------------------------------

REVIEW_STAFF = DatasetSpec(
    file_name="外审人员登记表.xlsx",
    sheets=[
        SheetSpec(
            name="外审人员登记",
            rows=[
                ["序号", "姓名", "性别", "所属机构", "职务",
                 "联系电话", "邮箱", "登记日期"],
                [1, "郭婉莹", "女", "示例会计师事务所", "高级审核员",
                 "13800000001", "guowy@example.cn", "2026-09-01"],
                [2, "李明轩", "男", "示例评估公司", "项目经理",
                 "13800000002", "limx@example.cn", "2026-09-03"],
                # 联系电话缺失
                [3, "王雅静", "女", "示例咨询公司", "顾问",
                 None, "wangyj@example.cn", "2026/9/5"],
                [None, None, None, ".", None, None, None, None],
                [4, "陈浩然", "男", "示例会计师事务所", "审核员",
                 "13800000004", "chenhr@example.cn", "2026年9月10日"],
                [5, "赵梓涵", "女", "示例检测公司", "技术专家",
                 "13800000005", "zhaozh@example.cn", "2026-09-12"],
            ],
        ),
    ],
)

DATASETS: tuple[DatasetSpec, ...] = (
    SALES_LEDGER,
    INVENTORY,
    CUSTOMER_FOLLOWUP,
    REVIEW_STAFF,
)


def dataset_bytes(dataset: DatasetSpec) -> bytes:
    """Serialize a dataset to in-memory .xlsx bytes (identical to disk)."""
    workbook = Workbook()
    workbook.remove(workbook.worksheets[0])
    for spec in dataset.sheets:
        worksheet = workbook.create_sheet(spec.name)
        for row in spec.rows:
            worksheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def write_all(output_dir: Path | None = None) -> list[Path]:
    """Write every dataset .xlsx next to this script (or into output_dir)."""
    target = output_dir or Path(__file__).resolve().parent
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for dataset in DATASETS:
        path = target / dataset.file_name
        path.write_bytes(dataset_bytes(dataset))
        written.append(path)
    return written


if __name__ == "__main__":
    for path in write_all():
        print(f"wrote {path}")
