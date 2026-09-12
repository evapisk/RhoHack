"""Resolve a transaction's counterparty to the right kind of graph node.

Rho exposes no counterparty id, only `counterparty_name`. For an internal transfer
that name is literally another of your own accounts ("Cash (Checking)"), so the naive
graph draws a fake vendor node and every account-to-account path dies in the middle.
On the sandbox fixture that is 28 of 72 transactions and 6 phantom vendors.

Four tiers, in order of confidence:

  exact           the sibling leg of the same `money_movement_id` names a different
                  account, with a mirrored amount. Certain. 14 of 28 fixture rows.
  unique_name     the display name matches exactly one known account.
  ambiguous_name  the display name matches several ("Credit Account" covers four
                  sibling accounts). Collapse them into ONE merged node rather than
                  picking a representative: a merged node is honest, a chosen id is
                  fake precision.
  external        a real vendor. The common case.

Only `external` counterparties enter the vendor namespace, so internal transfers can
no longer trigger first-contact alerts or be mistaken for an impersonated vendor.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Literal

from app.models import NodeKind, Resolution, Transaction, normalize_counterparty


def account_node_id(account_id: str) -> str:
    return f"account:{account_id}"


def counterparty_node_id(key: str) -> str:
    return f"counterparty:{key}"


def group_node_id(name_key: str) -> str:
    return f"account:group:{re.sub(r'[^a-z0-9]+', '-', name_key).strip('-')}"


@dataclass(frozen=True)
class ResolvedCounterparty:
    node_id: str
    node_kind: NodeKind
    stats_key: str  # key into the counterparty stat dicts
    display_name: str
    is_internal: bool
    confidence: Resolution
    member_ids: tuple[str, ...] = ()  # populated for merged ambiguous nodes

    @property
    def is_external(self) -> bool:
        return self.confidence == "external"


class AccountResolver:
    """Maps a transaction's counterparty onto a node id. Cheap and stateless per call."""

    def __init__(self) -> None:
        self._by_name: dict[str, list[str]] = defaultdict(list)  # normalized name -> account ids
        self._name_of: dict[str, str] = {}  # account id -> display name
        self._movement_peer: dict[str, dict[str, str]] = {}  # mm id -> {account_id: peer_account_id}

    # -- seeding ---------------------------------------------------------------

    def seed(self, accounts: Iterable[dict[str, Any]]) -> None:
        for acct in accounts:
            acct_id = acct.get("id")
            name = acct.get("account_name")
            if not acct_id or not name:
                continue
            key = normalize_counterparty(name)
            if acct_id not in self._by_name[key]:
                self._by_name[key].append(acct_id)
            self._name_of[acct_id] = name

    def index_money_movements(self, rows: Iterable[dict[str, Any]]) -> int:
        """Pre-pass over a batch of raw Rho rows, pairing the two legs of each transfer.

        Both legs of an internal transfer share a `money_movement_id` and carry mirrored
        amounts on different accounts. Seeing them together is the only way to resolve a
        transfer with certainty, which is why this runs over a whole page before any row
        is published. A single live transaction has no sibling and falls through to the
        name tiers, which is correct.
        """
        by_movement: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            mm = row.get("money_movement_id")
            if mm and row.get("account_id"):
                by_movement[mm].append(row)

        paired = 0
        for mm, legs in by_movement.items():
            accounts = {leg["account_id"] for leg in legs}
            if len(accounts) < 2:
                continue
            peers = self._movement_peer.setdefault(mm, {})
            for leg in legs:
                mine = leg["account_id"]
                others = [a for a in accounts if a != mine]
                if len(others) == 1:
                    peers[mine] = others[0]
                    paired += 1
        return paired

    def clear(self) -> None:
        self._by_name.clear()
        self._name_of.clear()
        self._movement_peer.clear()

    # -- resolution ------------------------------------------------------------

    def resolve(self, tx: Transaction) -> ResolvedCounterparty:
        key = tx.counterparty_key

        # Tier 1: the sibling leg tells us the peer account outright.
        peer = (self._movement_peer.get(tx.money_movement_id or "") or {}).get(tx.account_id)
        if peer and peer != tx.account_id:
            return self._account_result(peer, "exact", tx)

        matches = [a for a in self._by_name.get(key, []) if a != tx.account_id]
        if len(matches) == 1:
            return self._account_result(matches[0], "unique_name", tx)
        if len(matches) > 1:
            # Tier 3: one merged node standing for the whole sibling group.
            display = self._name_of.get(matches[0]) or tx.counterparty_name or key
            return ResolvedCounterparty(
                node_id=group_node_id(key),
                node_kind="account",
                stats_key=group_node_id(key),
                display_name=display,
                is_internal=True,
                confidence="ambiguous_name",
                member_ids=tuple(sorted(matches)),
            )

        return ResolvedCounterparty(
            node_id=counterparty_node_id(key),
            node_kind="counterparty",
            stats_key=key,
            display_name=tx.counterparty_name or "Unknown",
            is_internal=False,
            confidence="external",
        )

    def _account_result(self, account_id: str, confidence: Resolution, tx: Transaction) -> ResolvedCounterparty:
        node_id = account_node_id(account_id)
        return ResolvedCounterparty(
            node_id=node_id,
            node_kind="account",
            stats_key=node_id,
            display_name=self._name_of.get(account_id) or tx.counterparty_name or account_id,
            is_internal=True,
            confidence=confidence,
            member_ids=(account_id,),
        )
