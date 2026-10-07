# 参与贡献 Z-Sheet

感谢关注 Z-Sheet。本文说明开发环境、代码要求与提交流程。

## 开发环境

- Python 3.12（本地 3.13 亦可运行，CI 与容器固定 3.12）
- [uv](https://docs.astral.sh/uv/) 0.12
- Node.js 24、pnpm 12
- Docker（可选，用于与生产一致的单容器验证）

## 后端

```bash
uv sync
uv run alembic upgrade head     # 创建本地开发库
uv run uvicorn backend.api.main:app --reload
```

检查与测试（全部通过再提交）：

```bash
uv run ruff check .
uv run pyright
uv run pytest
```

测试对每个用例执行真实的 Alembic 迁移（upgrade/downgrade），新增存储变更时必须附迁移脚本，不要用 `create_all` 过渡。

## 前端

```bash
cd frontend
pnpm install
pnpm dev        # :5173，/api 代理到 :8000
pnpm lint       # oxlint
pnpm build      # tsc -b + vite build
```

## 容器验证

```bash
docker compose build
docker compose down -v
docker compose up -d
```

后端镜像同时构建前端，FastAPI 在单容器内提供 API 与 SPA 静态资源。

## 架构约束

- **Renderer 不调用 AI。** 渲染是 `model + data → UI` 的纯确定性过程；AI 只参与产出或修改 Business Model。
- Business Model 是唯一事实来源；修改模型只通过校验过的 JSON Patch 产生新版本，不整体重写。
- 依赖保持克制：V0.x 只使用 SQLite，不引入 Elasticsearch、Redis、Kafka 等基础设施。
- 统一用 uv / pyproject 管理 Python 依赖，用 pnpm 管理前端依赖，不新增 requirements.txt 等平行机制。

## 提交信息

使用 Conventional Commits：

```text
feat:     新功能
fix:      缺陷修复
refactor: 不改变外部行为的重构
test:     测试相关
docs:     文档
chore:    构建 / 工具 / 杂项
```

主题行客观描述变更本身；正文可分点列出关键改动。

## Pull Request

1. 从 `main` 切出描述性分支名；
2. 后端 ruff / pyright / pytest 全过，前端 oxlint / build 全过；
3. 新行为附带测试；修复尽量附带回归测试；
4. PR 描述说明动机、改动点与验证方式。

## 许可证

提交到本仓库即表示你同意你的贡献以 [Apache License 2.0](./LICENSE) 授权。
