import type { Bootstrap } from '../renderer/types'

/** The bundled demo app; lazily seeded by the backend on first bootstrap. */
export const DEMO_APP_KEY = 'default'

/**
 * Loads everything the renderer needs in one round trip.
 * The demo app is seeded lazily by the backend on the first request.
 */
export async function fetchBootstrap(appKey: string): Promise<Bootstrap> {
  const response = await fetch(`/api/v1/runtime/${appKey}/bootstrap`)
  if (!response.ok) {
    throw new Error(`bootstrap failed with status ${response.status}`)
  }
  return (await response.json()) as Bootstrap
}
