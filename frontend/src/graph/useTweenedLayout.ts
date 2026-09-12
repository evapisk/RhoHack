import { useEffect, useRef, useState } from 'react'
import type { PositionedGraph } from './layout'

const DURATION_MS = 600
const easeOut = (t: number) => 1 - (1 - t) ** 3

const prefersReducedMotion = () =>
  typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)

export interface TweenedGraph {
  graph: PositionedGraph | null
  /** Nodes that were not on screen before this layout arrived. */
  entering: Set<string>
}

/** Positions part-way from `from` to `target`. Edges are rebuilt so lines follow their nodes. */
function interpolate(target: PositionedGraph, from: Map<string, { x: number; y: number }>, t: number): PositionedGraph {
  const nodes = target.nodes.map((n) => {
    const p = from.get(n.id)
    return p ? { ...n, x: p.x + (n.x - p.x) * t, y: p.y + (n.y - p.y) * t } : n
  })
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const edges = target.edges.map((e) => {
    const s = byId.get(e.source)!
    const d = byId.get(e.target)!
    return { ...e, x1: s.x, y1: s.y, x2: d.x, y2: d.y }
  })
  return { ...target, nodes, edges, byId }
}

/**
 * Animates between successive layouts without changing where they end up.
 *
 * layoutGraph stays deterministic, so the settled picture is identical to the
 * un-animated one; this only fills in the frames between the old positions and the
 * new ones. A new vendor appears at its final spot and is flagged `entering` so CSS
 * can grow it in. The very first layout is shown as-is: animating all ~48 nodes in on
 * page load reads as noise, not as "something arrived".
 */
export function useTweenedLayout(layout: PositionedGraph | null): TweenedGraph {
  const [state, setState] = useState<TweenedGraph>({ graph: layout, entering: new Set() })
  // What is on screen right now, including mid-tween positions, so a layout that lands
  // during a tween starts from where the nodes visibly are.
  const shown = useRef<PositionedGraph | null>(layout)

  useEffect(() => {
    const prev = shown.current
    if (!layout || !prev || prev === layout || prefersReducedMotion()) {
      shown.current = layout
      setState({ graph: layout, entering: new Set() })
      return
    }

    const from = new Map(prev.nodes.map((n) => [n.id, { x: n.x, y: n.y }]))
    const entering = new Set(layout.nodes.filter((n) => !from.has(n.id)).map((n) => n.id))
    const start = performance.now()
    let raf = 0

    const step = (now: number) => {
      const t = Math.min(1, (now - start) / DURATION_MS)
      const graph = t >= 1 ? layout : interpolate(layout, from, easeOut(t))
      shown.current = graph
      setState({ graph, entering })
      if (t < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [layout])

  return state
}
