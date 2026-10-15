import { createContext } from 'react'

import type { Bootstrap, RecordRow } from './types'

export interface RuntimeContextValue {
  bootstrap: Bootstrap
  records: Record<string, RecordRow[]>
  /** Public /p/:slug pages are strictly read-only; hide all mutation UI. */
  readOnly: boolean
  saveRecord: (
    entityKey: string,
    row: RecordRow,
    mode: 'create' | 'update',
  ) => Promise<void>
  deleteRecord: (entityKey: string, key: string) => Promise<void>
}

export const RuntimeContext = createContext<RuntimeContextValue | null>(null)
