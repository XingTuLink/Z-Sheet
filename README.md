# Z-Sheet

表格变系统 —— 上传一份 Excel/CSV，AI 将其理解为 Business Model（实体、字段、关系、指标、视图），再由**确定性 Renderer** 生成一个可以直接使用的小系统。数据全部留在你自己的环境中，不依赖任何云账号。

> 状态：实验性开发中（Understanding UI 阶段）。首屏即上传页：上传真实 Excel 后，页面按真实管线阶段展示进度（解析工作表 → 识别字段 / 实体 / 关联 → 生成系统），完成后产出通过领域校验的 Business Model；理解结果的结构化确认页开发中。

## 它如何工作

```text
Excel / CSV
    →  AI 理解（结构化 Business Model，可校验、可版本化）
    →  model.yaml
    →  确定性 Renderer（model + data → UI，渲染过程不调用 AI）
    →  Web App（列表 / 详情 / 表单 / …）
```

模型是唯一事实来源：字段类型、关联、指标与页面视图都声明在 model 中；同一模型永远渲染出同一界面，结果可复现、可测试。

## 当前能力

- Business Model Schema（实体 / 字段 / 关系 / 指标 / 视图）与 YAML 导入、导出
- 模型版本化存储（Alembic 迁移、Snapshot、JSON Patch、三层校验）
- 文件解析 API：`.xlsx`（多 Sheet）与 `.csv`，Sheet 发现、表头行定位、UTF-8/UTF-16/GB18030 编码兼容（暂为无状态接口，尚无上传界面）
- 列画像：空值率、distinct/唯一/重复统计、高频值 Top 5、整行重复数，以及确定性等距采样（为类型推断与预览供数）
- 六类字段确定性类型推断：string / number / money / date / enum / phone，带置信度档位（≥0.85 已识别 / 0.60–0.85 建议确认 / <0.60 无法确定）、判定信号与 enum 候选值；无 AI，规则全部可测
- 六类语义角色推断：identifier / dimension / measure / time / enum / text，独立置信度与审查标记（唯一值+标识列名定 identifier；金额/日期/电话机械映射；词类枚举为维度、代码枚举为 enum；长文本与短标签分列 text/dimension）
- 实体识别：每表产出一个实体候选 customer / order / product（外加无法判定时诚实给 unknown），综合表名关键词与字段构成（订单=标识+流水金额+时间；商品=标识+单价/规格无时间轴；客户=标识+联系方式无流水无时间），名称与结构强冲突时降置信待确认，并给出 key_field
- 一对多关系推断（V0.1 仅此一种）：同名字段 + 值集合召回率/精确率 + 父侧唯一 / 子侧重复四重证据，四档置信度（0.88 / 0.75 / 0.75 异名语义 / 0.63 仅列名疑似外键）；两侧都唯一（一对一）不推断；无源 FK 定义，所有关系一律 `needs_review`，禁止假确定
- Business Model 自动组装：中文列名映射 snake_case（词典外按位置兜底 `field_N`）、同类实体命名空间去重、枚举无候选值安全降级；自动生成流水金额 sum 指标（单价不汇总）与 list / detail / form / dashboard 视图、导航；产物逐字段通过领域层引用完整性校验并可 YAML 导出，能直接走模型导入接口持久化
- 上传落地页与真实分阶段进度（SSE）：点击 / 拖拽上传 `.xlsx` / `.csv`（20 MB 内），后端通过 `POST /api/v1/ingestion/parse/stream` 逐个下发真实阶段事件（各阶段计数均为实际产出，不是等待动画），解析错误以事件形式回传；完成态汇总工作表 / 字段 / 实体 / 关联数量、实体标签与未纳入模型的工作表说明
- 理解结果浏览页（Understanding）：Entity 卡片（实体名 / key / 来源工作表 / 主键高亮）内嵌 Field 卡片（中文字段名、snake_case key、类型与语义角色标签、枚举候选值）；未纳入模型的工作表单独列出并给出跳过原因（空表 / unknown / 缺唯一标识字段）；数据经 sessionStorage 传递，无数据直达时有回上传引导
- 置信度三档与人工审查（设计 10.1/10.2）：每个字段/实体/关联按 ≥0.85 已识别（绿）、0.60–0.85 建议确认（黄）、<0.60 无法确定（红）展示数值与档位；**待确认队列**汇总所有 needs_review 项，字段/实体可逐项确认或撤销，关联可接受 / 拒绝 / 下拉改 on 关联字段；关键口径：Excel 无外键定义，凡是推断关联的子侧连接字段（如订单.客户名称）即使值完全匹配也强制 0.63 + 待确认（"列名一致，但源数据没有外键定义"），禁止假确定；审查决策暂存会话，随 Day 15 Confirm 落库
- 确定性 Renderer 第一版：
  - **List**：按模型声明的列渲染表格
  - **Detail**：字段详情
  - **Form**：按字段类型生成表单与校验（演示环境为页面内数据）
- 单容器交付：FastAPI 同时提供 API 与构建后的前端

尚未提供：审查决策随 Confirm 落库并生成正式应用（Day 15）、列表搜索/筛选/排序/分页、关联列表、业务数据持久化 CRUD、自然语言修改。这些是后续里程碑的内容。

## 快速开始（Docker）

前置：已安装 Docker（含 Compose）。

```bash
docker compose up -d --build
```

打开 <http://localhost:8000> 。首次启动会自动执行数据库迁移并导入内置演示模型（销售管理：客户与订单）。

停止并清理数据卷：

```bash
docker compose down -v
```

## 本地开发

环境要求：Python 3.12、[uv](https://docs.astral.sh/uv/) 0.12、Node 24、pnpm 12。

后端（:8000）：

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn backend.api.main:app --reload
```

前端（:5173，`/api` 已代理到 :8000）：

```bash
cd frontend
pnpm install
pnpm dev
```

打开 <http://localhost:5173> 。首次访问会通过 API 自动导入演示模型。

## 检查与测试

```bash
# 后端
uv run ruff check .
uv run pyright
uv run pytest

# 前端
cd frontend
pnpm lint
pnpm build
```

## 项目结构

```text
backend/domain      Business Model 与 Patch 的领域定义、序列化
backend/understanding  解析后的理解管线：画像 → 类型 → 角色 → 实体 → 关系 → 模型组装
backend/storage     SQLAlchemy 模型、仓储（模型版本）
backend/patch       RFC 6902 子集的确定性 Patch 应用器
backend/runtime     渲染运行时引导（model + data）
backend/api         FastAPI 路由（解析 / 模型管理 / 运行时）
migrations          Alembic 迁移
frontend/src/renderer  类型驱动的确定性渲染（View Resolver + 视图组件）
examples            演示模型与种子数据
docker              镜像与入口脚本
```

## 参与贡献

欢迎 Issue 与 PR，请先阅读 [CONTRIBUTING.md](./CONTRIBUTING.md)。

## 许可证

[Apache License 2.0](./LICENSE)。
