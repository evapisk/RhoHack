"""Rebuild the whole pipeline state from the checked-in fixture, and decide at startup
whether to run against live Rho or the fixture.

Two problems this solves.

Repeatability: injecting the same transaction three times used to score alert, then
warn, then normal, because the vendor stopped being new and its own amount dragged the
baseline up. Rehearsing the demo destroyed it. Rebuilding from a fixed baseline before
each scenario makes every run identical by construction rather than by arithmetic.

Survivability: the venue wifi is not a dependency worth betting a pitch on. If Rho is
unreachable, or there is no API key at all, the same 72 transactions are replayed from
disk and the demo is identical.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.bus import TOPIC_TRANSACTIONS_NEW, EventBus
from app.config import Settings
from app.graph.graph import TransactionGraph
from app.models import Transaction, TransactionEvent
from app.pipeline import Pipeline
from app.poller.simulator import load_fixture_accounts, load_fixture_transactions
from app.realtime.broadcaster import SSEBroadcaster
from app.rho_client import RhoClient, RhoError

log = logging.getLogger(__name__)

DataSource = Literal["rho", "fixture"]


@dataclass
class SeedResult:
    accounts: int
    transactions: int
    internal_resolved: int
    elapsed_ms: float
    source: DataSource

    def as_dict(self) -> dict[str, Any]:
        return {
            "accounts": self.accounts,
            "transactions": self.transactions,
            "internal_resolved": self.internal_resolved,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "source": self.source,
        }


async def seed_from_fixture(
    *,
    graph: TransactionGraph,
    pipeline: Pipeline,
    bus: EventBus,
    fixtures_dir: Path,
    accounts: list[dict[str, Any]] | None = None,
    broadcaster: SSEBroadcaster | None = None,
    source: DataSource = "fixture",
) -> SeedResult:
    """Reset graph and pipeline, then replay every fixture row as history.

    Rows go out over the bus rather than straight into the pipeline, so this shares one
    code path with the live poller and reset state can never drift from live state.
    """
    started = time.perf_counter()
    rows = load_fixture_transactions(fixtures_dir)
    accounts = accounts if accounts is not None else load_fixture_accounts(fixtures_dir)

    # Tell open browsers to flush before the replay: the feed dedups on (id, status)
    # and these ids are the ones it already has, so otherwise nothing would appear.
    if broadcaster is not None:
        await broadcaster.publish_control("reset", {"reason": "seed", "source": source})

    graph.reset(accounts)
    pipeline.reset()
    graph.index_money_movements(rows)

    for row in rows:
        tx = Transaction.from_rho(row)
        await bus.publish(
            TOPIC_TRANSACTIONS_NEW,
            TransactionEvent(kind="new", transaction=tx, backfill=True, source="replay"),
        )

    result = SeedResult(
        accounts=len(accounts),
        transactions=len(rows),
        internal_resolved=graph.internal_resolved,
        elapsed_ms=(time.perf_counter() - started) * 1000,
        source=source,
    )
    log.info(
        "seeded from fixture: %d transactions, %d internal transfers resolved, %.0fms",
        result.transactions,
        result.internal_resolved,
        result.elapsed_ms,
    )
    return result


async def bootstrap(
    *,
    graph: TransactionGraph,
    pipeline: Pipeline,
    bus: EventBus,
    client: RhoClient,
    settings: Settings,
) -> tuple[DataSource, list[dict[str, Any]]]:
    """Decide between live Rho and the fixture, and seed accounts accordingly.

    `GET /accounts` doubles as the reachability probe: it is the smallest call that
    proves the token, the base URL and the network all work.
    """
    if settings.demo_offline:
        log.warning("DEMO_OFFLINE is set: running against the checked-in fixture")
        return "fixture", load_fixture_accounts(settings.fixtures_dir)

    problem = settings.api_key_problem()
    if problem:
        log.warning("%s Falling back to fixture data.", problem)
        return "fixture", load_fixture_accounts(settings.fixtures_dir)

    try:
        accounts = await client.list_accounts()
    except (RhoError, OSError) as exc:
        log.warning("Rho unreachable (%s). Falling back to fixture data.", exc)
        return "fixture", load_fixture_accounts(settings.fixtures_dir)

    graph.seed_accounts(accounts)
    log.info("seeded %d accounts from %s", len(accounts), settings.rho_base_url)
    return "rho", accounts
