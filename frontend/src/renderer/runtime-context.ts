import { createContext } from 'react'

import type { Bootstrap, RecordRow } from './types'

export interface RuntimeContextValue {
  bootstrap: Bootstrap
  records: Record<string, RecordRow[]>
  saveRecord: (
    entityKey: string,
    row: RecordRow,
    mode: 'create' | 'update',
  ) => Promise<void>
  deleteRecord: (entityKey: string, key: string) => Promise<void>
}

export const RuntimeContext = createContext<RuntimeContextValue | null>(null)
