/** Typed client for backend health endpoints (proxied under /api in dev). */

export interface HealthStatus {
  status: string
  version: string
  database: string
}

export async function fetchHealth(): Promise<HealthStatus> {
  const response = await fetch('/api/v1/health')
  if (!response.ok) {
    throw new Error(`health check failed with status ${response.status}`)
  }
  return (await response.json()) as HealthStatus
}
