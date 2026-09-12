import { useEffect, useRef, useState } from 'react'
import { formatMoney } from '../api'
import type { StoryStage, StoryState } from '../story'

const prefersReducedMotion = () => Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)

/** Counts up to `target` so the audience watches the number move, not just change. */
function useCountUp(target: number, ms = 900): number {
  const [value, setValue] = useState(target)
  const shown = useRef(target)

  useEffect(() => {
    const from = shown.current
    if (from === target) return
    if (prefersReducedMotion()) {
      shown.current = target
      setValue(target)
      return
    }
    const start = performance.now()
    let raf = 0
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / ms)
      const v = Math.round(from + (target - from) * (1 - (1 - t) ** 3))
      shown.current = v
      setValue(v)
      if (t < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])

  return value
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

function outcome(s: StoryStage): string {
  if (s.status === 'pending') return 'waiting'
  if (s.status === 'active') return 'scoring…'
  const score = s.score?.toFixed(2) ?? ''
  if (s.level === 'alert') return `alert · ${score}`
  if (s.level === 'warn') return `warn · ${score}`
  return `cleared · ${score}`
}

function detail(s: StoryStage): string {
  const amount = s.amountMinor !== undefined ? formatMoney(s.amountMinor) : ''
  if (s.impersonates && s.similarity) {
    return `${amount} · ${Math.round(s.similarity * 100)}% like '${s.impersonates}'`
  }
  return s.counterparty ? `${amount} · ${s.counterparty}` : amount
}

interface Props {
  story: StoryState
  onDismiss: () => void
}

/** The attack replay: a stage strip, one line of narration, and the money kept safe. */
export function AttackStory({ story, onDismiss }: Props) {
  const intercepted = useCountUp(story.interceptedMinor)
  const active = story.current !== null ? story.stages[story.current] : undefined
  const lastScored = [...story.stages].reverse().find((s) => s.status === 'scored')

  const narration = story.finished
    ? `Attack stopped: ${plural(story.finished.alerts, 'fraudulent payment')} worth ${formatMoney(
        story.interceptedMinor,
      )} flagged while still pending, and ${plural(story.finished.cleared, 'routine payment')} cleared untouched.`
    : (active ?? lastScored)?.narration ?? "Replaying this business's real payment history. Then the attack begins."

  return (
    <section className={`story ${story.finished ? 'finished' : 'playing'}`} aria-live="polite">
      <div className="story-top">
        <div className="story-title">
          <span className="cap">{story.finished ? 'attack replay · complete' : 'attack replay · live'}</span>
          <h2>{story.title}</h2>
        </div>
        <div className="story-meter">
          <span className="cap">intercepted</span>
          <b>{formatMoney(intercepted)}</b>
          <span className="story-meter-sub">flagged while still pending</span>
        </div>
        <button className="story-close" onClick={onDismiss} aria-label="Hide the attack replay">
          ×
        </button>
      </div>

      <ol className="story-stages">
        {story.stages.map((s, i) => (
          <li key={i} className={['stage', s.status, s.level ?? ''].join(' ')}>
            <span className="stage-num">{String(i + 1).padStart(2, '0')}</span>
            <span className="stage-title">{s.title}</span>
            <span className="stage-outcome">{outcome(s)}</span>
            {s.status === 'scored' && <span className="stage-detail">{detail(s)}</span>}
          </li>
        ))}
      </ol>

      <p className="story-narration">{narration}</p>
    </section>
  )
}
