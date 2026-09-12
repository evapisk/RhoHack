import { useState } from 'react'
import { formatMoney, formatTime } from '../api'
import type { ScoredTransaction } from '../types'

interface Props {
  item: ScoredTransaction
}

export function TransactionRow({ item }: Props) {
  const [open, setOpen] = useState(false)
  const { event, anomaly, features } = item
  const tx = event.transaction
  const debit = tx.amount.amount < 0
  const classes = ['row', anomaly.level, event.backfill ? 'backfill' : 'live', open ? 'open' : '']

  return (
    <li className={classes.join(' ')} onClick={() => setOpen((v) => !v)} title="Click for details">
      <div className="row-main">
        <span className="time">{formatTime(tx.initiated_at)}</span>
        <span className="counterparty">
          {tx.counterparty_name ?? 'Unknown counterparty'}
          {features.is_new_counterparty && <span className="tag new">new vendor</span>}
        </span>
        <span className="account">
          {tx.account_name ?? tx.account_id}
          <span className="muted"> · {tx.transaction_type}</span>
          {tx.card_name && <span className="muted"> · {tx.card_name}</span>}
        </span>
        <span className={`amount ${debit ? 'debit' : 'credit'}`}>
          {debit ? '−' : '+'}
          {formatMoney(Math.abs(tx.amount.amount), tx.amount.currency)}
        </span>
        <span className={`status ${tx.status}`}>{tx.status}</span>
        <span className="score">
          <span className={`level ${anomaly.level}`}>{anomaly.level}</span>
          <span className="bar">
            <span className="fill" style={{ width: `${Math.round(anomaly.score * 100)}%` }} />
          </span>
          <span className="num">{anomaly.score.toFixed(2)}</span>
        </span>
        <span className="source">
          {event.source !== 'rho' && <span className={`tag ${event.source}`}>{event.source}</span>}
          {event.backfill && <span className="tag backfill">history</span>}
          {event.kind === 'updated' && (
            <span className="tag updated">
              {event.previous_status} → {tx.status}
            </span>
          )}
        </span>
      </div>
      {open && (
        <div className="row-detail">
          {anomaly.reasons.length ? (
            <ul className="reasons">
              {anomaly.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          ) : (
            <p className="muted">No anomaly signals. Looks routine.</p>
          )}
          <dl className="components">
            {Object.entries(anomaly.components).map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{v.toFixed(2)}</dd>
              </div>
            ))}
            <div>
              <dt>model</dt>
              <dd>{anomaly.model}</dd>
            </div>
            <div>
              <dt>vendor history</dt>
              <dd>{features.counterparty_tx_count} tx</dd>
            </div>
            <div>
              <dt>account history</dt>
              <dd>{features.account_tx_count} tx</dd>
            </div>
            <div>
              <dt>tx id</dt>
              <dd className="mono">{tx.id}</dd>
            </div>
          </dl>
          {(tx.memo || tx.note) && <p className="memo">{tx.memo ?? tx.note}</p>}
        </div>
      )}
    </li>
  )
}
