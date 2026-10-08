import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import {
  loadReviewState,
  loadUnderstandingResult,
  saveReviewState,
  type FieldRole,
  type FieldType,
  type InferredLink,
  type LinkReview,
  type ReviewDecision,
  type ReviewState,
  type UnderstandingEntity,
  type UnderstandingResult,
  type UnderstandingSheet,
} from '../api/ingestion'

const TYPE_LABELS: Record<FieldType, string> = {
  string: '文本',
  number: '数字',
  money: '金额',
  date: '日期',
  enum: '枚举',
  phone: '电话',
}

const ROLE_LABELS: Record<FieldRole, string> = {
  identifier: '标识',
  dimension: '维度',
  measure: '度量',
  time: '时间',
  enum: '枚举',
  text: '文本',
}

type Tier = 'high' | 'medium' | 'low'

const TIER_LABELS: Record<Tier, string> = {
  high: '已识别',
  medium: '建议确认',
  low: '无法确定',
}

function confidenceTier(confidence: number): Tier {
  if (confidence >= 0.85) return 'high'
  if (confidence >= 0.6) return 'medium'
  return 'low'
}

function ConfidenceBadge({ confidence }: { confidence: number }) {
  const tier = confidenceTier(confidence)
  return (
    <span className={`tag tag-tier tier-${tier}`} title={`置信度 ${confidence}`}>
      {TIER_LABELS[tier]} · {confidence.toFixed(2)}
    </span>
  )
}

function sheetDropReason(sheet: UnderstandingSheet): string {
  if (sheet.is_empty) return '空工作表'
  if (sheet.inferred_entity?.key === 'unknown') return '实体类型无法识别（unknown）'
  if (sheet.inferred_entity && sheet.inferred_entity.key_field === null) {
    return '缺少唯一标识字段'
  }
  return '未纳入模型'
}

function fieldAckId(entityKey: string, fieldKey: string) {
  return `${entityKey}.${fieldKey}`
}

interface ReviewQueueProps {
  result: UnderstandingResult
  entityConfidence: Map<string, { confidence: number; needs_review: boolean }>
  review: ReviewState
  onAcknowledge: (id: string) => void
  onLinkChange: (linkKey: string, patch: Record<string, unknown>) => void
}

function ReviewQueue({
  result,
  entityConfidence,
  review,
  onAcknowledge,
  onLinkChange,
}: ReviewQueueProps) {
  const entities = result.business_model?.entities ?? []
  const entityName = (key: string) =>
    entities.find((entity) => entity.key === key)?.name ?? key

  interface AckItem {
    kind: 'entity' | 'field'
    id: string
    location: string
    label: string
    confidence: number
    reason: string | null
  }

  const ackItems: AckItem[] = []
  for (const entity of entities) {
    const entityMeta = entityConfidence.get(entity.key)
    if (entityMeta && entityMeta.needs_review) {
      ackItems.push({
        kind: 'entity',
        id: entity.key,
        location: '实体',
        label: entity.name,
        confidence: entityMeta.confidence,
        reason: '实体归类证据不足，建议确认',
      })
    }
    for (const field of entity.fields) {
      if (field.needs_review) {
        ackItems.push({
          kind: 'field',
          id: fieldAckId(entity.key, field.key),
          location: `${entity.name} · 字段`,
          label: field.name,
          confidence: field.confidence,
          reason: field.review_reason ?? '推断证据不足，建议人工确认',
        })
      }
    }
  }

  const links = result.inferred_links
  const total = ackItems.length + links.length
  const pending =
    ackItems.filter((item) => !review.acknowledged[item.id]).length +
    links.filter((link) => !review.links[link.key]?.decision).length

  if (total === 0) return null

  return (
    <section className="card review-queue" id="review-queue">
      <header className="queue-head">
        <h2>
          待确认队列
          <span
            className={`queue-count${pending === 0 ? ' is-done' : ''}`}
          >
            {pending === 0 ? '全部已处理' : `${pending} 项待确认 / 共 ${total} 项`}
          </span>
        </h2>
        <p className="muted queue-hint">
          系统把不确定的推断显式列在这里。确认或修正后才会进入下一步；高置信不等于无需确认（所有关联均无数据源外键定义）。
        </p>
      </header>

      <ul className="queue-list">
        {ackItems.map((item) => {
          const acked = Boolean(review.acknowledged[item.id])
          return (
            <li key={item.kind + item.id} className={`queue-item${acked ? ' is-acked' : ''}`}>
              <div className="queue-item-main">
                <div className="queue-item-title">
                  <span className="queue-item-label">{item.label}</span>
                  <span className="tag tag-kind">{item.location}</span>
                  <ConfidenceBadge confidence={item.confidence} />
                  {acked && <span className="tag tag-acked">已确认</span>}
                </div>
                <p className="queue-reason">{item.reason}</p>
              </div>
              <div className="queue-item-actions">
                {acked ? (
                  <button
                    type="button"
                    className="btn btn-mini"
                    onClick={() => onAcknowledge(item.id)}
                    aria-pressed="true"
                  >
                    撤销确认
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn btn-mini btn-primary"
                    onClick={() => onAcknowledge(item.id)}
                  >
                    确认无误
                  </button>
                )}
              </div>
            </li>
          )
        })}

        {links.map((link) => (
          <LinkReviewRow
            key={link.key}
            link={link}
            fromName={entityName(link.from_entity)}
            toName={entityName(link.to_entity)}
            parentEntity={entities.find((e) => e.key === link.from_entity)}
            childEntity={entities.find((e) => e.key === link.to_entity)}
            review={review.links[link.key]}
            onChange={(patch) => onLinkChange(link.key, patch)}
          />
        ))}
      </ul>
    </section>
  )
}

