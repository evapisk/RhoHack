import { formatMoney, formatTime } from '../api'
import type { ScoredTransaction } from '../types'

interface Props {
  item: ScoredTransaction
  open: boolean
  onToggle: () => void
}

export function TransactionRow({ item, open, onToggle }: Props) {
  const { event, anomaly, features } = item
  const tx = event.transaction
  const debit = tx.amount.amount < 0
  const impostor = features.lookalike
  const classes = [
    'row',
    anomaly.level,
    event.backfill ? 'backfill' : 'live',
    open ? 'open' : '',
    impostor ? 'impostor' : '',
    features.is_internal_transfer ? 'internal' : '',
  ]

  return (
    <li className={classes.join(' ')} onClick={onToggle} title="Click for details">
      <div className="row-main">
        <span className="time">{formatTime(tx.initiated_at)}</span>
        <span className="counterparty">
          {tx.counterparty_name ?? 'Unknown counterparty'}
          {impostor && <span className="tag impostor">impostor?</span>}
          {!impostor && features.is_new_counterparty && !features.is_internal_transfer && (
            <span className="tag new">new vendor</span>
          )}
          {features.is_internal_transfer && <span className="tag internal">internal</span>}
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
          {event.source !== 'rho' && event.source !== 'replay' && (
            <span className={`tag ${event.source}`}>{event.source}</span>
          )}
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
          <div className="detail-left">
            {impostor && (
              <p className="impostor-callout">
                Impersonates <b>{impostor.matched_display_name}</b> at{' '}
                {Math.round(impostor.ratio * 100)}% name similarity. That vendor has been paid{' '}
                {impostor.matched_tx_count} time{impostor.matched_tx_count === 1 ? '' : 's'},{' '}
                {formatMoney(impostor.matched_total_minor)} in total.
              </p>
            )}
            {anomaly.reasons.length ? (
              <ul className="reasons">
                {anomaly.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            ) : (
              <p className="muted">No anomaly signals. Looks routine.</p>
            )}
            {(tx.memo || tx.note) && <p className="memo">{tx.memo ?? tx.note}</p>}
          </div>

          <dl className="components">
            {Object.entries(anomaly.components).map(([k, v]) => (
              <div key={k}>
                <dt>{k === 'new_counterparty_gated' ? 'new vendor (not escalated)' : k}</dt>
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
              <dt>accounts using peer</dt>
              <dd>{features.counterparty_account_count}</dd>
            </div>
            <div>
              <dt>resolved as</dt>
              <dd>{features.counterparty_resolution}</dd>
            </div>
          </dl>
        </div>
      )}
    </li>
  )
}
