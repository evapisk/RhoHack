import { useEffect, useState } from 'react'
import { fetchGraph } from '../api'
import type { GraphPayload } from '../types'

/**
 * Stretch-goal placeholder. Polls /api/graph and shows what the graph knows.
 * TODO (frontend owner, hours 9-13): render nodes/edges with a force layout
 * (react-force-graph or sigma.js); color counterparty nodes by their latest anomaly level.
 */
export function GraphView() {
  const [graph, setGraph] = useState<GraphPayload | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () =>
      fetchGraph()
        .then((g) => !cancelled && (setGraph(g), setError(null)))
        .catch((e) => !cancelled && setError(String(e)))
    load()
    const id = setInterval(load, 10_000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  const topVendors = graph?.nodes
    .filter((n) => n.kind === 'counterparty')
    .sort((a, b) => b.tx_count - a.tx_count)
    .slice(0, 8)

  return (
    <aside className="graph-panel">
      <h2>Graph</h2>
      {error && <p className="error">{error}</p>}
      {graph ? (
        <>
          <dl className="stats">
            <div>
              <dt>accounts</dt>
              <dd>{graph.stats.accounts}</dd>
            </div>
            <div>
              <dt>vendors</dt>
              <dd>{graph.stats.counterparties}</dd>
            </div>
            <div>
              <dt>edges</dt>
              <dd>{graph.stats.edges}</dd>
            </div>
            <div>
              <dt>transactions</dt>
              <dd>{graph.stats.transactions}</dd>
            </div>
          </dl>
          <h3>Most active vendors</h3>
          <ol className="vendors">
            {topVendors?.map((n) => (
              <li key={n.id}>
                <span>{n.display_name}</span>
                <span className="muted">{n.tx_count} tx</span>
              </li>
            ))}
          </ol>
          <p className="muted small">Force-directed graph visualization: stretch goal. Data is already served by /api/graph.</p>
        </>
      ) : (
        <p className="muted">Loading graph…</p>
      )}
    </aside>
  )
}
