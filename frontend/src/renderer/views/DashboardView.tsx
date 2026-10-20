import { useQuery } from '@tanstack/react-query'
import { Link, useLocation } from 'react-router-dom'

import { fetchDashboard, type DashboardData } from '../../api/runtime'
import { resolveAppRoute } from '../appRoute'
import { formatCell, formatMoney, formatNumber } from '../format'
import { findViewForEntity, getEntity, getField } from '../resolve'
import type {
  BusinessField,
  BusinessModel,
  DashboardView as DashboardViewModel,
  Metric,
  RecordRow,
} from '../types'
import { useRuntime } from '../useRuntime'

function formatMetricValue(
  metric: Metric,
  model: BusinessModel,
  value: number,
): string {
  const entity = model.entities.find((item) => item.key === metric.entity)
  const field = metric.formula.field
    ? entity?.fields.find((item) => item.key === metric.formula.field)
    : undefined
  if (field) return formatCell(field, value)
  return formatNumber(Math.round(value))
}

function MetricCards({
  view,
  values,
}: {
  view: DashboardViewModel
  values: Record<string, number>
}) {
  const { bootstrap } = useRuntime()
  const metrics = (view.metrics ?? [])
    .map((key) => bootstrap.model.metrics.find((metric) => metric.key === key))
    .filter((metric): metric is Metric => Boolean(metric))

  return (
    <div className="metric-grid">
      {metrics.map((metric) => (
        <article className="card metric-card" key={metric.key}>
          <p className="metric-name">{metric.name}</p>
          <strong className="metric-value">
            {formatMetricValue(
              metric,
              bootstrap.model,
              values[metric.key] ?? 0,
            )}
          </strong>
          <p className="metric-definition">{metric.business_definition}</p>
        </article>
      ))}
    </div>
  )
}

function RecentRows({ recent }: { recent: DashboardData['recent'] }) {
  const { bootstrap } = useRuntime()
  const location = useLocation()
  const { basePath } = resolveAppRoute(location.pathname)
  if (!recent) return null

  const entity = getEntity(bootstrap.model, recent.entity)
  if (!entity) return null
  const listView = findViewForEntity(bootstrap.model, recent.entity, 'list')
  const detailView = findViewForEntity(
    bootstrap.model,
    recent.entity,
    'detail',
  )
  const columns: BusinessField[] = (listView?.columns ?? [entity.key_field])
    .map((key) => getField(entity, key))
    .filter((field): field is BusinessField => Boolean(field))
    .slice(0, 4)

  return (
    <section className="card dashboard-panel">
      <div className="panel-head">
        <h2>最近{entity.name}</h2>
        {listView && (
          <Link className="panel-link" to={`${basePath}/views/${listView.key}`}>
            查看全部
          </Link>
        )}
      </div>
      <table className="grid dashboard-grid">
        <thead>
          <tr>
            {columns.map((field) => (
              <th key={field.key}>{field.name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {recent.rows.map((row: RecordRow, index: number) => {
            const rowKey = String(row[entity.key_field] ?? '')
            return (
              <tr key={`${rowKey}-${index}`}>
                {columns.map((field) => {
                  const value = row[field.key]
                  return (
                    <td key={field.key}>
                      {field.key === entity.key_field && detailView ? (
                        <Link
                          className="row-link"
                          to={`${basePath}/views/${detailView.key}?id=${encodeURIComponent(rowKey)}`}
                        >
                          {formatCell(field, value)}
                        </Link>
                      ) : field.type === 'enum' && value ? (
                        <span className="tag">{formatCell(field, value)}</span>
                      ) : (
                        formatCell(field, value)
                      )}
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}

function TrendPanel({ trend }: { trend: DashboardData['trend'] }) {
  const { bootstrap } = useRuntime()
  if (!trend) return null
  const entity = getEntity(bootstrap.model, trend.entity)
  if (!entity) return null
  const measureField = trend.measure_field
    ? getField(entity, trend.measure_field)
    : undefined
  const max = Math.max(...trend.points.map((point) => point.value), 1)

  return (
    <section className="card dashboard-panel">
      <div className="panel-head">
        <h2>
          {measureField
            ? `${measureField.name}趋势`
            : `${entity.name}数量趋势`}
        </h2>
        <span className="muted">按月统计</span>
      </div>
      <div className="bar-chart" aria-label="按月趋势">
        {trend.points.map((point) => (
          <div className="bar-column" key={point.label}>
            <div className="bar-track">
              <div
                className="bar-fill"
                style={{ height: `${Math.max(4, (point.value / max) * 100)}%` }}
              >
                <span className="bar-value">
                  {measureField
                    ? formatMoney(point.value)
                    : formatNumber(point.value)}
                </span>
              </div>
            </div>
            <span className="bar-label">{point.label.slice(5)}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

function DistributionPanel({
  distribution,
}: {
  distribution: DashboardData['distribution']
}) {
  const { bootstrap } = useRuntime()
  if (!distribution) return null
  const entity = getEntity(bootstrap.model, distribution.entity)
  if (!entity) return null
  const field = getField(entity, distribution.field)
  if (!field) return null
  const max = Math.max(...distribution.items.map((item) => item.count), 1)

  return (
    <section className="card dashboard-panel">
      <div className="panel-head">
        <h2>{field.name}分布</h2>
        <span className="muted">{entity.name}</span>
      </div>
      <div className="distribution-list">
        {distribution.items.map((item) => {
          // Values imported from vertically merged category cells may carry
          // visual in-cell line breaks; collapse them for the axis label.
          const label = item.label.replace(/\s*[\r\n]+\s*/g, '').trim()
          return (
            <div className="distribution-row" key={item.label}>
              <span className="distribution-label" title={label}>
                {label}
              </span>
              <div className="distribution-track">
                <div
                  className="distribution-fill"
                  style={{ width: `${Math.max(4, (item.count / max) * 100)}%` }}
                />
              </div>
              <span className="distribution-count">{item.count}</span>
            </div>
          )
        })}
      </div>
    </section>
  )
}

export function DashboardView({ view }: { view: DashboardViewModel }) {
  const { apiBase } = useRuntime()
  const { data, isPending, isError } = useQuery({
    queryKey: ['dashboard', apiBase],
    queryFn: () => fetchDashboard(apiBase),
  })

  return (
    <section className="view dashboard-view">
      <header className="view-header">
        <div>
          <h1>{view.title}</h1>
        </div>
      </header>
      {isError ? (
        <div className="card">
          <p className="muted">仪表盘加载失败，请刷新页面重试。</p>
        </div>
      ) : isPending || !data ? (
        <div className="card">
          <p className="muted">正在加载仪表盘…</p>
        </div>
      ) : (
        <>
          <MetricCards view={view} values={data.metrics} />
          <div className="dashboard-panels">
            <RecentRows recent={data.recent} />
            <TrendPanel trend={data.trend} />
            <DistributionPanel distribution={data.distribution} />
          </div>
        </>
      )}
    </section>
  )
}
