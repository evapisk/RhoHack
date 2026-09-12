import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from 'd3-force'
import type { SimulationLinkDatum, SimulationNodeDatum } from 'd3-force'
import type { GraphEdge, GraphNode, GraphPayload } from '../types'

export interface PositionedNode extends GraphNode, SimulationNodeDatum {
  x: number
  y: number
  r: number
  label: string
}

export interface PositionedEdge {
  id: string
  source: string
  target: string
  x1: number
  y1: number
  x2: number
  y2: number
  tx_count: number
  internal: boolean
  /** Synthesised client-side to join an impostor to the vendor it imitates. */
  lookalike?: boolean
  label?: string
}

export interface PositionedGraph {
  nodes: PositionedNode[]
  edges: PositionedEdge[]
  byId: Map<string, PositionedNode>
  width: number
  height: number
}

export interface ExtraLink {
  source: string
  target: string
  label?: string
}

interface LinkDatum extends SimulationLinkDatum<PositionedNode> {
  edge?: GraphEdge
  internal: boolean
  lookalike: boolean
  label?: string
}

const nodeLabel = (n: GraphNode): string =>
  (n.display_name as string) || (n.name as string) || n.id.replace(/^(account|counterparty):/, '')

export const nodeRadius = (n: GraphNode): number =>
  n.kind === 'account' ? 9 + Math.min(9, Math.sqrt(n.tx_count)) : 5 + 3 * Math.sqrt(Math.max(n.tx_count, 1))

/**
 * Lay the graph out synchronously, to a fixed tick count.
 *
 * Deliberately not animated: initial positions are seeded on a circle by index and the
 * simulation is stepped to completion in one go, so the same data always produces the
 * same picture. A layout that looks different in the rehearsal and in the run is worse
 * than a slightly less pretty one.
 */
export function layoutGraph(
  payload: GraphPayload,
  opts: { width: number; height: number; extraLinks?: ExtraLink[]; ticks?: number },
): PositionedGraph {
  const { width, height, extraLinks = [], ticks = 420 } = opts

  const nodes: PositionedNode[] = payload.nodes.map((n, i) => {
    const angle = (i / Math.max(payload.nodes.length, 1)) * Math.PI * 2
    const ring = n.kind === 'account' ? Math.min(width, height) * 0.17 : Math.min(width, height) * 0.38
    return {
      ...n,
      r: nodeRadius(n),
      label: nodeLabel(n),
      x: width / 2 + Math.cos(angle) * ring,
      y: height / 2 + Math.sin(angle) * ring,
    }
  })

  const present = new Set(nodes.map((n) => n.id))
  const links: LinkDatum[] = payload.edges
    .filter((e) => present.has(e.source) && present.has(e.target))
    .map((e) => ({
      source: e.source,
      target: e.target,
      edge: e,
      internal: Boolean(e.internal),
      lookalike: false,
    }))

  for (const extra of extraLinks) {
    if (present.has(extra.source) && present.has(extra.target)) {
      links.push({ source: extra.source, target: extra.target, internal: false, lookalike: true, label: extra.label })
    }
  }

  const sim = forceSimulation(nodes)
    .force(
      'link',
      forceLink<PositionedNode, LinkDatum>(links)
        .id((d) => d.id)
        // Pull an impostor tight against the vendor it imitates, so the two land adjacent.
        .distance((d) => (d.lookalike ? 46 : d.internal ? 46 : 74))
        .strength((d) => (d.lookalike ? 1 : 0.5)),
    )
    .force('charge', forceManyBody().strength(-150).distanceMax(260))
    // forceX/forceY rather than forceCenter: this graph has several disconnected
    // components, and a single centring force lets them drift apart until the clamp
    // stacks them along the edges. Independent axis springs keep each one in frame.
    .force('x', forceX<PositionedNode>(width / 2).strength(0.06))
    .force('y', forceY<PositionedNode>(height / 2).strength(0.09))
    .force(
      'collide',
      forceCollide<PositionedNode>()
        .radius((d) => d.r + (d.kind === 'account' ? 16 : 11))
        .strength(0.9),
    )
  sim.stop()
  sim.tick(ticks)

  const pad = 16
  for (const n of nodes) {
    n.x = Math.max(pad + n.r, Math.min(width - pad - n.r, n.x ?? width / 2))
    n.y = Math.max(pad + n.r, Math.min(height - pad - n.r, n.y ?? height / 2))
  }

  const byId = new Map(nodes.map((n) => [n.id, n]))
  const edges: PositionedEdge[] = links.map((l, i) => {
    const s = typeof l.source === 'object' ? (l.source as PositionedNode) : byId.get(String(l.source))!
    const t = typeof l.target === 'object' ? (l.target as PositionedNode) : byId.get(String(l.target))!
    return {
      id: `${s.id}->${t.id}#${i}`,
      source: s.id,
      target: t.id,
      x1: s.x,
      y1: s.y,
      x2: t.x,
      y2: t.y,
      tx_count: l.edge?.tx_count ?? 1,
      internal: l.internal,
      lookalike: l.lookalike,
      label: l.label,
    }
  })

  return { nodes, edges, byId, width, height }
}

/** Signature that changes only when the topology does, so layout is not recomputed per row. */
export const topologySignature = (p: GraphPayload | null, extra: string): string =>
  p ? `${p.nodes.length}:${p.edges.length}:${p.stats.transactions}:${extra}` : `empty:${extra}`
