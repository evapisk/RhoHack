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
  kind: 'account' | 'counterparty'
  name?: string
  display_name?: string
  tx_count: number
  [key: string]: unknown
}

export interface GraphEdge {
  source: string
  target: string
  tx_count: number
  total_minor: number
  [key: string]: unknown
}

export interface GraphPayload {
  nodes: GraphNode[]
  edges: GraphEdge[]
  stats: { accounts: number; counterparties: number; edges: number; transactions: number }
}

export interface Health {
  status: string
  scorer: string
  rho_base_url: string
  sse_clients: number
  poller: Record<string, unknown> | null
  tasks: Record<string, string>
}
