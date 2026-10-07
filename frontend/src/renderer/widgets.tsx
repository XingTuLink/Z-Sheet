import type { ReactNode } from 'react'

/** Defensive fallback for model references that should never be wrong
 * (the backend validates them) but must not crash the renderer anyway. */
export function ModelError({ children }: { children: ReactNode }) {
  return (
    <section className="view">
      <div className="card">
        <h1>模型配置有误</h1>
        <p className="muted">{children}</p>
      </div>
    </section>
  )
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}
