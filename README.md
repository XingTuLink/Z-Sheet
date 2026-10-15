# Z-Sheet

**表格变系统** —— 上传一份 Excel/CSV，AI 将其理解为 Business Model（实体、字段、关系、指标、视图），再由**确定性 Renderer** 生成一个可以直接使用的小系统。模型是唯一事实来源，渲染过程不调用 AI，结果可复现、可测试。

> **状态：0.1.0-alpha.1 — Experimental（实验性）。** 核心闭环（Excel → AI 理解 → 人工审查 → 可用系统 → 自然语言持续修改）已跑通并内置故障降级；面向早期试用者，接口与界面仍可能变化。详见 [CHANGELOG.md](./CHANGELOG.md) 与 [ROADMAP.md](./ROADMAP.md)。

## 它如何工作

```text
Excel / CSV
    →  AI 理解（结构化 Business Model，可校验、可版本化）
    →  model.yaml
    →  确定性 Renderer（model + data → UI，渲染过程不调用 AI）
    →  Web App（列表 / 详情 / 表单 / Dashboard / 自然语言修改）
```

AI 是可选的：不配置任何大模型时，系统以内置**规则引擎**运行（擅长标准销售/订单/商品台账）；配置任意 **OpenAI 兼容接口**（公有云或本地 Ollama / vLLM）后，LLM 负责理解任意业务表，规则引擎同时作为其输出的硬校验器与故障兜底——AI 超时、限流或返回非法内容时自动重试、自动降级，解析与使用不会中断。

## 当前能力（0.1.0-alpha.1）

**从文件到系统**

- `.xlsx`（多 Sheet）/ `.csv` 解析：表头行定位、UTF-8/UTF-16/GB18030 编码兼容、标点伪行等脏数据治理，20 MB 上限
- 列画像（空值率、distinct/唯一/重复、高频值、确定性等距采样）与六类字段类型推断（string / number / money / date / enum / phone）、六类语义角色推断，全部带置信度与待确认标记
- 实体识别与一对多关系推断：四重证据 + 四档置信度，无外键定义的关联一律进人工审查，禁止假确定
- 上传页 SSE 分阶段真实进度；Understanding 页面逐字段/实体/关联审查（接受、拒绝、改关联字段、一键确认待处理项）
- Confirm 全链路：服务端重跑管线（不采信客户端推断）→ 决策重组模型 → 领域全量校验 → 版本落库 → 数据按类型规整入库

**AI 理解层（可选，OpenAI 兼容）**

- 统一 Provider：默认 DeepSeek 公有云，环境变量一键切换 Ollama / vLLM 等本地引擎（本地引擎无需 Key）
- LLM 提案经真实工作表硬校验：列必须存在、类型/关系必须合法，漏列自动回退规则推断，非法/低置信项进审查队列
- **韧性设计**：超时分类、5xx/429/坏输出小预算自动重试（`ZSHEET_LLM_MAX_RETRIES`），重试耗尽自动降级为规则引擎，降级事件写入日志与页面说明；日志中的 API Key 一律掩码

**生成的应用（确定性 Renderer）**

- List：跨字段搜索（多词 AND、中英端子串）、多选过滤（含空桶）、三态排序、分页
- Detail：字段详情 + 一对多关联列表，可下钻子记录
- Form / CRUD：类型化表单、必填与前后端校验、业务键编辑锁定、持久化
- Dashboard：指标卡、月度趋势、枚举分布（零图表库依赖）

**自然语言修改（NL Patch）**

- 一句话修改系统：加/删/改字段、改实体名等高风险操作生成结构化意图与中文变更说明
- 预览 → 确认/拒绝的防篡改会话（补丁不出服务端、1 小时过期、版本漂移 409）
- **撤销与回滚**：undo 撤销最新修改、rollback 回到任意历史版本；版本链 append-only，历史从不改写，回滚后可继续修改

**交付与运维**

- 单容器（FastAPI 同时提供 API 与构建后的 SPA）、Alembic 自动迁移、SQLite 数据卷、容器健康检查
- 内置演示系统（销售管理）与 4 份带瑕疵的真实感演示数据集（`examples/demo_data/`）

**暂不提供（V0.1 禁区）**：数据库直连、多用户权限、工作流/审批/通知、移动端、拖拽式低代码、复杂 BI、Agent、插件生态、Excel 双向同步、多对多关系。见 [ROADMAP.md](./ROADMAP.md)。

## 快速开始（Docker，推荐）

