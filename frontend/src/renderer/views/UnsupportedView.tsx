import type { View } from '../types'

/** Registry placeholder for view kinds not implemented yet (e.g. dashboard). */
export function UnsupportedView({ view }: { view: View }) {
  return (
    <section className="view">
      <div className="card placeholder-card">
        <h1>{view.title}</h1>
        <p className="muted">该视图类型（{view.kind}）将在后续版本提供。</p>
      </div>
    </section>
  )
}
