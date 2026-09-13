import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchHealth, fetchRecent, useTransactionStream } from './api'
import { AttackStory } from './components/AttackStory'
import { DemoPanel } from './components/DemoPanel'
import type { GraphHighlight } from './components/GraphCanvas'
import { GraphView } from './components/GraphView'
import { TransactionRow } from './components/TransactionRow'
import { applyStoryEvent, type StoryState } from './story'
import type { Health, ScenarioRun, ScoredTransaction, StoryEvent } from './types'

const MAX_ROWS = 500
const rowKey = (s: ScoredTransaction) => `${s.event.transaction.id}:${s.event.transaction.status}`
const GRAPH_PREF_KEY = 'rho.showGraph'
// Storage can throw (private window, blocked site data); the graph just defaults on.
const readShowGraph = () => {
  try {
    return localStorage.getItem(GRAPH_PREF_KEY) !== '0'
  } catch {
    return true
  }
}
const byNewest = (a: ScoredTransaction, b: ScoredTransaction) =>
  a.scored_at < b.scored_at ? 1 : a.scored_at > b.scored_at ? -1 : 0

export default function App() {
  const [rows, setRows] = useState<ScoredTransaction[]>([])
  const [showBackfill, setShowBackfill] = useState(true)
  const [onlyFlagged, setOnlyFlagged] = useState(false)
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [graphKey, setGraphKey] = useState(0)
  const [projector, setProjector] = useState(false)
  const [showGraph, setShowGraph] = useState(readShowGraph)
  const [story, setStory] = useState<StoryState | null>(null)

  const upsert = useCallback((incoming: ScoredTransaction[]) => {
    setRows((prev) => {
      const seen = new Set(prev.map(rowKey))
      const fresh = incoming.filter((s) => !seen.has(rowKey(s)))
      if (!fresh.length) return prev
      return [...fresh, ...prev].sort(byNewest).slice(0, MAX_ROWS)
    })
  }, [])

  // A fixture replay delivers 72 rows in a burst. Refetching the graph per row would
  // mean 72 requests and 72 layout runs, so coalesce them into one.
  const graphTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const bumpGraph = useCallback(() => {
    if (graphTimer.current) clearTimeout(graphTimer.current)
    graphTimer.current = setTimeout(() => setGraphKey((k) => k + 1), 350)
  }, [])
  useEffect(() => () => {
    if (graphTimer.current) clearTimeout(graphTimer.current)
  }, [])

  const clearFeed = useCallback(() => {
    setRows([])
    setSelectedKey(null)
    setStory(null)
  }, [])

  useEffect(() => {
    fetchRecent(300)
      .then((items) => {
        upsert(items)
        setError(null)
      })
      .catch((e) => setError(`Backend unreachable: ${e}`))
    fetchHealth()
      .then(setHealth)
      .catch(() => undefined)
  }, [upsert])

  // Press P to switch to projector sizing. One keystroke on stage beats editing CSS.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return
      if (e.key === 'p' || e.key === 'P') setProjector((v) => !v)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    document.body.classList.toggle('projector', projector)
  }, [projector])

  useEffect(() => {
    try {
      localStorage.setItem(GRAPH_PREF_KEY, showGraph ? '1' : '0')
    } catch {
      // not persisted; the toggle still works for this session
    }
  }, [showGraph])

  const handlers = useMemo(
    () => ({
      onTransaction: (s: ScoredTransaction) => {
        upsert([s])
        bumpGraph()
      },
      // The backend rebuilt from the fixture; drop our rows so the replay lands.
      onReset: clearFeed,
      onStory: (e: StoryEvent) => {
        setStory((s) => applyStoryEvent(s, e))
        // Focus the graph on each impostor the moment it is caught: the name-match link is the proof.
        if (e.status === 'scored' && e.level === 'alert') setSelectedKey(`${e.transaction_id}:${e.transaction_status}`)
      },
    }),
    [upsert, clearFeed, bumpGraph],
  )
  const status = useTransactionStream(handlers)

  const onScenarioResult = useCallback((run: ScenarioRun) => {
    // The last flagged step, so a story ends focused on its final catch, not its first routine payment.
    const flagged = [...run.results].reverse().find((r) => r.anomaly.level !== 'normal') ?? run.results[0]
    if (flagged) setSelectedKey(rowKey(flagged))
    bumpGraph()
  }, [bumpGraph])

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

  // Only live alerts pulse. The fixture's own historical alerts would ring from page
  // load and teach the audience to ignore the effect before the demo starts.
  const alertNodeIds = useMemo(() => {
    const ids = new Set<string>()
    for (const r of rows) {
      if (!r.event.backfill && r.anomaly.level === 'alert' && r.features.counterparty_node_id) {
        ids.add(r.features.counterparty_node_id)
      }
    }
    return ids
  }, [rows])

  const selected = useMemo(
    () => rows.find((r) => rowKey(r) === selectedKey) ?? null,
    [rows, selectedKey],
  )

  const highlight: GraphHighlight | null = useMemo(() => {
    if (!selected) return null
    return {
      accountNodeId: selected.features.account_node_id || null,
      counterpartyNodeId: selected.features.counterparty_node_id || null,
      lookalikeNodeId: selected.features.lookalike?.matched_node_id ?? null,
      ratio: selected.features.lookalike?.ratio ?? null,
      level: selected.anomaly.level,
    }
  }, [selected])

  return (
    <div className="app">
      <header>
        <div className="brand">
          <h1>RhoGuard</h1>
          <span className={`conn ${status}`}>
            <i /> {status}
          </span>
          {health?.offline && (
            <span className="pill offline" title="Rho was unreachable; replaying the bundled sandbox data">
              offline · fixture data
            </span>
          )}
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
          {health?.scorer && <span className="muted">model: {health.scorer}</span>}
        </div>
        <div className="controls">
          <label>
            <input type="checkbox" checked={showBackfill} onChange={(e) => setShowBackfill(e.target.checked)} /> show
            history
          </label>
          <label>
            <input type="checkbox" checked={onlyFlagged} onChange={(e) => setOnlyFlagged(e.target.checked)} /> flagged
            only
          </label>
          <label>
            <input type="checkbox" checked={showGraph} onChange={(e) => setShowGraph(e.target.checked)} /> graph
          </label>
        </div>
      </header>

      <DemoPanel onReset={clearFeed} onResult={onScenarioResult} />

      {story && <AttackStory story={story} onDismiss={() => setStory(null)} />}

      {error && <div className="banner error">{error}</div>}

      <main className={showGraph ? undefined : 'no-graph'}>
        <section className="feed">
          {visible.length === 0 ? (
            <p className="empty muted">No transactions yet. Press one of the buttons above to run a scenario.</p>
          ) : (
            <>
              <div className="feed-head cap">
                <span>Time</span>
                <span>Counterparty</span>
                <span>Account</span>
                <span className="right">Amount</span>
                <span>Status</span>
                <span>Anomaly</span>
                <span />
              </div>
              <ul>
              {visible.map((item) => {
                const key = rowKey(item)
                return (
                  <TransactionRow
                    key={key}
                    item={item}
                    open={key === selectedKey}
                    onToggle={() => setSelectedKey((cur) => (cur === key ? null : key))}
                  />
                )
                })}
              </ul>
            </>
          )}
        </section>
        {showGraph && <GraphView highlight={highlight} refreshKey={graphKey} alertNodeIds={alertNodeIds} />}
      </main>
    </div>
  )
}
