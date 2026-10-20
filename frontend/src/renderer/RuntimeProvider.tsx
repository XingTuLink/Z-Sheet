import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo } from 'react'
import type { ReactNode } from 'react'

import {
  createRecord as createRecordApi,
  deleteRecord as deleteRecordApi,
  updateRecord as updateRecordApi,
} from '../api/runtime'
import { RuntimeContext } from './runtime-context'
import type { Bootstrap, RecordRow } from './types'

const RECORD_SCOPES = ['rows', 'row', 'related', 'dashboard'] as const

export function RuntimeProvider({
  bootstrap,
  apiBase,
  readOnly = false,
  children,
}: {
  bootstrap: Bootstrap
  apiBase: string
  readOnly?: boolean
  children: ReactNode
}) {
  const queryClient = useQueryClient()

  // Rows are no longer mirrored in React state: mutations invalidate the
  // server-backed queries so every mounted view refetches what it shows.
  const invalidateRecords = useCallback(() => {
    for (const scope of RECORD_SCOPES) {
      void queryClient.invalidateQueries({ queryKey: [scope, apiBase] })
    }
  }, [apiBase, queryClient])

  const saveRecord = useCallback(
    async (
      entityKey: string,
      row: RecordRow,
      mode: 'create' | 'update',
    ): Promise<void> => {
      if (readOnly) throw new Error('公开发布页为只读模式')
      const entity = bootstrap.model.entities.find(
        (item) => item.key === entityKey,
      )
      if (!entity) throw new Error(`实体不存在：${entityKey}`)
      const key = String(row[entity.key_field] ?? '')
      if (mode === 'create') {
        await createRecordApi(bootstrap.app_key, entityKey, row)
      } else {
        await updateRecordApi(bootstrap.app_key, entityKey, key, row)
      }
      invalidateRecords()
    },
    [bootstrap.app_key, bootstrap.model.entities, invalidateRecords, readOnly],
  )

  const deleteRecord = useCallback(
    async (entityKey: string, key: string): Promise<void> => {
      if (readOnly) throw new Error('公开发布页为只读模式')
      const entity = bootstrap.model.entities.find(
        (item) => item.key === entityKey,
      )
      if (!entity) throw new Error(`实体不存在：${entityKey}`)
      await deleteRecordApi(bootstrap.app_key, entityKey, key)
      invalidateRecords()
    },
    [bootstrap.app_key, bootstrap.model.entities, invalidateRecords, readOnly],
  )

  const value = useMemo(
    () => ({ bootstrap, apiBase, readOnly, saveRecord, deleteRecord }),
    [bootstrap, apiBase, readOnly, saveRecord, deleteRecord],
  )

  return (
    <RuntimeContext.Provider value={value}>{children}</RuntimeContext.Provider>
  )
}
