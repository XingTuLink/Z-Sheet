# 更新日志（Changelog）

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)（0.x 阶段任何接口都可能变化）。

## [0.1.0-alpha.1] - 2026-10-13

首个实验性公开版本（Experimental）。核心闭环——**Excel/CSV → AI 理解 → 人工审查 → 可用业务系统 → 自然语言持续修改**——全部跑通，支持 Docker 单容器交付，LLM 支持公有云与本地引擎切换，外部依赖故障可重试、可降级。

### 新增

- **文件理解全链路**：`.xlsx`（多 Sheet）/ `.csv`（CSV 含 UTF-8/UTF-16/GB18030）解析、表头行定位、20 MB 上限；列画像（空值/distinct/唯一/重复/高频值/等距采样）；六类字段类型（string/number/money/date/enum/phone）与六类语义角色（identifier/dimension/measure/time/enum/text）的确定性推断，全部带置信度档位与待确认标记
- **实体与关系推断**：客户/订单/商品/人员等实体识别、一对多关系四重证据推断与四档置信度；无外键定义的关联强制人工审查
- **LLM 理解层**：OpenAI 兼容 Provider（默认 DeepSeek `deepseek-flash`，可切换 Ollama / vLLM，本地引擎免 Key）；LLM 提案按真实工作表硬校验（列存在性、类型/关系合法性），漏列回退规则推断，非法与低置信项进审查队列
- **审查与确认**：SSE 分阶段真实进度上传页、Understanding 审查页（字段/实体/关联逐项接受、拒绝、改关联字段、一键确认）、Confirm 防伪造会话（服务端重跑管线 + 文件 hash 会话）
- **确定性 Renderer**：List（搜索/多选过滤/三态排序/分页）、Detail（含一对多关联下钻）、类型化 Form 与 CRUD 持久化、Dashboard（指标卡/月趋势/枚举分布，无图表库依赖）
- **自然语言修改（NL Patch）**：一句话生成加/删/改字段等结构化意图、中文变更说明、高风险操作确认门；防篡改预览会话（1 小时过期、版本漂移 409）；undo 撤销与任意版本 rollback，append-only 版本链回滚后仍可继续修改
- **模型管理**：Business Model YAML 导入/导出、Alembic 迁移、Snapshot + RFC 6902 子集 JSON Patch 三层校验
- **交付物料**：多阶段单容器镜像（自动迁移、健康检查、非 root 用户、SQLite 数据卷）、docker compose、内置销售管理演示系统、4 份带瑕疵真实感演示数据集（销售台账/库存/客户跟进/人员登记）
- **可观测性**：统一 `zsheet` 日志体系（`ZSHEET_LOG_LEVEL`），记录启动配置（Key 掩码）、LLM 重试与降级、补丁应用/撤销/回滚、SSE worker 异常；任何日志不落 API Key

### 变更（韧性与安全）

- LLM 超时、5xx/429、连接错误与非法 JSON 输出按小预算自动重试（`ZSHEET_LLM_MAX_RETRIES`，默认 1 次），线性退避；401/403 等确定性拒绝立即失败不重试
- LLM 完全不可用时理解层自动降级为规则引擎，SSE 仍正常产出 `engine=rules` 结果并附降级说明；NL Patch 无规则兜底，返回明确的 503/502 而非裸 500
- SSE worker 异常返回通用错误文案，原始堆栈仅入日志

### 质量

- 后端 446 项 pytest（每个存储用例执行真实 Alembic upgrade/downgrade），ruff + pyright 全绿；前端 vitest + oxlint + tsc + vite build 全绿；GitHub Actions 双端 CI

### 已知边界（随 V0.1 演进解决）

- 规则引擎仅覆盖典型销售/订单/商品台账模式；非销售业务表建议配置 LLM
- 仅支持一对多关系、单文件导入、SQLite；无用户/权限体系
- LLM 输出经校验后仍建议在审查页逐项确认后再生成系统
