/**
 * Frontend mirror of the frozen Business Model contract (schema 0.1).
 *
 * The renderer consumes only model + data; these types describe exactly what
 * the backend bootstrap endpoint returns. Snapshots are dumped without field
 * aliases, so Link fields appear as `from_` and `on.from_`.
 */

export type FieldType = 'string' | 'number' | 'money' | 'date' | 'enum' | 'phone'
export type FieldRole =
  | 'identifier'
  | 'dimension'
  | 'measure'
  | 'time'
  | 'enum'
  | 'text'

export interface BusinessField {
  key: string
  name: string
  type: FieldType
  role: FieldRole
  values?: string[] | null
  confidence: number
  needs_review?: boolean | null
  review_reason?: string | null
}

export interface SourceRef {
  file: string
  sheet?: string | null
}

export interface Entity {
  key: string
  name: string
  source?: SourceRef | null
  key_field: string
  fields: BusinessField[]
}

export interface LinkOn {
  from_: string
  to: string
}

export interface Link {
  key: string
  from_: string
  to: string
  type: 'one_to_many'
  on: LinkOn
  confidence: number
  needs_review?: boolean | null
  review_reason?: string | null
}

export interface SortSpec {
  field: string
  dir: 'asc' | 'desc'
}

export interface ListView {
  kind: 'list'
  key: string
  entity: string
  title: string
  columns: string[]
  search?: string[]
  filters?: string[]
  sorts?: SortSpec[]
}

export type DetailBlockType = 'fields' | 'related_list'

export interface DetailBlock {
  type: DetailBlockType
  link?: string | null
  title?: string | null
}

export interface DetailView {
  kind: 'detail'
  key: string
  entity: string
  title: string
  blocks: DetailBlock[]
}

export interface FormView {
  kind: 'form'
  key: string
  entity: string
  title: string
  fields?: string[] | null
}

export interface DashboardView {
  kind: 'dashboard'
  key: string
  title: string
  metrics?: string[]
}

export type View = ListView | DetailView | FormView | DashboardView
export type ViewKind = View['kind']

export interface NavigationItem {
  label: string
  view: string
}

export interface BusinessModel {
  version: string
  app: { name: string; source?: { files: string[] } | null }
  entities: Entity[]
  links: Link[]
  metrics: unknown[]
  views: View[]
  navigation: NavigationItem[]
}

export type FieldValue = string | number | null
export type RecordRow = Record<string, FieldValue>

export interface Bootstrap {
  app_key: string
  version: number
  model: BusinessModel
  records: Record<string, RecordRow[]>
}