interface LinkReviewRowProps {
  link: InferredLink
  fromName: string
  toName: string
  parentEntity?: UnderstandingEntity
  childEntity?: UnderstandingEntity
  review?: { decision?: 'accepted' | 'rejected'; on_from: string; on_to: string }
  onChange: (patch: Record<string, unknown>) => void
}

function LinkReviewRow({
  link,
  fromName,
  toName,
  parentEntity,
  childEntity,
  review,
  onChange,
}: LinkReviewRowProps) {
  const decision = review?.decision
  const onFrom = review?.on_from ?? link.on_from
  const onTo = review?.on_to ?? link.on_to

  const fieldOptions = (entity: UnderstandingEntity | undefined, selected: string) => (
    <select
      value={selected}
      disabled={decision === 'rejected'}
      onChange={(event) => onChange({ [entity === parentEntity ? 'on_from' : 'on_to']: event.target.value })}
    >
      {(entity?.fields ?? []).map((field) => (
        <option key={field.key} value={field.key}>
          {field.name}（{field.key}）
        </option>
      ))}
    </select>
  )

  return (
    <li className={`queue-item queue-link ${decision ? `is-${decision}` : ''}`}>
      <div className="queue-item-main">
        <div className="queue-item-title">
          <span className="queue-item-label">
            {fromName} <span className="link-cardinality">1 : N</span> {toName}
          </span>
          <ConfidenceBadge confidence={link.confidence} />
          {decision === 'accepted' && <span className="tag tag-accepted">已接受</span>}
          {decision === 'rejected' && <span className="tag tag-rejected">已拒绝</span>}
        </div>
        <p className="queue-reason">
          {link.review_reason ?? '源数据没有外键定义，关联由字段名与值重叠推断，请确认。'}
        </p>
        <div className="link-on-editor">
          <span className="link-on-label">关联字段</span>
          {fieldOptions(parentEntity, onFrom)}
          <span className="link-on-eq">=</span>
          {fieldOptions(childEntity, onTo)}
        </div>
      </div>
      <div className="queue-item-actions">
        <button
          type="button"
          className={`btn btn-mini${decision === 'accepted' ? ' btn-primary' : ''}`}
          aria-pressed={decision === 'accepted'}
          onClick={() => onChange({ decision: 'accepted' })}
        >
          接受
        </button>
        <button
          type="button"
          className={`btn btn-mini${decision === 'rejected' ? ' btn-danger' : ''}`}
          aria-pressed={decision === 'rejected'}
          onClick={() => onChange({ decision: 'rejected' })}
        >
          拒绝
        </button>
      </div>
    </li>
  )
}

