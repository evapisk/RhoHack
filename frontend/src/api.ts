import { useEffect, useRef, useState } from 'react'
import type {
  DemoResetResponse,
  GraphPayload,
  Health,
  ScenarioRun,
  ScenarioSummary,
  ScoredTransaction,
} from './types'

// All calls are relative: Vite proxies /api to the backend in dev.

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${url}`)
  return (await res.json()) as T
}

async function postJson<T>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${url}`)
  return (await res.json()) as T
}

export const fetchRecent = (limit = 300, flagged = false) =>
  getJson<ScoredTransaction[]>(`/api/transactions?limit=${limit}&flagged=${flagged}`)

export const fetchHealth = () => getJson<Health>('/api/health')

export const fetchGraph = () => getJson<GraphPayload>('/api/graph')

export const fetchScenarios = () =>
  getJson<{ scenarios: ScenarioSummary[] }>('/api/demo/scenarios').then((r) => r.scenarios)

export const runScenario = (id: string) => postJson<ScenarioRun>(`/api/demo/scenarios/${id}`)

export const resetDemo = () => postJson<DemoResetResponse>('/api/demo/reset')

export type StreamStatus = 'connecting' | 'live' | 'reconnecting'

export interface StreamHandlers {
  onTransaction: (item: ScoredTransaction) => void
  /** The backend rebuilt its state. Flush the feed before the replay arrives. */
  onReset?: () => void
  onScenario?: (info: { scenario_id: string; title: string; blurb: string }) => void
}

/**
 * Subscribes to the SSE feed. EventSource reconnects on its own; we only surface status.
 * Handlers are kept in a ref so callers can pass fresh closures without re-subscribing.
 */
export function useTransactionStream(handlers: StreamHandlers): StreamStatus {
  const [status, setStatus] = useState<StreamStatus>('connecting')
  const ref = useRef(handlers)

  useEffect(() => {
    ref.current = handlers
  }, [handlers])

  useEffect(() => {
    const es = new EventSource('/api/stream')
    es.onopen = () => setStatus('live')
    es.onerror = () => setStatus('reconnecting')
    es.addEventListener('heartbeat', () => setStatus('live'))

    const parse = <T,>(e: MessageEvent<string>, fn?: (v: T) => void) => {
      if (!fn) return
      try {
        fn(JSON.parse(e.data) as T)
      } catch (err) {
        console.error('bad SSE payload', err)
      }
    }

    es.addEventListener('transaction', (e: MessageEvent<string>) =>
      parse<ScoredTransaction>(e, (v) => ref.current.onTransaction(v)),
    )
    // Without this the feed would silently swallow a reset: it dedups by
    // (id, status) and a fixture replay reuses ids it already has.
    es.addEventListener('reset', () => ref.current.onReset?.())
    es.addEventListener('scenario', (e: MessageEvent<string>) =>
      parse<{ scenario_id: string; title: string; blurb: string }>(e, (v) => ref.current.onScenario?.(v)),
    )
    return () => es.close()
  }, [])

  return status
}

export function formatMoney(minor: number, currency = 'USD'): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(minor / 100)
}

export function formatCompactMoney(minor: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(minor / 100)
}

export function formatTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}
