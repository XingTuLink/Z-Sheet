import type {
  BusinessField,
  BusinessModel,
  Entity,
  View,
  ViewKind,
} from './types'

/** View Resolver helpers: navigation/view-key/entity -> declared model nodes. */

export function findViewByKey(model: BusinessModel, key: string): View | undefined {
  return model.views.find((view) => view.key === key)
}

export function findViewForEntity(
  model: BusinessModel,
  entityKey: string,
  kind: ViewKind,
): View | undefined {
  return model.views.find(
    // DashboardView has no `entity`; the in-operator narrow keeps this safe
    // for every view kind passed in here.
    (view) => view.kind === kind && 'entity' in view && view.entity === entityKey,
  )
}

export function getEntity(model: BusinessModel, key: string): Entity | undefined {
  return model.entities.find((entity) => entity.key === key)
}

export function getField(entity: Entity, key: string): BusinessField | undefined {
  return entity.fields.find((field) => field.key === key)
}
