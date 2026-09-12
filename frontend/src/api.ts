import { useEffect, useRef, useState } from 'react'
import type { GraphPayload, Health, ScoredTransaction } from './types'

// All calls are relative: Vite proxies /api to the backend in dev.

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${url}`)
  return (await res.json()) as T
}

export const fetchRecent = (limit = 300, flagged = false) =>
  getJson<ScoredTransaction[]>(`/api/transactions?limit=${limit}&flagged=${flagged}`)

export const fetchHealth = () => getJson<Health>('/api/health')

export const fetchGraph = () => getJson<GraphPayload>('/api/graph')

export type StreamStatus = 'connecting' | 'live' | 'reconnecting'

/**
 * Subscribes to the SSE feed. EventSource reconnects on its own; we only surface status.
 * `onEvent` is kept in a ref so callers can pass a fresh closure without re-subscribing.
 */
export function useTransactionStream(onEvent: (item: ScoredTransaction) => void): StreamStatus {
  const [status, setStatus] = useState<StreamStatus>('connecting')
  const handler = useRef(onEvent)

  useEffect(() => {
    handler.current = onEvent
  }, [onEvent])

  useEffect(() => {
    const es = new EventSource('/api/stream')
    es.onopen = () => setStatus('live')
    es.onerror = () => setStatus('reconnecting')
    es.addEventListener('heartbeat', () => setStatus('live'))
    es.addEventListener('transaction', (e: MessageEvent<string>) => {
      try {
        handler.current(JSON.parse(e.data) as ScoredTransaction)
      } catch (err) {
        console.error('bad SSE payload', err)
      }
    })
    return () => es.close()
  }, [])

  return status
}

export function formatMoney(minor: number, currency = 'USD'): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(minor / 100)
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
