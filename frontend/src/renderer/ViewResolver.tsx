import { useLocation } from 'react-router-dom'

import type { View } from './types'
import { DashboardView } from './views/DashboardView'
import { DetailView } from './views/DetailView'
import { FormView } from './views/FormView'
import { ListView } from './views/ListView'
import { UnsupportedView } from './views/UnsupportedView'

/**
 * Component registry entry point (design doc 28.5):
 * View Resolver maps the declared view kind to a React component.
 * Selection is purely a function of the model; no AI calls occur here.
 */
export function ViewResolver({ view }: { view: View }) {
  const location = useLocation()
  const editId = new URLSearchParams(location.search).get('id') ?? ''
  switch (view.kind) {
    case 'list':
      return <ListView view={view} />
    case 'detail':
      return <DetailView view={view} />
    case 'form':
      return <FormView key={`${view.key}:${editId}`} view={view} />
    case 'dashboard':
      return <DashboardView view={view} />
    default:
      return <UnsupportedView view={view} />
  }
}
