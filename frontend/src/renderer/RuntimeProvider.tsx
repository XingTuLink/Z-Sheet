import { useCallback, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import {
  createRecord as createRecordApi,
  deleteRecord as deleteRecordApi,
  updateRecord as updateRecordApi,
} from '../api/runtime'
import { RuntimeContext } from './runtime-context'
import type { Bootstrap, RecordRow } from './types'

export function RuntimeProvider({
  bootstrap,
  readOnly = false,
  children,
}: {
  bootstrap: Bootstrap
  readOnly?: boolean
  children: ReactNode
}) {
  const [records, setRecords] = useState<Record<string, RecordRow[]>>(() =>
    structuredClone(bootstrap.records),
  )

  const saveRecord = useCallback(
    async (
      entityKey: string,
      row: RecordRow,
      mode: 'create' | 'update',
    ): Promise<void> => {
      if (readOnly) throw new Error('公开发布页为只读模式')
      const entity = bootstrap.model.entities.find((item) => item.key === entityKey)
      if (!entity) throw new Error(`实体不存在：${entityKey}`)
      const key = String(row[entity.key_field] ?? '')
      const saved =
        mode === 'create'
          ? await createRecordApi(bootstrap.app_key, entityKey, row)
          : await updateRecordApi(bootstrap.app_key, entityKey, key, row)

      setRecords((previous) => {
        const current = previous[entityKey] ?? []
        if (mode === 'create') {
          return { ...previous, [entityKey]: [...current, saved] }
        }
        return {
          ...previous,
          [entityKey]: current.map((item) =>
            String(item[entity.key_field] ?? '') === key ? saved : item,
          ),
        }
      })
    },
    [bootstrap.app_key, bootstrap.model.entities, readOnly],
  )

  const deleteRecord = useCallback(
    async (entityKey: string, key: string): Promise<void> => {
      if (readOnly) throw new Error('公开发布页为只读模式')
      const entity = bootstrap.model.entities.find((item) => item.key === entityKey)
      if (!entity) throw new Error(`实体不存在：${entityKey}`)
      await deleteRecordApi(bootstrap.app_key, entityKey, key)
      setRecords((previous) => ({
        ...previous,
        [entityKey]: (previous[entityKey] ?? []).filter(
          (item) => String(item[entity.key_field] ?? '') !== key,
        ),
      }))
    },
    [bootstrap.app_key, bootstrap.model.entities, readOnly],
  )

  const value = useMemo(
    () => ({ bootstrap, records, readOnly, saveRecord, deleteRecord }),
    [bootstrap, records, readOnly, saveRecord, deleteRecord],
  )

  return <RuntimeContext.Provider value={value}>{children}</RuntimeContext.Provider>
}
