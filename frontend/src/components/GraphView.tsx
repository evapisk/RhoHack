import { useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D, { type NodeObject, type LinkObject } from 'react-force-graph-2d'
import { fetchGraph } from '../api'
import type { GraphPayload, Level, ScoredTransaction } from './../types'

/**
 * Force-directed view of /api/graph: accounts + vendors as nodes, money movement as
 * edges. Vendor nodes are colored by the worst anomaly level ever seen on a
 * transaction with them (from the same `transactions` the feed renders), so a vendor
 * that triggered one alert stays visibly red even after later normal transactions --
 * this is meant to be the thing a judge points at during the pitch.
 */

// Mirrors app/models.py normalize_counterparty -- must match exactly to map a
// transaction's counterparty_name onto the same node id the backend graph uses.
function normalizeCounterparty(name: string | null | undefined): string {
  const cleaned = (name ?? '').trim().replace(/\s+/g, ' ').toLowerCase()
  return cleaned || 'unknown'
}

const LEVEL_RANK: Record<Level, number> = { normal: 0, warn: 1, alert: 2 }
const LEVEL_COLOR: Record<Level, string> = { normal: '#3fa86b', warn: '#f59e0b', alert: '#ef4444' }
const ACCOUNT_COLOR = '#60a5fa'

interface WorstByNode {
  level: Level
  score: number
}

function worstLevelsByNode(transactions: ScoredTransaction[]): Map<string, WorstByNode> {
  const worst = new Map<string, WorstByNode>()
  const consider = (nodeId: string, level: Level, score: number) => {
    const prev = worst.get(nodeId)
    if (!prev || LEVEL_RANK[level] > LEVEL_RANK[prev.level]) worst.set(nodeId, { level, score })
  }
  for (const s of transactions) {
    const tx = s.event.transaction
    consider(`account:${tx.account_id}`, s.anomaly.level, s.anomaly.score)
    consider(`counterparty:${normalizeCounterparty(tx.counterparty_name)}`, s.anomaly.level, s.anomaly.score)
  }
  return worst
}

interface GraphNodeDatum extends NodeObject {
  id: string
  kind: 'account' | 'counterparty'
  name?: string
  display_name?: string
  tx_count: number
}

export function GraphView({ transactions }: { transactions: ScoredTransaction[] }) {
  const [graph, setGraph] = useState<GraphPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  // react-force-graph-2d's imperative-handle generics don't infer cleanly against a
  // custom node type here; `any` is fine for a ref only used to call zoomToFit().
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fgRef = useRef<any>(null)

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

  const worst = useMemo(() => worstLevelsByNode(transactions), [transactions])

  const graphData = useMemo(() => {
    if (!graph) return { nodes: [] as GraphNodeDatum[], links: [] as LinkObject[] }
    const nodes: GraphNodeDatum[] = graph.nodes.map((n) => ({ ...n }) as GraphNodeDatum)
    const links: LinkObject[] = graph.edges.map((e) => ({ ...e, source: e.source, target: e.target }))
    return { nodes, links }
  }, [graph])

  const topVendors = graph?.nodes
    .filter((n) => n.kind === 'counterparty')
    .sort((a, b) => b.tx_count - a.tx_count)
    .slice(0, 5)

  const flaggedVendorCount = useMemo(
    () => [...worst.entries()].filter(([id, w]) => id.startsWith('counterparty:') && w.level !== 'normal').length,
    [worst],
  )

  return (
    <aside className="graph-panel">
      <h2>Graph</h2>
      {error && <p className="error">{error}</p>}
      {graph ? (
        <>
          <div className="graph-canvas">
            <ForceGraph2D
              ref={fgRef}
              graphData={graphData}
              width={392}
              height={340}
              backgroundColor="#0b0f14"
              nodeId="id"
              nodeVal={(n) => Math.sqrt(((n as GraphNodeDatum).tx_count || 1)) * 1.6 + 2}
              nodeColor={(n) => {
                const node = n as GraphNodeDatum
                if (node.kind === 'account') return ACCOUNT_COLOR
                const w = worst.get(node.id)
                return w ? LEVEL_COLOR[w.level] : '#3b4657'
              }}
              nodeLabel={(n) => {
                const node = n as GraphNodeDatum
                const label = node.kind === 'account' ? node.name : node.display_name
                const w = worst.get(node.id)
                return `${label ?? node.id} · ${node.tx_count} tx${w ? ` · worst: ${w.level}` : ''}`
              }}
              linkColor={() => 'rgba(148,163,184,0.35)'}
              linkDirectionalArrowLength={3}
              linkDirectionalArrowRelPos={1}
              linkWidth={1}
              cooldownTicks={80}
              onEngineStop={() => fgRef.current?.zoomToFit(300, 24)}
            />
          </div>
          <div className="graph-legend">
            <span>
              <i className="dot account" /> account
            </span>
            <span>
              <i className="dot normal" /> normal
            </span>
            <span>
              <i className="dot warn" /> warn
            </span>
            <span>
              <i className="dot alert" /> alert
            </span>
          </div>
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
              <dt>flagged</dt>
              <dd className={flaggedVendorCount ? 'alert-count' : ''}>{flaggedVendorCount}</dd>
            </div>
          </dl>
          <h3>Most active vendors</h3>
          <ol className="vendors">
            {topVendors?.map((n) => {
              const w = worst.get(n.id)
              return (
                <li key={n.id}>
                  <span className={w && w.level !== 'normal' ? `level-${w.level}` : undefined}>{n.display_name}</span>
                  <span className="muted">{n.tx_count} tx</span>
                </li>
              )
            })}
          </ol>
        </>
      ) : (
        <p className="muted">Loading graph…</p>
      )}
    </aside>
  )
}
