import type { Level, StoryEvent, StoryStageInfo } from './types'

export type StageStatus = 'pending' | 'active' | 'scored'

export interface StoryStage extends StoryStageInfo {
  status: StageStatus
  level?: Level
  score?: number
  amountMinor?: number
  counterparty?: string | null
  impersonates?: string | null
  similarity?: number | null
}

export interface StoryState {
  scenarioId: string
  title: string
  stages: StoryStage[]
  /** Index of the stage being scored right now, if any. */
  current: number | null
  interceptedMinor: number
  finished: { alerts: number; warns: number; cleared: number } | null
}

/**
 * Fold one `story` SSE frame into the timeline. Pure, so React can replay it safely.
 * Only `started` creates a timeline; stray frames for another story are ignored.
 */
export function applyStoryEvent(state: StoryState | null, e: StoryEvent): StoryState | null {
  if (e.status === 'started') {
    return {
      scenarioId: e.scenario_id,
      title: e.title,
      stages: e.stages.map((s) => ({ ...s, status: 'pending' })),
      current: null,
      interceptedMinor: 0,
      finished: null,
    }
  }
  if (!state || state.scenarioId !== e.scenario_id) return state

  switch (e.status) {
    case 'active':
      return {
        ...state,
        current: e.stage,
        stages: state.stages.map((s, i) => (i === e.stage ? { ...s, status: 'active' } : s)),
      }
    case 'scored':
      return {
        ...state,
        interceptedMinor: state.interceptedMinor + (e.level === 'alert' ? e.amount_minor : 0),
        stages: state.stages.map((s, i) =>
          i === e.stage
            ? {
                ...s,
                status: 'scored',
                level: e.level,
                score: e.score,
                amountMinor: e.amount_minor,
                counterparty: e.counterparty_name,
                impersonates: e.impersonates,
                similarity: e.similarity,
              }
            : s,
        ),
      }
    case 'finished':
      return {
        ...state,
        current: null,
        interceptedMinor: e.intercepted_minor,
        finished: { alerts: e.alerts, warns: e.warns, cleared: e.cleared },
      }
  }
}
