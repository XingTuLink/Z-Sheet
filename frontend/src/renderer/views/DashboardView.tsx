import { Link, useLocation } from 'react-router-dom'

import { resolveAppRoute } from '../appRoute'
import {
  selectDistribution,
  selectRecent,
  selectTrend,
} from '../dashboardData'
import { formatCell, formatMoney, formatNumber } from '../format'
import { evaluateMetric } from '../metrics'
import { getField } from '../resolve'
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

function MetricCards({ view }: { view: DashboardViewModel }) {
  const { bootstrap, records } = useRuntime()
  const metrics = (view.metrics ?? [])
    .map((key) => bootstrap.model.metrics.find((metric) => metric.key === key))
    .filter((metric): metric is Metric => Boolean(metric))

  return (
    <div className="metric-grid">
      {metrics.map((metric) => {
        const value = evaluateMetric(metric, records[metric.entity] ?? [])
        return (
          <article className="card metric-card" key={metric.key}>
            <p className="metric-name">{metric.name}</p>
            <strong className="metric-value">
              {formatMetricValue(metric, bootstrap.model, value)}
            </strong>
            <p className="metric-definition">{metric.business_definition}</p>
          </article>
        )
      })}
    </div>
  )
}

function RecentRows() {
  const { bootstrap, records } = useRuntime()
  const location = useLocation()
  const { basePath } = resolveAppRoute(location.pathname)
  const recent = selectRecent(bootstrap.model, records)
  if (!recent) return null

  const columns: BusinessField[] = (recent.listView?.columns ?? [
    recent.entity.key_field,
  ])
    .map((key) => getField(recent.entity, key))
    .filter((field): field is BusinessField => Boolean(field))
    .slice(0, 4)

  return (
    <section className="card dashboard-panel">
      <div className="panel-head">
        <h2>最近{recent.entity.name}</h2>
        {recent.listView && (
          <Link className="panel-link" to={`${basePath}/views/${recent.listView.key}`}>
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
          {recent.rows.map((row: RecordRow) => {
            const rowKey = String(row[recent.entity.key_field] ?? '')
            return (
              <tr key={rowKey}>
                {columns.map((field) => {
                  const value = row[field.key]
                  return (
                    <td key={field.key}>
                      {field.key === recent.entity.key_field && recent.detailView ? (
                        <Link
                          className="row-link"
                          to={`${basePath}/views/${recent.detailView.key}?id=${encodeURIComponent(rowKey)}`}
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

function TrendPanel() {
  const { bootstrap, records } = useRuntime()
  const trend = selectTrend(bootstrap.model, records)
  if (!trend) return null
  const max = Math.max(...trend.points.map((point) => point.value), 1)

  return (
    <section className="card dashboard-panel">
      <div className="panel-head">
        <h2>{trend.measureField ? `${trend.measureField.name}趋势` : `${trend.entity.name}数量趋势`}</h2>
        <span className="muted">按月统计</span>
      </div>
      <div className="bar-chart" aria-label="按月趋势">
        {trend.points.map((point) => (
          <div className="bar-column" key={point.label}>
            <span className="bar-value">
              {trend.measureField
                ? formatMoney(point.value)
                : formatNumber(point.value)}
            </span>
            <div className="bar-track">
              <div className="bar-fill" style={{ height: `${Math.max(4, (point.value / max) * 100)}%` }} />
            </div>
            <span className="bar-label">{point.label.slice(5)}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

function DistributionPanel() {
  const { bootstrap, records } = useRuntime()
  const distribution = selectDistribution(bootstrap.model, records)
  if (!distribution) return null
  const max = Math.max(...distribution.items.map((item) => item.count), 1)

  return (
    <section className="card dashboard-panel">
      <div className="panel-head">
        <h2>{distribution.field.name}分布</h2>
        <span className="muted">{distribution.entity.name}</span>
      </div>
      <div className="distribution-list">
        {distribution.items.map((item) => (
          <div className="distribution-row" key={item.label}>
            <span className="distribution-label">{item.label}</span>
            <div className="distribution-track">
              <div
                className="distribution-fill"
                style={{ width: `${Math.max(4, (item.count / max) * 100)}%` }}
              />
            </div>
            <span className="distribution-count">{item.count}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

export function DashboardView({ view }: { view: DashboardViewModel }) {
  return (
    <section className="view dashboard-view">
      <header className="view-header">
        <div>
          <h1>{view.title}</h1>
          <p className="view-sub">由已确认模型和当前数据自动计算</p>
        </div>
      </header>
      <MetricCards view={view} />
      <div className="dashboard-panels">
        <RecentRows />
        <TrendPanel />
        <DistributionPanel />
      </div>
    </section>
  )
}