function EntityCard({
  entity,
  confidence,
  review,
}: {
  entity: UnderstandingEntity
  confidence?: { confidence: number; needs_review: boolean }
  review: ReviewState
}) {
  return (
    <section className="card entity-card">
      <header className="entity-head">
        <div className="entity-title">
          <h2>{entity.name}</h2>
          <span className="tag entity-key">{entity.key}</span>
          {confidence && <ConfidenceBadge confidence={confidence.confidence} />}
          {confidence?.needs_review &&
            (review.acknowledged[entity.key] ? (
              <span className="tag tag-acked">已确认</span>
            ) : (
              <span className="tag tag-pending">待确认</span>
            ))}
        </div>
        <div className="entity-meta">
          {entity.source?.sheet && (
            <span className="muted">来源工作表：{entity.source.sheet}</span>
          )}
        </div>
      </header>

      <ul className="field-list">
        {entity.fields.map((field) => {
          const isKey = field.key === entity.key_field
          const ackId = fieldAckId(entity.key, field.key)
          const acked = Boolean(review.acknowledged[ackId])
          return (
            <li key={field.key} className={`field-item${isKey ? ' is-key' : ''}`}>
              <div className="field-name">
                <span className="field-cn">{field.name}</span>
                <code className="field-key">{field.key}</code>
                {isKey && <span className="tag tag-key">主键</span>}
                {field.needs_review &&
                  (acked ? (
                    <span className="tag tag-acked">已确认</span>
                  ) : (
                    <span className="tag tag-pending">待确认</span>
                  ))}
              </div>
              <div className="field-tags">
                <span className={`tag tag-type tag-type-${field.type}`}>
                  {TYPE_LABELS[field.type]}
                </span>
                <span className={`tag tag-role tag-role-${field.role}`}>
                  {ROLE_LABELS[field.role]}
                </span>
                <ConfidenceBadge confidence={field.confidence} />
              </div>
              {field.needs_review && (
                <p className="field-review-reason">
                  {field.review_reason ?? '推断证据不足，建议人工确认'}
                </p>
              )}
              {field.type === 'enum' && field.values && field.values.length > 0 && (
                <p className="field-enum">
                  候选值：{field.values.slice(0, 8).join('、')}
                  {field.values.length > 8 ? ' 等' : ''}
                </p>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function RelationSection({ result, review }: { result: UnderstandingResult; review: ReviewState }) {
  const entities = result.business_model?.entities ?? []
  const entityName = (key: string) =>
    entities.find((entity) => entity.key === key)?.name ?? key

  if (result.inferred_links.length === 0) {
    return (
      <section className="card understanding-block">
        <h2 className="block-title">关联关系</h2>
        <p className="muted block-empty">没有检测到一对多关联。</p>
      </section>
    )
  }

  return (
    <section className="card understanding-block">
      <h2 className="block-title">
        关联关系
        <span className="muted block-count">{result.inferred_links.length} 个</span>
      </h2>
      <ul className="relation-list">
        {result.inferred_links.map((link) => {
          const decision = review.links[link.key]?.decision
          const onFrom = review.links[link.key]?.on_from ?? link.on_from
          const onTo = review.links[link.key]?.on_to ?? link.on_to
          return (
            <li
              key={link.key}
              className={`relation-item${decision ? ` is-${decision}` : ''}`}
            >
              <span className="relation-end">{entityName(link.from_entity)}</span>
              <span className="relation-arrow" aria-hidden="true">
                1 : N
              </span>
              <span className="relation-end">{entityName(link.to_entity)}</span>
              <code className="relation-on">
                {onFrom} = {onTo}
              </code>
              <span className="relation-status">
                {decision === 'accepted' && <span className="tag tag-accepted">已接受</span>}
                {decision === 'rejected' && <span className="tag tag-rejected">已拒绝</span>}
                {!decision && <span className="tag tag-pending">待确认</span>}
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function DroppedSheetsSection({
  result,
  assembledSheets,
}: {
  result: UnderstandingResult
  assembledSheets: Set<string>
}) {
  const dropped = result.sheets.filter(
    (sheet) => !assembledSheets.has(sheet.name),
  )
  if (dropped.length === 0) return null

  return (
    <section className="card understanding-block">
      <h2 className="block-title">
        未纳入模型的工作表
        <span className="muted block-count">{dropped.length} 个</span>
      </h2>
      <ul className="dropped-list">
        {dropped.map((sheet) => (
          <li key={sheet.name} className="dropped-item">
            <div className="dropped-head">
              <span className="dropped-name">{sheet.name}</span>
              <span className="tag tag-dropped">{sheetDropReason(sheet)}</span>
            </div>
            <p className="muted dropped-meta">
              {sheet.is_empty
                ? '工作表中没有数据行'
                : `${sheet.profile.row_count} 行数据 · ${sheet.columns.length} 列：${sheet.columns.join('、')}`}
            </p>
          </li>
        ))}
      </ul>
    </section>
  )
}

export function UnderstandingPage() {
  const result = useMemo(() => loadUnderstandingResult(), [])
  const [review, setReview] = useState<ReviewState>(() => loadReviewState())

  if (!result || !result.business_model) {
    return (
      <main className="gate">
        <div className="card gate-card">
          <h1 className="gate-brand">Z-Sheet</h1>
          <p>还没有可浏览的理解结果。</p>
          <p className="muted">请先上传一个 Excel 或 CSV 文件。</p>
          <p style={{ marginTop: '20px' }}>
            <Link to="/" className="btn btn-primary">
              去上传表格
            </Link>
          </p>
        </div>
      </main>
    )
  }

  const entities = result.business_model.entities
  const assembledSheets = new Set(
    entities
      .map((entity) => entity.source?.sheet)
      .filter((sheet): sheet is string => Boolean(sheet)),
  )

  // Entity confidence lives on the sheet-level inference; join via source sheet.
  const entityConfidence = new Map<
    string,
    { confidence: number; needs_review: boolean }
  >()
  for (const entity of entities) {
    const sheetName = entity.source?.sheet
    const inferred = sheetName
      ? result.sheets.find((sheet) => sheet.name === sheetName)?.inferred_entity
      : null
    if (inferred) {
      entityConfidence.set(entity.key, {
        confidence: inferred.confidence,
        needs_review: inferred.needs_review,
      })
    }
  }

  const pendingCount =
    entities.reduce(
      (sum, entity) =>
        sum +
        (entityConfidence.get(entity.key)?.needs_review &&
        !review.acknowledged[entity.key]
          ? 1
          : 0) +
        entity.fields.filter(
          (field) =>
            field.needs_review && !review.acknowledged[fieldAckId(entity.key, field.key)],
        ).length,
      0,
    ) +
    result.inferred_links.filter((link) => !review.links[link.key]?.decision).length

  const updateReview = (next: ReviewState) => {
    setReview(next)
    saveReviewState(next)
  }

  const acknowledge = (id: string) => {
    updateReview({
      ...review,
      acknowledged: { ...review.acknowledged, [id]: !review.acknowledged[id] },
    })
  }

  const changeLink = (linkKey: string, patch: Record<string, unknown>) => {
    const current = review.links[linkKey]
    const link = result.inferred_links.find((item) => item.key === linkKey)
    const next: LinkReview = {
      decision:
        (patch.decision as ReviewDecision | undefined) ?? current?.decision,
      on_from:
        (patch.on_from as string | undefined) ??
        current?.on_from ??
        link?.on_from ??
        '',
      on_to:
        (patch.on_to as string | undefined) ??
        current?.on_to ??
        link?.on_to ??
        '',
    }
    updateReview({
      ...review,
      links: { ...review.links, [linkKey]: next },
    })
  }

  return (
    <main className="understanding-page">
      <div className="understanding-inner">
        <header className="understanding-header">
          <div>
            <Link to="/" className="back-link">
              返回上传
            </Link>
            <h1>{result.business_model.app.name} · 理解结果</h1>
            <p className="view-sub">{result.file_name}</p>
          </div>
          <div className="understanding-stats">
            <span>{entities.length} 个实体</span>
            <span>{result.inferred_links.length} 个关联</span>
            <span className={pendingCount === 0 ? 'stat-done' : 'stat-pending'}>
              {pendingCount === 0 ? '审查已完成' : `${pendingCount} 项待确认`}
            </span>
          </div>
        </header>

        <ReviewQueue
          result={result}
          entityConfidence={entityConfidence}
          review={review}
          onAcknowledge={acknowledge}
          onLinkChange={changeLink}
        />

        <section className="understanding-section">
          <h2 className="section-title">
            业务实体
            <span className="muted block-count">{entities.length} 个</span>
          </h2>
          <div className="entity-grid">
            {entities.map((entity) => (
              <EntityCard
                key={entity.key}
                entity={entity}
                confidence={entityConfidence.get(entity.key)}
                review={review}
              />
            ))}
          </div>
        </section>

        <RelationSection result={result} review={review} />
        <DroppedSheetsSection result={result} assembledSheets={assembledSheets} />

        <p className="muted review-footnote">
          审查结果暂存在当前会话中；「确认并生成系统」将在下一阶段开放，届时接受/拒绝/字段修正会随模型一起落库。
        </p>
      </div>
    </main>
  )
}
