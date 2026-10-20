import type {
  ImportConflict,
  ImportFileInfo,
  ImportPlan,
  UnderstandingSheet,
} from '../api/ingestion'

const CONFLICT_LABELS: Record<ImportConflict['kind'], string> = {
  primary_key_duplicate: '主键重复',
  field_type_mismatch: '字段类型不一致',
  enum_values_expanded: '枚举取值不一致',
}

/** Why a sheet cannot become an entity; empty string means it can. */
function sheetReason(sheet: UnderstandingSheet): string {
  if (sheet.is_empty) return '空工作表，无法生成实体'
  if (sheet.inferred_entity?.key === 'unknown') return '实体类型无法识别'
  if (!sheet.inferred_entity?.key_field) return '缺少唯一标识字段'
  return ''
}

function sheetLabel(sheet: UnderstandingSheet): string {
  return sheet.display_name ?? sheet.name
}

function entityName(sheet: UnderstandingSheet | undefined): string {
  return sheet?.inferred_entity?.name ?? '未识别实体'
}

interface ImportPlanPanelProps {
  files: ImportFileInfo[]
  sheets: UnderstandingSheet[]
  conflicts: ImportConflict[]
  plan: ImportPlan
  /** False when the uploads were lost to a page refresh. */
  hasFiles: boolean
  busy: boolean
  error: string
  notice: string
  onChange: (plan: ImportPlan) => void
  onReset: () => void
}

