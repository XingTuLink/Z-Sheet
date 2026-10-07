import { createContext } from 'react'

import type { Bootstrap, RecordRow } from './types'

export interface RuntimeContextValue {
  bootstrap: Bootstrap
  /** Local, non-persisted record state. Persistence lands with the data runtime. */
  records: Record<string, RecordRow[]>
  addRecord: (entityKey: string, row: RecordRow) => void
}

export const RuntimeContext = createContext<RuntimeContextValue | null>(null)
