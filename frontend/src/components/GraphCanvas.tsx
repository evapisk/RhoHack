import type { PositionedGraph, PositionedNode } from '../graph/layout'
import type { Level } from '../types'

interface LabelBox {
  x: number
  y: number
  halfW: number
  halfH: number
}

const boxesOverlap = (a: LabelBox, b: LabelBox): boolean =>
  Math.abs(a.x - b.x) < a.halfW + b.halfW && Math.abs(a.y - b.y) < a.halfH + b.halfH

const estHalfLabelWidth = (text: string): number => Math.min(90, 8 + text.length * 3.3)

/**
 * The force layout only keeps *nodes* apart; nothing stops two nearby nodes' labels
 * (each independently offset below/above its own node) from landing on the same spot,
 * which happens routinely here since several accounts share a display name (two
 * distinct accounts both called "Rewards", etc.) and cluster together via internal
 * transfers. Greedily place each label at the first non-colliding candidate offset,
 * visiting higher-priority nodes (already fixed, then busiest) first so the labels
 * that matter most keep their natural spot below the node.
 */
function placeLabels(
  candidates: { node: PositionedNode; text: string; baseY: number }[],
  fixed: LabelBox[] = [],
): Map<string, number> {
  const placed: LabelBox[] = [...fixed]
  const result = new Map<string, number>()
  const halfH = 8
  for (const { node, text, baseY } of candidates) {
    const halfW = estHalfLabelWidth(text)
    const candidateOffsets = [baseY, -baseY, baseY + 15, -baseY - 15, baseY + 30]
    let chosen = candidateOffsets[0]
    for (const offset of candidateOffsets) {
      const box: LabelBox = { x: node.x, y: node.y + offset, halfW, halfH }
      chosen = offset
      if (!placed.some((p) => boxesOverlap(p, box))) break
    }
    result.set(node.id, chosen)
    placed.push({ x: node.x, y: node.y + chosen, halfW, halfH })
  }
  return result
}

export interface GraphHighlight {
  accountNodeId: string | null
  counterpartyNodeId: string | null
  /** The real vendor being impersonated, when there is one. */
  lookalikeNodeId: string | null
  ratio: number | null
  level: Level
}

interface Props {
  layout: PositionedGraph
  highlight: GraphHighlight | null
  /** Nodes that just appeared; CSS grows them in. */
  entering?: Set<string>
  /** Nodes with a live alert; they ring until the feed resets. */
  alertNodeIds?: Set<string>
}

const NONE: Set<string> = new Set()

