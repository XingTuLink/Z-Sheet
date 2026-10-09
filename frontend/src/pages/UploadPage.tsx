import { useRef, useState, type ChangeEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import {
  parseWorkbookWithProgress,
  saveUnderstandingResult,
  TEMPLATE_URL,
  type StageEvent,
  type StageKey,
  type UnderstandingResult,
} from '../api/ingestion'
import { setLastUploadedFile } from '../api/sessionStore'

type Phase = 'idle' | 'working' | 'done' | 'error'
type StageState = 'pending' | 'active' | 'done'

const MAX_BYTES = 20 * 1024 * 1024
const ACCEPT = '.xlsx,.csv'

interface StageDef {
  key: StageKey
  pending: string
  active: string
  done: (count: number) => string
}

const STAGE_DEFS: StageDef[] = [
  {
    key: 'sheets',
    pending: '解析工作表',
    active: '正在解析工作表…',
    done: (n) => `解析 ${n} 个工作表`,
  },
  {
    key: 'fields',
    pending: '识别字段',
    active: '正在识别字段…',
    done: (n) => `识别 ${n} 个字段`,
  },
  {
    key: 'entities',
    pending: '识别业务实体',
    active: '正在识别业务实体…',
    done: (n) => `识别 ${n} 个业务实体`,
  },
  {
    key: 'relations',
    pending: '检测关联关系',
    active: '正在检测关联关系…',
    done: (n) => `找到 ${n} 个可能的关联`,
  },
  {
    key: 'assemble',
    pending: '生成系统',
    active: '正在生成系统',
    done: () => '系统生成完成',
  },
]

type StageMap = Record<StageKey, { status: StageState; count: number }>

function initialStages(): StageMap {
  return {
    sheets: { status: 'pending', count: 0 },
    fields: { status: 'pending', count: 0 },
    entities: { status: 'pending', count: 0 },
    relations: { status: 'pending', count: 0 },
    assemble: { status: 'pending', count: 0 },
  }
}

function isSupportedFile(file: File): boolean {
  const name = file.name.toLowerCase()
  return name.endsWith('.xlsx') || name.endsWith('.csv')
}

export function UploadPage() {
  const navigate = useNavigate()
  const [phase, setPhase] = useState<Phase>('idle')
  const [stages, setStages] = useState<StageMap>(initialStages)
  const [fileName, setFileName] = useState('')
  const [result, setResult] = useState<UnderstandingResult | null>(null)
  const [errorMessage, setErrorMessage] = useState('')
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const patchStage = (key: StageKey, status: StageState, count?: number) => {
    setStages((prev) => ({
      ...prev,
      [key]: {
        status,
        count: count === undefined ? prev[key].count : count,
      },
    }))
  }

  const startUpload = async (file: File) => {
    if (!isSupportedFile(file)) {
      setErrorMessage('目前支持 .xlsx 与 .csv 文件，请重新选择。')
      setPhase('error')
      return
    }
    if (file.size > MAX_BYTES) {
      setErrorMessage('文件超过 20 MB 上限，请拆分或精简后再上传。')
      setPhase('error')
      return
    }

    setFileName(file.name)
    setResult(null)
    setErrorMessage('')
    setStages(initialStages())
    patchStage('sheets', 'active')
    setPhase('working')

    try {
      const nextStage: Record<StageKey, StageKey | null> = {
        sheets: 'fields',
        fields: 'entities',
        entities: 'relations',
        relations: 'assemble',
        assemble: null,
      }
      const onStage = (event: StageEvent) => {
        if (event.key === 'assemble') {
          // The assemble frame is an in-progress marker; the result frame
          // arriving is what completes this stage.
          patchStage('assemble', 'active')
          return
        }
        patchStage(event.key, 'done', event.count)
        const following = nextStage[event.key]
        if (following) patchStage(following, 'active')
      }

      const payload = await parseWorkbookWithProgress(file, onStage)
      patchStage('assemble', 'done')
      saveUnderstandingResult(payload)
      // Day 15: Confirm re-sends this same file; keep it until the tab closes.
      setLastUploadedFile(file)
      setResult(payload)
      setPhase('done')
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分析失败，请重试。')
      setPhase('error')
    }
  }

  const onInputChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (file) void startUpload(file)
    event.target.value = ''
  }

  const reset = () => {
    setPhase('idle')
    setResult(null)
    setErrorMessage('')
    setStages(initialStages())
    setFileName('')
  }

  return (
    <main className="gate upload-page">
      <div className="card upload-card">
        <h1 className="gate-brand">Z-Sheet</h1>

        {phase === 'idle' && (
          <>
            <p className="upload-tagline">上传一张 Excel，一分钟内变成一个能用的系统。</p>
            <input
              ref={inputRef}
              type="file"
              accept={ACCEPT}
              className="upload-input"
              onChange={onInputChange}
            />
            <div
              className={`dropzone${dragging ? ' dragging' : ''}`}
              role="button"
              tabIndex={0}
              onClick={() => inputRef.current?.click()}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') inputRef.current?.click()
              }}
              onDragOver={(event) => {
                event.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault()
                setDragging(false)
                const file = event.dataTransfer.files?.[0]
                if (file) void startUpload(file)
              }}
            >
              <p className="dropzone-title">点击选择，或将文件拖拽到此处</p>
              <p className="dropzone-sub">支持 .xlsx（多工作表）与 .csv，单个文件不超过 20 MB</p>
            </div>
            <p className="upload-note">
              文件仅在当前环境解析，不上传任何业务数据到云端。
            </p>
            <p className="upload-demo">
              没有现成文件？<Link to="/app/demo">先看看演示系统</Link>
            </p>
          </>
        )}

        {phase === 'working' && (
          <>
            <p className="upload-tagline">正在理解「{fileName}」</p>
            <ul className="stage-list">
              {STAGE_DEFS.map((def) => {
                const state = stages[def.key]
                const label =
                  state.status === 'done'
                    ? def.done(state.count)
                    : state.status === 'active'
                      ? def.active
                      : def.pending
                return (
                  <li key={def.key} className={`stage-item ${state.status}`}>
                    <span className="stage-mark" aria-hidden="true">
                      {state.status === 'done' ? '✓' : state.status === 'active' ? '→' : ''}
                    </span>
                    <span className="stage-label">{label}</span>
                  </li>
                )
              })}
            </ul>
            <p className="upload-note">每个阶段都基于真实分析结果，而不是等待动画。</p>
          </>
        )}

        {phase === 'done' && result && !result.business_model && (
          <>
            <p className="upload-tagline">这份表格没能生成系统</p>
            <div className="notice error-notice">
              <p>文件可以正常打开和解析，但没有识别出客户、商品、订单类的业务实体。</p>
            </div>
            {result.assembly_notes.length > 0 && (
              <div className="notice result-notes">
                <p className="result-notes-title">具体原因：</p>
                <ul>
                  {result.assembly_notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              </div>
            )}
            <p className="upload-note">
              Z-Sheet 目前识别明细表：每张工作表一行表头、一列业务唯一标识（如客户名称、订单编号），
              数据从第二行起每行一条记录；标题行、合并单元格、合计行以及报价单这类功能清单无法构成系统主体。
            </p>
            <div className="upload-actions">
              <a
                href={TEMPLATE_URL}
                download
                className="btn btn-primary"
              >
                下载标准模板
              </a>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => inputRef.current?.click()}
              >
                重新选择文件
              </button>
            </div>
            <p className="upload-demo">
              模板内含可直接上传体验的示例数据，正式使用时替换成你的真实数据（保留表头）即可。
            </p>
            <input
              ref={inputRef}
              type="file"
              accept={ACCEPT}
              className="upload-input"
              onChange={onInputChange}
            />
          </>
        )}

        {phase === 'done' && result && result.business_model && (
          <>
            <p className="upload-tagline">分析完成</p>
            <div className="result-head">
              <span className="result-name">
                {result.business_model?.app.name ?? '未命名应用'}
              </span>
              <span className="muted">{result.file_name}</span>
            </div>
            <dl className="summary-grid">
              <div className="summary-item">
                <dt>工作表</dt>
                <dd>{stages.sheets.count}</dd>
              </div>
              <div className="summary-item">
                <dt>字段</dt>
                <dd>{stages.fields.count}</dd>
              </div>
              <div className="summary-item">
                <dt>业务实体</dt>
                <dd>{stages.entities.count}</dd>
              </div>
              <div className="summary-item">
                <dt>可能的关联</dt>
                <dd>{stages.relations.count}</dd>
              </div>
            </dl>
            {result.business_model && result.business_model.entities.length > 0 && (
              <div className="result-entities">
                {result.business_model.entities.map((entity) => (
                  <span key={entity.key} className="tag">
                    {entity.name}
                  </span>
                ))}
              </div>
            )}
            {result.assembly_notes.length > 0 && (
              <div className="notice result-notes">
                <p className="result-notes-title">以下工作表未纳入模型：</p>
                <ul>
                  {result.assembly_notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              </div>
            )}
            <div className="upload-actions">
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => navigate('/understanding')}
              >
                查看理解结果
              </button>
              <button type="button" className="btn btn-secondary" onClick={reset}>
                再上传一个文件
              </button>
            </div>
            <p className="upload-demo">
              想先看渲染效果？<Link to="/app/demo">查看内置演示系统</Link>
            </p>
          </>
        )}

        {phase === 'error' && (
          <>
            <p className="upload-tagline">未能完成分析</p>
            <div className="notice error-notice">
              <p>{errorMessage}</p>
            </div>
            <div className="upload-actions">
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => inputRef.current?.click()}
              >
                重新选择文件
              </button>
              <button type="button" className="btn btn-secondary" onClick={reset}>
                返回
              </button>
            </div>
            <input
              ref={inputRef}
              type="file"
              accept={ACCEPT}
              className="upload-input"
              onChange={onInputChange}
            />
          </>
        )}
      </div>
    </main>
  )
}
