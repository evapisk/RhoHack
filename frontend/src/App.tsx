import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchHealth, fetchRecent, useTransactionStream } from './api'
import { GraphView } from './components/GraphView'
import { TransactionRow } from './components/TransactionRow'
import type { ScoredTransaction } from './types'

const MAX_ROWS = 500
const rowKey = (s: ScoredTransaction) => `${s.event.transaction.id}:${s.event.transaction.status}`
const byNewest = (a: ScoredTransaction, b: ScoredTransaction) => (a.scored_at < b.scored_at ? 1 : a.scored_at > b.scored_at ? -1 : 0)

export default function App() {
  const [rows, setRows] = useState<ScoredTransaction[]>([])
  const [showBackfill, setShowBackfill] = useState(true)
  const [onlyFlagged, setOnlyFlagged] = useState(false)
  const [scorer, setScorer] = useState('')
  const [error, setError] = useState<string | null>(null)

  const upsert = useCallback((incoming: ScoredTransaction[]) => {
    setRows((prev) => {
      const seen = new Set(prev.map(rowKey))
      const fresh = incoming.filter((s) => !seen.has(rowKey(s)))
      if (!fresh.length) return prev
      return [...fresh, ...prev].sort(byNewest).slice(0, MAX_ROWS)
    })
  }, [])

  useEffect(() => {
    fetchRecent(300)
      .then((items) => {
        upsert(items)
        setError(null)
      })
      .catch((e) => setError(`Backend unreachable: ${e}`))
    fetchHealth()
      .then((h) => setScorer(h.scorer))
      .catch(() => undefined)
  }, [upsert])

  const status = useTransactionStream(useCallback((s: ScoredTransaction) => upsert([s]), [upsert]))

  const visible = useMemo(
    () => rows.filter((r) => (showBackfill || !r.event.backfill) && (!onlyFlagged || r.anomaly.level !== 'normal')),
    [rows, showBackfill, onlyFlagged],
  )

  const counts = useMemo(() => {
    let live = 0
    let alerts = 0
    let warns = 0
    for (const r of rows) {
      if (!r.event.backfill) live++
      if (r.anomaly.level === 'alert') alerts++
      else if (r.anomaly.level === 'warn') warns++
    }
    return { total: rows.length, live, alerts, warns }
  }, [rows])

  return (
    <div className="app">
      <header>
        <div className="brand">
          <h1>Rho Transaction Graph</h1>
          <span className={`conn ${status}`}>
            <i /> {status}
          </span>
        </div>
        <div className="counters">
          <span>
            <b>{counts.total}</b> scored
          </span>
          <span>
            <b>{counts.live}</b> live
          </span>
          <span className="warn">
            <b>{counts.warns}</b> warn
          </span>
          <span className="alert">
            <b>{counts.alerts}</b> alert
          </span>
          {scorer && <span className="muted">model: {scorer}</span>}
        </div>
        <div className="controls">
          <label>
            <input type="checkbox" checked={showBackfill} onChange={(e) => setShowBackfill(e.target.checked)} /> show history
          </label>
          <label>
            <input type="checkbox" checked={onlyFlagged} onChange={(e) => setOnlyFlagged(e.target.checked)} /> flagged only
          </label>
        </div>
      </header>

      {error && <div className="banner error">{error}</div>}

      <main>
        <section className="feed">
          {visible.length === 0 ? (
            <p className="empty muted">
              No transactions yet. Backfill runs on first start; try <code>make demo-inject</code>.
            </p>
          ) : (
            <ul>
              {visible.map((item) => (
                <TransactionRow key={rowKey(item)} item={item} />
              ))}
            </ul>
          )}
        </section>
        <GraphView transactions={rows} />
      </main>
    </div>
  )
}
