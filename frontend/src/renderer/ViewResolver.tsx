import type { View } from './types'
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
  switch (view.kind) {
    case 'list':
      return <ListView view={view} />
    case 'detail':
      return <DetailView view={view} />
    case 'form':
      return <FormView view={view} />
    default:
      return <UnsupportedView view={view} />
  }
}
