# Z-Sheet Frontend

React + Vite + TypeScript。

## 开发

```bash
pnpm install
pnpm dev      # http://localhost:5173，/api 代理到后端 http://localhost:8000
pnpm build    # 类型检查 + 生产构建
pnpm lint     # oxlint
```

后端启动：在仓库根目录执行 `uv run uvicorn backend.api.main:app --reload`。
