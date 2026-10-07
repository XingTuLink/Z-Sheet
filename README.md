# Z-Sheet

表格变系统 —— 上传一份 Excel/CSV，AI 将其理解为 Business Model（实体、字段、关系、指标、视图），再由**确定性 Renderer** 生成一个可以直接使用的小系统。数据全部留在你自己的环境中，不依赖任何云账号。

> 状态：实验性开发中（Foundation 阶段）。当前里程碑：内置演示模型可直接渲染为可浏览的 Web 应用。

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
- 确定性 Renderer 第一版：
  - **List**：按模型声明的列渲染表格
  - **Detail**：字段详情
  - **Form**：按字段类型生成表单与校验（演示环境为页面内数据）
- 单容器交付：FastAPI 同时提供 API 与构建后的前端

尚未提供：上传界面与 Excel → Business Model 的自动理解（解析之后的字段类型推断、实体/关系识别）、列表搜索/筛选/排序/分页、关联列表、数据持久化 CRUD、自然语言修改。这些是后续里程碑的内容。

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
backend/storage     SQLAlchemy 模型、仓储（模型版本）
backend/patch       RFC 6902 子集的确定性 Patch 应用器
backend/runtime     渲染运行时引导（model + data）
backend/api         FastAPI 路由（模型管理 / 运行时）
migrations          Alembic 迁移
frontend/src/renderer  类型驱动的确定性渲染（View Resolver + 视图组件）
examples            演示模型与种子数据
docker              镜像与入口脚本
```

## 参与贡献

欢迎 Issue 与 PR，请先阅读 [CONTRIBUTING.md](./CONTRIBUTING.md)。

## 许可证

[Apache License 2.0](./LICENSE)。
