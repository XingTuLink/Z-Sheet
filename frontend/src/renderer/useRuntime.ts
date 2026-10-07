import { useContext } from 'react'

import { RuntimeContext } from './runtime-context'

export function useRuntime() {
  const context = useContext(RuntimeContext)
  if (context === null) {
    throw new Error('renderer components must be wrapped in <RuntimeProvider>')
  }
  return context
}
