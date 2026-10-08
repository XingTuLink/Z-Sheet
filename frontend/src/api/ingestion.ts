// Day 12: upload a workbook and follow real understanding stages over SSE.
// The endpoint streams one JSON frame per Server-Sent Event:
//   stage  -> { key, count }  a pipeline stage just made progress
//   result -> full ProfiledParsedWorkbook payload
//   error  -> { detail }

export type StageKey = 'sheets' | 'fields' | 'entities' | 'relations' | 'assemble'

export interface StageEvent {
  key: StageKey
  count: number
}

export interface InferredLink {
  key: string
  confidence: number
  needs_review: boolean
}

export interface UnderstandingEntity {
  key: string
  name: string
}

export interface UnderstandingResult {
  file_name: string
  file_type: string
  assembly_notes: string[]
  inferred_links: InferredLink[]
  business_model: {
    app: { name: string }
    entities: UnderstandingEntity[]
    metrics: unknown[]
  } | null
  business_model_yaml: string | null
}

/**
 * POST the file to the SSE parse endpoint and invoke `onStage` whenever a
 * real pipeline stage completes. Resolves with the full understanding
 * result when the final `result` frame arrives.
 */
export async function parseWorkbookWithProgress(
  file: File,
  onStage: (event: StageEvent) => void,
): Promise<UnderstandingResult> {
  const form = new FormData()
  form.append('file', file)

  const response = await fetch('/api/v1/ingestion/parse/stream', {
    method: 'POST',
    body: form,
  })
  if (!response.ok || !response.body) {
    throw new Error(`服务暂不可用（HTTP ${response.status}），请确认后端已启动。`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const handleBlock = (block: string): UnderstandingResult | undefined => {
    let event = ''
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event: ')) event = line.slice('event: '.length)
      else if (line.startsWith('data: ')) dataLines.push(line.slice('data: '.length))
    }
    if (!event) return undefined
    const data = dataLines.join('\n')

    if (event === 'stage') {
      onStage(JSON.parse(data) as StageEvent)
      return undefined
    }
    if (event === 'result') {
      return JSON.parse(data) as UnderstandingResult
    }
    if (event === 'error') {
      const payload = JSON.parse(data) as { detail?: unknown }
      throw new Error(
        typeof payload.detail === 'string' && payload.detail
          ? payload.detail
          : '文件解析失败，请检查文件格式后重试。',
      )
    }
    return undefined
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let separator: number
    while ((separator = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, separator)
      buffer = buffer.slice(separator + 2)
      const result = handleBlock(block)
      if (result) return result
    }
  }

  throw new Error('连接中断，未收到分析结果，请重试。')
}
