"""Build the realistic demo datasets (Internal Alpha fixtures).

Ten deliberately "real-world messy" workbooks that every Z-Sheet acceptance
run must understand. The first four are the original sales-domain fixtures;
the remaining six broaden the demo beyond sales (procurement, assets,
projects, production, HR, logistics) so multi-file imports get varied input:

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
5. 2026采购订单.xlsx — 供应商 / 采购订单：供应商等级枚举、合计行、
   同一供应商多张订单的一对多关联。
6. 固定资产台账.xlsx — 单表资产台账：使用部门纵向合并单元格（锚点只在
   顶行）、原值脏金额、中文日期、"."伪行。
7. 2026项目进度.xlsx — 项目 / 任务：项目状态与任务状态枚举、完成进度
   数值列、混杂日期、同一项目多条任务的一对多关联、小计行。
8. 2026生产工单.xlsx — 单表生产工单：计划/完成数量、进行中工单完工
   日期为受控缺失标记（not provided）、合计行、"-"伪行。
9. 员工花名册.xlsx — 单表员工名册：工号主键、性别与在职状态枚举、
   中文入职日期、缺失手机、"."伪行。
10. 2026物流运单.xlsx — 单表物流运单：重量数值列、运费脏金额、中文日期、
    签收状态枚举、小计行。

Run ``python examples/demo_data/build_demo_datasets.py`` to (re)write the
``.xlsx`` files next to this script. The acceptance tests import this module
directly, so the on-disk files and the tested bytes never drift.
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
    # A1-style ranges merged after the rows are written (real workbooks carry
    # vertical category merges whose value only sits in the anchor cell).
    merges: tuple[str, ...] = ()


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

# --- 5. 采购订单 ----------------------------------------------------------------------

PURCHASE_ORDERS = DatasetSpec(
    file_name="2026采购订单.xlsx",
    sheets=[
        SheetSpec(
            name="供应商",
            rows=[
                ["供应商名称", "联系人", "联系电话", "供应商等级", "所在城市"],
                ["示例材料A", "郑强", "13800000001", "A级", "西安"],
                ["示例电子B", "刘洋", "13800000002", "B级", "深圳"],
                ["示例包装C", "孙海", "13800000003", "C级", "郑州"],
                # 标点伪行：只有最后一列有一个 "."
                [None, None, None, None, "."],
                ["示例物流D", "吴刚", "13800000004", "B级", "成都"],
            ],
        ),
        SheetSpec(
            name="采购订单",
            rows=[
                ["采购单号", "供应商名称", "采购金额", "采购日期", "状态", "采购员"],
                ["PO-2026-001", "示例材料A", "¥18,500.00", "2026/1/20", "已入库",
                 "赵磊"],
                ["PO-2026-002", "示例电子B", 12600, "2026-02-05", "已入库", "赵磊"],
                ["PO-2026-003", "示例材料A", "9,800.50", "2026/2/18", "采购中",
                 "孙莉"],
                ["PO-2026-004", "示例包装C", 5400, "2026年3月2日", "已入库",
                 "钱进"],
                [],
                ["-", "-", "-", "-", "-", "-"],
                [None, None, "合计", 46200.5, None, None],
            ],
        ),
    ],
)

# --- 6. 固定资产台账 ------------------------------------------------------------------

FIXED_ASSETS = DatasetSpec(
    file_name="固定资产台账.xlsx",
    sheets=[
        SheetSpec(
            name="资产台账",
            rows=[
                ["资产编号", "资产名称", "类别", "规格型号", "原值",
                 "购置日期", "使用部门", "保管人", "状态"],
                ["FA-0001", "激光打印机", "办公设备", "HP-M405", "¥3,200.00",
                 "2024/3/12", "示例研发部", "王敏", "在用"],
                # 使用部门纵向合并：覆盖行本身为空，由解析器向下填充
                ["FA-0002", "服务器", "IT设备", "Dell-R750", "¥42,000.00",
                 "2024-04-08", None, "王敏", "在用"],
                ["FA-0003", "办公桌椅", "家具", "标准四件套", "6,800",
                 "2024/5/20", "示例行政部", "李强", "在用"],
                [None, None, None, None, None, None, None, None, "."],
                ["FA-0004", "投影仪", "办公设备", "Epson-CB", "¥8,900.00",
                 "2026年1月5日", "示例研发部", "周洁", "维修中"],
                ["FA-0005", "空调", "电器", "格力-3P", 4500,
                 "2026/2/14", "示例行政部", "李强", "在用"],
            ],
            merges=("G2:G3",),
        ),
    ],
)

# --- 7. 项目进度 ----------------------------------------------------------------------

PROJECT_PROGRESS = DatasetSpec(
    file_name="2026项目进度.xlsx",
    sheets=[
        SheetSpec(
            name="项目",
            rows=[
                ["项目编号", "项目名称", "负责人", "开始日期", "结束日期",
                 "项目状态", "预算金额"],
                ["PRJ-2026-01", "官网改版", "张伟", "2026/1/10", "2026/4/30",
                 "进行中", "¥120,000.00"],
                ["PRJ-2026-02", "数据中台", "李强", "2026-02-01", "2026-06-30",
                 "进行中", 380000],
                ["PRJ-2026-03", "移动端App", "陈晓", "2026/3/15", "2026/5/20",
                 "已完成", "¥85,000.00"],
                [".", None, None, None, None, None, None],
                ["PRJ-2026-04", "仓储系统", "周洁", "2026年4月1日", "2026/8/31",
                 "暂停", "150,000"],
            ],
        ),
        SheetSpec(
            name="任务",
            rows=[
                ["任务编号", "项目名称", "任务名称", "负责人", "完成进度",
                 "计划完成日期", "状态"],
                ["TK-001", "官网改版", "需求评审", "张伟", 100, "2026/1/20",
                 "已完成"],
                ["TK-002", "官网改版", "UI设计", "王敏", 80, "2026-02-15",
                 "进行中"],
                ["TK-003", "数据中台", "架构设计", "李强", 60, "2026/3/1",
                 "进行中"],
                ["TK-004", "移动端App", "上线部署", "陈晓", 100, "2026/5/15",
                 "已完成"],
                [None, None, None, None, "小计", None, None],
                ["TK-005", "数据中台", "数据接入", "周洁", 30, "2026年6月1日",
                 "进行中"],
            ],
        ),
    ],
)

# --- 8. 生产工单 ----------------------------------------------------------------------

PRODUCTION_ORDERS = DatasetSpec(
    file_name="2026生产工单.xlsx",
    sheets=[
        SheetSpec(
            name="生产工单",
            rows=[
                ["工单编号", "产品名称", "计划数量", "完成数量", "工单状态",
                 "开工日期", "完工日期", "生产线"],
                ["WO-2026-001", "无尘布", 500, 500, "已完工", "2026/1/5",
                 "2026/1/12", "一线"],
                # 受控缺失标记：未完工工单的完工日期归一为空值
                ["WO-2026-002", "防静电手环", 800, 620, "生产中", "2026-01-20",
                 "not provided", "二线"],
                ["WO-2026-003", "标签打印机", 50, 50, "已完工", "2026/2/2",
                 "2026/2/10", "一线"],
                ["WO-2026-004", "条码纸", 2000, 1500, "生产中", "2026年3月1日",
                 None, "三线"],
                [],
                ["-", "-", "-", "-", "-", "-", "-", "-"],
                [None, None, "合计", 2670, None, None, None, None],
            ],
        ),
    ],
)

# --- 9. 员工花名册 --------------------------------------------------------------------

EMPLOYEE_ROSTER = DatasetSpec(
    file_name="员工花名册.xlsx",
    sheets=[
        SheetSpec(
            name="员工名册",
            rows=[
                ["工号", "姓名", "性别", "部门", "职位", "入职日期", "手机",
                 "邮箱", "在职状态"],
                ["E0001", "王敏", "女", "研发部", "工程师", "2022/3/1",
                 "13800000001", "wangm@example.cn", "在职"],
                ["E0002", "李强", "男", "研发部", "技术经理", "2020-06-15",
                 "13800000002", "liq@example.cn", "在职"],
                ["E0003", "张伟", "男", "市场部", "销售代表", "2023年2月10日",
                 "13800000003", "zhangw@example.cn", "离职"],
                # 手机缺失
                ["E0004", "陈晓", "女", "市场部", "销售代表", "2021/9/1",
                 None, "chenx@example.cn", "在职"],
                [None, None, ".", None, None, None, None, None, None],
                ["E0005", "周洁", "女", "行政部", "行政专员", "2024/4/8",
                 "13800000005", "zhouj@example.cn", "在职"],
            ],
        ),
    ],
)

# --- 10. 物流运单 ---------------------------------------------------------------------

LOGISTICS_WAYBILLS = DatasetSpec(
    file_name="2026物流运单.xlsx",
    sheets=[
        SheetSpec(
            name="运单",
            rows=[
                ["运单号", "寄件客户", "收件城市", "重量", "运费金额",
                 "发货日期", "签收状态", "承运商"],
                ["YT-2026-0001", "示例科技A", "上海", 12.5, "¥180.00",
                 "2026/1/8", "已签收", "示例快运"],
                ["YT-2026-0002", "示例贸易B", "广州", 8, "120",
                 "2026-01-15", "已签收", "示例快运"],
                ["YT-2026-0003", "示例科技A", "北京", 25, "¥360.00",
                 "2026/2/3", "运输中", "示例物流"],
                ["YT-2026-0004", "示例信息C", "成都", 3.2, "88.5",
                 "2026年2月20日", "已签收", "示例物流"],
                [],
                [None, None, "小计", None, 748.5, None, None, None],
                ["YT-2026-0005", "示例零售D", "武汉", 15, "210",
                 "2026/3/11", "已退回", "示例快运"],
            ],
        ),
    ],
)

DATASETS: tuple[DatasetSpec, ...] = (
    SALES_LEDGER,
    INVENTORY,
    CUSTOMER_FOLLOWUP,
    REVIEW_STAFF,
    PURCHASE_ORDERS,
    FIXED_ASSETS,
    PROJECT_PROGRESS,
    PRODUCTION_ORDERS,
    EMPLOYEE_ROSTER,
    LOGISTICS_WAYBILLS,
)


def dataset_bytes(dataset: DatasetSpec) -> bytes:
    """Serialize a dataset to in-memory .xlsx bytes (identical to disk)."""
    workbook = Workbook()
    workbook.remove(workbook.worksheets[0])
    for spec in dataset.sheets:
        worksheet = workbook.create_sheet(spec.name)
        for row in spec.rows:
            worksheet.append(row)
        for merged_range in spec.merges:
            worksheet.merge_cells(merged_range)
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
