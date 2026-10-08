import { Link } from 'react-router-dom'

import {
  loadUnderstandingResult,
  type FieldRole,
  type FieldType,
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

function sheetDropReason(sheet: UnderstandingSheet): string {
  if (sheet.is_empty) return '空工作表'
  if (sheet.inferred_entity?.key === 'unknown') return '实体类型无法识别（unknown）'
  if (sheet.inferred_entity && sheet.inferred_entity.key_field === null) {
    return '缺少唯一标识字段'
  }
  return '未纳入模型'
}

function EntityCard({ entity }: { entity: UnderstandingEntity }) {
  return (
    <section className="card entity-card">
      <header className="entity-head">
        <div className="entity-title">
          <h2>{entity.name}</h2>
          <span className="tag entity-key">{entity.key}</span>
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
          return (
            <li key={field.key} className={`field-item${isKey ? ' is-key' : ''}`}>
              <div className="field-name">
                <span className="field-cn">{field.name}</span>
                <code className="field-key">{field.key}</code>
                {isKey && <span className="tag tag-key">主键</span>}
              </div>
              <div className="field-tags">
                <span className={`tag tag-type tag-type-${field.type}`}>
                  {TYPE_LABELS[field.type]}
                </span>
                <span className={`tag tag-role tag-role-${field.role}`}>
                  {ROLE_LABELS[field.role]}
                </span>
              </div>
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

function RelationSection({ result }: { result: UnderstandingResult }) {
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
        {result.inferred_links.map((link) => (
          <li key={link.key} className="relation-item">
            <span className="relation-end">{entityName(link.from_entity)}</span>
            <span className="relation-arrow" aria-hidden="true">
              1 : N
            </span>
            <span className="relation-end">{entityName(link.to_entity)}</span>
            <code className="relation-on">
              {link.on_from} = {link.on_to}
            </code>
          </li>
        ))}
      </ul>
      <p className="muted block-footnote">
        所有关联均需人工确认，确认与修正将在下一步开放。
      </p>
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
  const result = loadUnderstandingResult()

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
          </div>
        </header>

        <section className="understanding-section">
          <h2 className="section-title">
            业务实体
            <span className="muted block-count">{entities.length} 个</span>
          </h2>
          <div className="entity-grid">
            {entities.map((entity) => (
              <EntityCard key={entity.key} entity={entity} />
            ))}
          </div>
        </section>

        <RelationSection result={result} />
        <DroppedSheetsSection result={result} assembledSheets={assembledSheets} />
      </div>
    </main>
  )
}
