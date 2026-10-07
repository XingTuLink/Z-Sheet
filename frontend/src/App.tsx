import { useQuery } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router-dom'

import { fetchHealth } from './api/health'
import './App.css'

function HealthPage() {
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ['health'],
    queryFn: fetchHealth,
    retry: false,
  })

  return (
    <main className="page">
      <h1>Z-Sheet</h1>
      <p className="subtitle">前后端联调骨架 · Day 2</p>

      <section className="card">
        <h2>后端连接状态</h2>
        {isLoading && <p className="muted">检查中…</p>}
        {isError && (
          <p className="status">
            <span className="dot dot-error" />
            无法连接后端（请确认后端已在 localhost:8000 启动）
          </p>
        )}
        {data && (
          <>
            <p className="status">
              <span className="dot dot-ok" />
              API 正常
            </p>
            <dl className="meta">
              <div>
                <dt>状态</dt>
                <dd>{data.status}</dd>
              </div>
              <div>
                <dt>版本</dt>
                <dd>{data.version}</dd>
              </div>
              <div>
                <dt>数据库</dt>
                <dd>{data.database}</dd>
              </div>
            </dl>
          </>
        )}
        <button type="button" onClick={() => void refetch()} disabled={isFetching}>
          {isFetching ? '检查中…' : '重新检查'}
        </button>
      </section>
    </main>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<HealthPage />} />
      </Routes>
    </BrowserRouter>
  )
}
