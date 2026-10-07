import { useCallback, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { RuntimeContext } from './runtime-context'
import type { Bootstrap, RecordRow } from './types'

export function RuntimeProvider({
  bootstrap,
  children,
}: {
  bootstrap: Bootstrap
  children: ReactNode
}) {
  const [records, setRecords] = useState<Record<string, RecordRow[]>>(() =>
    structuredClone(bootstrap.records),
  )

  const addRecord = useCallback((entityKey: string, row: RecordRow) => {
    setRecords((previous) => ({
      ...previous,
      [entityKey]: [...(previous[entityKey] ?? []), row],
    }))
  }, [])

  const value = useMemo(
    () => ({ bootstrap, records, addRecord }),
    [bootstrap, records, addRecord],
  )

  return <RuntimeContext.Provider value={value}>{children}</RuntimeContext.Provider>
}
