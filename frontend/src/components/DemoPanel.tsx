import { useEffect, useState } from 'react'
import { fetchScenarios, resetDemo, runScenario } from '../api'
import type { ScenarioRun, ScenarioSummary } from '../types'

interface Props {
  /** Called before a run so the feed can clear; the backend reseeds from the fixture. */
  onReset: () => void
  onResult: (run: ScenarioRun) => void
}

/**
 * The demo is driven from here, not from a terminal. Every scenario reseeds first, so
 * pressing the same button ten times produces the same score ten times.
 */
export function DemoPanel({ onReset, onResult }: Props) {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [last, setLast] = useState<ScenarioRun | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchScenarios()
      .then(setScenarios)
      .catch((e) => setError(String(e)))
  }, [])

  const run = async (id: string) => {
    setBusy(id)
    setError(null)
    try {
      onReset()
      const result = await runScenario(id)
      setLast(result)
      onResult(result)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(null)
    }
  }

  const reset = async () => {
    setBusy('__reset__')
    setError(null)
    try {
      onReset()
      setLast(null)
      await resetDemo()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="demo-panel">
      <div className="demo-buttons">
        {scenarios.map((s) => (
          <button
            key={s.id}
            className={`demo-btn ${s.expected_level}`}
            onClick={() => run(s.id)}
            disabled={busy !== null}
            title={s.blurb}
          >
            {busy === s.id ? 'running…' : s.title}
          </button>
        ))}
        <button className="demo-btn reset" onClick={reset} disabled={busy !== null}>
          {busy === '__reset__' ? 'resetting…' : 'Reset'}
        </button>
      </div>

      {error && <p className="error small">{error}</p>}

      {last && (
        <div className="demo-result">
          <p className="blurb">{last.blurb}</p>
          <span className={`chip ${last.matched ? 'ok' : 'bad'} ${last.actual_level}`}>
            {last.actual_level} {last.results[0] ? last.results[0].anomaly.score.toFixed(2) : ''}
            {last.matched ? ' ✓ as expected' : ` ✗ expected ${last.expected_level}`}
          </span>
        </div>
      )}
    </section>
  )
}