前置：已安装 Docker（含 Compose 插件）。Windows / macOS 用 Docker Desktop，Linux 用 Docker Engine + Compose。

**1. 准备 LLM 配置（可选但推荐）**

```bash
cp .env.example .env
# 编辑 .env：
#   - 有 DeepSeek Key：直接填 ZSHEET_LLM_API_KEY 即可（默认已是云端配置）
#   - 想用本地 Ollama：改 BASE_URL / MODEL，API_KEY 留空（详见 .env 内注释）
#   - 不想配置 LLM：跳过本步，系统以规则引擎运行
```

**2. 构建并启动**

```bash
docker compose up -d --build
```

打开 <http://localhost:8000> 。容器启动时自动执行数据库迁移；首次打开应用时会自动导入内置演示系统（客户 / 订单），也可直接上传自己的 Excel。

健康检查：`GET http://localhost:8000/api/v1/health`（返回版本号与数据库状态）。

停止；加 `-v` 同时清空数据卷：

```bash
docker compose down        # 停止，保留数据
docker compose down -v     # 停止并清空全部数据
```

> Linux 本地引擎：把 `.env` 里的 Ollama 地址换成 `http://localhost:11434/v1` 并注意 Ollama 默认只监听 127.0.0.1；容器访问宿主机统一用 `host.docker.internal`（Linux 需 Compose v2.3+ 或添加 `--add-host`）。

## 本地开发

环境要求：Python 3.12、[uv](https://docs.astral.sh/uv/) 0.12、Node 24、pnpm 12。Windows / Linux / macOS 均可开发。

后端（:8000）：

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn backend.api.main:app --reload
```

前端（:5173，`/api` 已代理到 :8000；5173 被占用时 Vite 会自动改用 5174，仍走代理）：

```bash
cd frontend
pnpm install
pnpm dev
```

打开 <http://localhost:5173> 。LLM 配置同样通过仓库根目录的 `.env`（或 `ZSHEET_*` 环境变量）生效；不配置即规则引擎模式，全部离线测试均在此模式运行。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ZSHEET_LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容接口地址（`/chat/completions`） |
| `ZSHEET_LLM_MODEL` | `deepseek-flash` | 模型名（Ollama 如 `qwen2.5:7b`） |
| `ZSHEET_LLM_API_KEY` | _空_ | API Key；本地引擎留空，留空且为云端地址时不调用 LLM |
| `ZSHEET_LLM_TIMEOUT` | `60` | 单次请求超时秒数 |
| `ZSHEET_LLM_MAX_RETRIES` | `1` | 失败后额外重试次数（0 = 不重试） |
| `ZSHEET_LLM_ENABLE_THINKING` | `true` | 仅 DeepSeek 官方端点发送 thinking 参数 |
| `ZSHEET_LOG_LEVEL` | `INFO` | 日志级别（DEBUG/INFO/WARNING/ERROR） |
| `ZSHEET_DATA_DIR` | `./data` | SQLite 与数据目录（容器内固定 `/app/data`） |
| `ZSHEET_CORS_ORIGINS` | 本地 5173 | JSON 数组，允许跨域访问 API 的来源 |

## 检查与测试

```bash
# 后端
uv run ruff check .
uv run pyright
uv run pytest

# 前端
cd frontend
pnpm test
pnpm lint
pnpm build
```

## 项目结构

```text
backend/ai            OpenAI 兼容 LLM Provider（超时分类、重试、Key 掩码）
backend/domain        Business Model 与 Patch 的领域定义、序列化
backend/understanding  理解管线：画像 → 类型 → 角色 → 实体 → 关系 → LLM 提案校验 → 组装
backend/patch         RFC 6902 子集的确定性 Patch 应用器 + NL 意图解析/生成
backend/storage       SQLAlchemy 模型、仓储（模型版本与 app_data 运行时数据）
backend/runtime       渲染运行时引导（model + data）
backend/api           FastAPI 路由（解析 / 模型管理 / Patch 会话 / 运行时）
migrations            Alembic 迁移
frontend/src/renderer 类型驱动的确定性渲染（View Resolver + 视图组件）
examples              演示模型、种子数据与 4 份演示 Excel 数据集
docker                镜像与入口脚本
```

## 参与贡献

欢迎 Issue 与 PR，请先阅读 [CONTRIBUTING.md](./CONTRIBUTING.md)；后续计划见 [ROADMAP.md](./ROADMAP.md)，版本历史见 [CHANGELOG.md](./CHANGELOG.md)。

## 许可证

[GNU Affero General Public License v3.0](./LICENSE)（AGPL-3.0）。