export function ImportPlanPanel({
  files,
  sheets,
  conflicts,
  plan,
  hasFiles,
  busy,
  error,
  notice,
  onChange,
  onReset,
}: ImportPlanPanelProps) {
  const selected = new Set(plan.sheets)
  const byId = new Map(
    sheets.flatMap((sheet) => (sheet.id ? [[sheet.id, sheet] as const] : [])),
  )
  const groups = plan.groups.filter((group) => group.sheets.length > 1)

  const toggleSheet = (sheetId: string, checked: boolean) => {
    const nextSheets = checked
      ? [...plan.sheets, sheetId]
      : plan.sheets.filter((id) => id !== sheetId)
    // Dropping a sheet also drops it from any merge group.
    const nextGroups = plan.groups
      .map((group) => ({
        sheets: group.sheets.filter((id) => nextSheets.includes(id)),
      }))
      .filter((group) => group.sheets.length > 0)
    onChange({ ...plan, sheets: nextSheets, groups: nextGroups })
  }

  const toggleMember = (groupIndex: number, sheetId: string, checked: boolean) => {
    const nextGroups = plan.groups
      .map((group, index) => {
        if (index === groupIndex) {
          return {
            sheets: checked
              ? [...group.sheets, sheetId]
              : group.sheets.filter((id) => id !== sheetId),
          }
        }
        return { sheets: group.sheets.filter((id) => id !== sheetId) }
      })
      // A group of one is just an independent entity.
      .filter((group) => group.sheets.length > 1)
    onChange({ ...plan, groups: nextGroups })
  }

  const splitGroup = (groupIndex: number) => {
    onChange({
      ...plan,
      groups: plan.groups.filter((_group, index) => index !== groupIndex),
    })
  }

  const setConflict = (conflictId: string, value: string) => {
    onChange({ ...plan, conflicts: { ...plan.conflicts, [conflictId]: value } })
  }

  const disabled = !hasFiles || busy

  return (
    <section className="card understanding-block import-plan" id="import-plan">
      <h2 className="block-title">
        导入方案
        <span className="muted block-count">
          {files.length} 个文件 · {sheets.length} 张工作表
        </span>
      </h2>
      <p className="muted block-footnote">
        选择要纳入系统的工作表，调整同实体多表的合并方式，并处理合并冲突。
        任何调整都会重新计算业务实体与关联，下方审查记录也会随之重置。
      </p>

      {!hasFiles && (
        <div className="notice">
          <p>
            当前会话已丢失上传的文件（页面可能被刷新过），无法重新计算方案。
            请返回重新上传；已上传的文件可以继续按现有方案生成。
          </p>
        </div>
      )}
      {busy && <p className="plan-status">正在重新计算方案…</p>}
      {error && (
        <div className="notice error-notice">
          <p>{error}</p>
        </div>
      )}
      {notice && <p className="plan-status">{notice}</p>}

      <h3 className="plan-subtitle">文件与工作表</h3>
      <ul className="plan-file-list">
        {files.map((file) => {
          const fileSheets = sheets.filter(
            (sheet) => (sheet.file_name ?? '') === file.name,
          )
          return (
            <li key={file.name} className="plan-file">
              <div className="plan-file-head">
                <span className="plan-file-name">{file.name}</span>
                <span className="muted">
                  {file.sheet_count} 张表 · {file.row_count} 行
                </span>
              </div>
              <ul className="plan-sheet-list">
                {fileSheets.map((sheet) => {
                  const id = sheet.id ?? ''
                  const reason = sheetReason(sheet)
                  const checked = selected.has(id)
                  return (
                    <li key={id || sheet.name} className="plan-sheet">
                      <label className="plan-check">
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={disabled || Boolean(reason)}
                          onChange={(event) =>
                            toggleSheet(id, event.target.checked)
                          }
                        />
                        <span className="plan-sheet-name">{sheetLabel(sheet)}</span>
                        <span className="muted">{sheet.profile.row_count} 行</span>
                        {reason ? (
                          <span className="tag tag-dropped">{reason}</span>
                        ) : (
                          <span className="tag">{entityName(sheet)}</span>
                        )}
                      </label>
                    </li>
                  )
                })}
              </ul>
            </li>
          )
        })}
      </ul>

      <div className="plan-subhead">
        <h3 className="plan-subtitle">合并方式</h3>
        <button
          type="button"
          className="btn btn-mini"
          disabled={disabled}
          onClick={onReset}
        >
          恢复推荐合并
        </button>
      </div>
      {groups.length === 0 ? (
        <p className="muted plan-empty">每张工作表各自生成一个独立实体。</p>
      ) : (
        <ul className="plan-group-list">
          {groups.map((group, index) => {
            const first = byId.get(group.sheets[0])
            return (
              <li key={group.sheets.join('-')} className="plan-group">
                <div className="plan-group-head">
                  <span className="plan-group-name">
                    「{entityName(first)}」合并为同一实体
                  </span>
                  <span className="muted">{group.sheets.length} 张工作表</span>
                  <button
                    type="button"
                    className="btn btn-mini"
                    disabled={disabled}
                    onClick={() => splitGroup(index)}
                  >
                    拆分为独立实体
                  </button>
                </div>
                <ul className="plan-sheet-list">
                  {group.sheets.map((sheetId) => {
                    const sheet = byId.get(sheetId)
                    return (
                      <li key={sheetId} className="plan-sheet">
                        <label className="plan-check">
                          <input
                            type="checkbox"
                            checked
                            disabled={disabled}
                            onChange={(event) =>
                              toggleMember(index, sheetId, event.target.checked)
                            }
                          />
                          <span className="plan-sheet-name">
                            {sheet ? sheetLabel(sheet) : sheetId}
                          </span>
                          <span className="muted">
                            {sheet?.file_name ?? ''} · {sheet?.profile.row_count ?? 0} 行
                          </span>
                        </label>
                      </li>
                    )
                  })}
                </ul>
              </li>
            )
          })}
        </ul>
      )}
      <p className="muted plan-hint">
        取消勾选某张工作表，即可把它从合并中拆出（单独成为一个实体）。
      </p>

      <h3 className="plan-subtitle">
        合并冲突
        <span className="muted block-count">{conflicts.length} 项</span>
      </h3>
      {conflicts.length === 0 ? (
        <p className="muted plan-empty">没有检测到需要处理的冲突。</p>
      ) : (
        <ul className="plan-conflict-list">
          {conflicts.map((conflict) => (
            <li key={conflict.id} className="plan-conflict">
              <div className="plan-conflict-main">
                <div className="plan-conflict-head">
                  <span className="tag tag-kind">
                    {CONFLICT_LABELS[conflict.kind]}
                  </span>
                  <span className="plan-conflict-entity">
                    {conflict.entity_name}
                    {conflict.field ? ` · ${conflict.field}` : ''}
                  </span>
                </div>
                <p className="plan-conflict-message">{conflict.message}</p>
              </div>
              <select
                className="plan-conflict-select"
                value={plan.conflicts[conflict.id] ?? conflict.default}
                disabled={disabled}
                onChange={(event) => setConflict(conflict.id, event.target.value)}
              >
                {conflict.options.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
