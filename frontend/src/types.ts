// Mirrors backend/app/models.py. Keep the two in sync.

export type Level = 'normal' | 'warn' | 'alert'
export type EventKind = 'new' | 'updated'
export type EventSource = 'rho' | 'replay' | 'inject'

export interface Money {
  amount: number // signed minor units (cents); negative = money leaving the account
  currency: string
}

export interface Transaction {
  id: string
  money_movement_id: string | null
  account_id: string
  account_name: string | null
  account_type: string | null
  transaction_type: string
  amount: Money
  status: string
  initiated_at: string
  posted_at: string | null
  user_id: string | null
  user_full_name: string | null
  card_id: string | null
  card_name: string | null
  counterparty_name: string | null
  memo: string | null
  note: string | null
}

export interface TransactionEvent {
  kind: EventKind
  transaction: Transaction
  backfill: boolean
  source: EventSource
  previous_status: string | null
}

export type NodeKind = 'account' | 'counterparty'
export type Resolution = 'exact' | 'unique_name' | 'ambiguous_name' | 'external'

/** An existing vendor whose name a brand-new counterparty is impersonating. */
export interface LookalikeMatch {
  matched_key: string
  matched_node_id: string
  matched_display_name: string
  ratio: number
  shared_tokens: string[]
  matched_tx_count: number
  matched_total_minor: number
  matched_last_seen: string | null
}

export interface GraphFeatures {
  is_new_counterparty: boolean
  counterparty_tx_count: number
  counterparty_mean_log_amount: number | null
  counterparty_std_log_amount: number | null
  account_tx_count: number
  account_mean_log_amount: number | null
  account_std_log_amount: number | null
  hours_since_last_tx_to_counterparty: number | null
  account_out_degree: number
  account_tx_last_hour: number
  population_tx_count: number
  population_mean_log_amount: number | null
  population_std_log_amount: number | null
  is_internal_transfer: boolean
  counterparty_node_kind: NodeKind
  counterparty_resolution: Resolution
  account_node_id: string
  counterparty_node_id: string
  counterparty_account_count: number
  lookalike: LookalikeMatch | null
}

export interface AnomalyScore {
  score: number // 0..1
  level: Level
  reasons: string[]
  components: Record<string, number>
  model: string
}

export interface ScoredTransaction {
  event: TransactionEvent
  features: GraphFeatures
  anomaly: AnomalyScore
  scored_at: string
}

export interface GraphNode {
  id: string
  kind: NodeKind
  name?: string
  display_name?: string
  tx_count: number
  total_minor?: number
  account_type?: string | null
  ambiguous?: boolean
  member_ids?: string[]
  [key: string]: unknown
}

export interface GraphEdge {
  source: string
  target: string
  tx_count: number
  total_minor: number
  internal?: boolean
  [key: string]: unknown
}

export interface GraphPayload {
  nodes: GraphNode[]
  edges: GraphEdge[]
  stats: {
    accounts: number
    counterparties: number
    edges: number
    transactions: number
    internal_resolved: number
  }
}

export interface ScenarioSummary {
  id: string
  title: string
  blurb: string
  expected_level: Level
  step_count: number
  /** Older backends omit it; treat that as a single scenario. */
  kind?: ScenarioKind
}

export type ScenarioKind = 'scenario' | 'story'

export interface ScenarioRun {
  scenario_id: string
  title: string
  blurb: string
  reset: boolean
  results: ScoredTransaction[]
  expected_level: Level
  actual_level: Level
  matched: boolean
  elapsed_ms: number
}

export interface StoryStageInfo {
  title: string
  narration: string
}

interface StoryFrame {
  scenario_id: string
  title: string
}

/** One `story` SSE frame, mirroring _publish_story in backend/app/demo/scenarios.py. */
export type StoryEvent =
  | (StoryFrame & { status: 'started'; stages: StoryStageInfo[] })
  | (StoryFrame & { status: 'active'; stage: number })
  | (StoryFrame & {
      status: 'scored'
      stage: number
      transaction_id: string
      transaction_status: string
      level: Level
      score: number
      amount_minor: number
      counterparty_name: string | null
      impersonates: string | null
      similarity: number | null
    })
  | (StoryFrame & {
      status: 'finished'
      intercepted_minor: number
      alerts: number
      warns: number
      cleared: number
      elapsed_ms: number
    })

export interface DemoResetResponse {
  reset: boolean
  source: 'rho' | 'fixture'
  accounts: number
  transactions: number
  internal_resolved: number
  elapsed_ms: number
}

export interface Health {
  status: string
  scorer: string
  data_source: 'rho' | 'fixture'
  offline: boolean
  rho_base_url: string
  sse_clients: number
  poller: Record<string, unknown> | null
  tasks: Record<string, string>
}