/** Pure renderer. Positions come from layoutGraph; nothing here fetches or simulates. */
export function GraphCanvas({ layout, highlight, entering = NONE, alertNodeIds = NONE }: Props) {
  const focus = new Set(
    [highlight?.accountNodeId, highlight?.counterpartyNodeId, highlight?.lookalikeNodeId].filter(
      (v): v is string => Boolean(v),
    ),
  )
  const dimmed = focus.size > 0
  const level = highlight?.level ?? 'normal'

  const isFocusEdge = (source: string, target: string) => focus.has(source) && focus.has(target)

  // With ~48 nodes in a sidebar, labelling everything is an unreadable pile. Label the
  // focused subgraph when there is one; otherwise, always label accounts -- there are
  // only a handful and they're the graph's fixed anchors, an unlabeled square reads as
  // a mistake -- plus the busiest few vendors. (A blended "top 7 by tx_count regardless
  // of kind" used to pick almost entirely accounts anyway, since one account touches
  // many vendors, so vendors went unlabeled by default; this just makes that explicit.)
  const busiestVendors = new Set(
    [...layout.nodes]
      .filter((n) => n.kind === 'counterparty')
      .sort((a, b) => b.tx_count - a.tx_count)
      .slice(0, 5)
      .map((n) => n.id),
  )
  const showLabel = (n: PositionedNode) => (dimmed ? focus.has(n.id) : n.kind === 'account' || busiestVendors.has(n.id))

  // "Crescent Property Group" and "Crescent Property Group LLC" both truncate to the
  // same string at 22 characters, which destroys the one comparison this picture
  // exists to make. Focused labels get room, and the impersonated vendor is placed
  // above its node while the impostor sits below, so the two never collide.
  const labelText = (n: PositionedNode) => {
    const max = focus.has(n.id) ? 34 : 20
    return n.label.length > max ? `${n.label.slice(0, max - 1)}…` : n.label
  }

  // The lookalike vendor's above-node placement is deliberate and non-negotiable (see
  // comment above); treat it as a fixed obstacle and greedily place every other shown
  // label around it, busiest/focused first, so the labels that matter most keep their
  // natural spot and only the lower-priority ones get displaced when two collide.
  const shown = layout.nodes.filter(showLabel)
  const lookalike = shown.find((n) => n.id === highlight?.lookalikeNodeId)
  const fixedBoxes: LabelBox[] = lookalike
    ? [{ x: lookalike.x, y: lookalike.y - lookalike.r - 18, halfW: estHalfLabelWidth(labelText(lookalike)), halfH: 8 }]
    : []
  const movable = shown
    .filter((n) => n.id !== highlight?.lookalikeNodeId)
    .sort((a, b) => (focus.has(b.id) ? 1 : 0) - (focus.has(a.id) ? 1 : 0) || b.tx_count - a.tx_count)
  const labelOffsets = placeLabels(
    movable.map((node) => ({ node, text: labelText(node), baseY: node.r + 15 })),
    fixedBoxes,
  )
  const labelY = (n: PositionedNode) =>
    n.id === highlight?.lookalikeNodeId ? -n.r - 18 : (labelOffsets.get(n.id) ?? n.r + 15)

  return (
    <svg
      className={`graph-canvas ${dimmed ? 'has-focus' : ''}`}
      viewBox={`0 0 ${layout.width} ${layout.height}`}
      role="img"
      aria-label="Transaction graph: accounts and the counterparties they move money with"
    >
      <g className="edges">
        {layout.edges.map((e) => {
          const focused = e.lookalike || isFocusEdge(e.source, e.target)
          const classes = [
            'edge',
            e.internal ? 'internal' : '',
            e.lookalike ? `lookalike ${level}` : '',
            focused ? 'focused' : '',
          ]
          return (
            <line
              key={e.id}
              className={classes.join(' ')}
              x1={e.x1}
              y1={e.y1}
              x2={e.x2}
              y2={e.y2}
              strokeWidth={e.lookalike ? 2.5 : 1 + Math.log1p(e.tx_count)}
            />
          )
        })}
      </g>

      {/* The payoff: the impostor sits beside the vendor it imitates, joined in red. */}
      {layout.edges
        .filter((e) => e.lookalike && e.label)
        .map((e) => {
          // The impostor sits ~30px from the vendor it imitates, so a label at the
          // midpoint lands on top of a node. Push it out along the edge normal.
          const dx = e.x2 - e.x1
          const dy = e.y2 - e.y1
          const len = Math.hypot(dx, dy) || 1
          const off = 30
          return (
            <text
              key={`${e.id}-label`}
              className="edge-label"
              x={(e.x1 + e.x2) / 2 + (-dy / len) * off}
              y={(e.y1 + e.y2) / 2 + (dx / len) * off}
              textAnchor="middle"
            >
              {e.label}
            </text>
          )
        })}

      <g className="nodes">
        {layout.nodes.map((n) => {
          const focused = focus.has(n.id)
          const classes = [
            'node',
            n.kind,
            n.ambiguous ? 'ambiguous' : '',
            focused ? 'focused' : '',
            focused && n.id === highlight?.counterpartyNodeId ? level : '',
            n.id === highlight?.lookalikeNodeId ? 'impersonated' : '',
            entering.has(n.id) ? 'entering' : '',
            alertNodeIds.has(n.id) ? 'pulse' : '',
          ]
          return (
            <g key={n.id} className={classes.join(' ')} transform={`translate(${n.x} ${n.y})`}>
              {/* A separate element, drawn under the node: a CSS transform on the <g> itself
                  would replace its translate() and throw the node to the corner. */}
              {alertNodeIds.has(n.id) && <circle className="pulse-ring" r={n.r} />}
              {n.kind === 'account' ? (
                <rect x={-n.r} y={-n.r * 0.72} width={n.r * 2} height={n.r * 1.44} rx={4} />
              ) : (
                <circle r={n.r} />
              )}
              {showLabel(n) && (
                <text className="node-label" y={labelY(n)} textAnchor="middle">
                  {labelText(n)}
                </text>
              )}
            </g>
          )
        })}
      </g>
    </svg>
  )
}
