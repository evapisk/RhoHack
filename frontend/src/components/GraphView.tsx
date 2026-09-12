import { useEffect, useMemo, useState } from 'react'
import { fetchGraph } from '../api'
import { layoutGraph, topologySignature, type ExtraLink } from '../graph/layout'
import type { GraphPayload } from '../types'
import { GraphCanvas, type GraphHighlight } from './GraphCanvas'

const WIDTH = 600
const HEIGHT = 520

interface Props {
  highlight: GraphHighlight | null
  /** Bumped by App whenever the backend state changed, so the graph refetches. */
  refreshKey: number
}

export function GraphView({ highlight, refreshKey }: Props) {
  const [graph, setGraph] = useState<GraphPayload | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchGraph()
      .then((g) => {
        if (cancelled) return
        setGraph(g)
        setError(null)
      })
      .catch((e) => !cancelled && setError(String(e)))
    return () => {
      cancelled = true
    }
  }, [refreshKey])

  const extraLinks: ExtraLink[] = useMemo(() => {
    if (!highlight?.lookalikeNodeId || !highlight.counterpartyNodeId) return []
    return [
      {
        source: highlight.counterpartyNodeId,
        target: highlight.lookalikeNodeId,
        label: highlight.ratio ? `${Math.round(highlight.ratio * 100)}% name match` : 'name match',
      },
    ]
  }, [highlight?.counterpartyNodeId, highlight?.lookalikeNodeId, highlight?.ratio])

  const signature = topologySignature(graph, extraLinks.map((l) => `${l.source}~${l.target}`).join())

  // Recompute only when the topology actually changes, not on every selected row.
  const layout = useMemo(
    () => (graph ? layoutGraph(graph, { width: WIDTH, height: HEIGHT, extraLinks }) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [signature],
  )

  return (
    <aside className="graph-panel">
      <div className="graph-head">
        <h2>Transaction graph</h2>
        <span className="legend">
          <i className="swatch account" /> accounts
          <i className="swatch counterparty" /> vendors
        </span>
      </div>

      {error && <p className="error">{error}</p>}

      {layout ? (
        <GraphCanvas layout={layout} highlight={highlight} />
      ) : (
        <p className="muted">Loading graph…</p>
      )}

      {graph && (
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
            <dt>transfers resolved</dt>
            <dd>{graph.stats.internal_resolved}</dd>
          </div>
        </dl>
      )}
      <p className="muted small">
        Dashed grey edges are transfers between this business's own accounts, resolved from
        counterparty names back to real accounts.
        {layout && layout.omittedCount > 0 && (
          <>
            {' '}
            {layout.omittedCount} account{layout.omittedCount === 1 ? '' : 's'} with no transactions omitted.
          </>
        )}
      </p>
    </aside>
  )
}
