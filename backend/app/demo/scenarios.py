"""Named demo scenarios, each one click and each repeatable.

Every scenario reseeds from the fixture before injecting, so click 1 and click 10 score
against a byte-identical baseline. The alternative, unwinding a scenario's own footprint
afterwards, cannot work: Welford's accumulator has no inverse without keeping every
sample, and an injected amount also moves the account and population baselines.

The reset is shown in the UI rather than hidden. Someone who watches the feed clear and
refill understands why the number is stable; someone who watches scores drift on repeat
clicks concludes the model is unreliable.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.bus import TOPIC_TRANSACTIONS_NEW, EventBus
from app.demo.seed import seed_from_fixture
from app.graph.graph import TransactionGraph
from app.models import Level, ScoredTransaction, TransactionEvent
from app.pipeline import Pipeline
from app.poller.simulator import InjectRequest, make_injected_transaction
from app.realtime.broadcaster import SSEBroadcaster

log = logging.getLogger(__name__)

# Sandbox account ids, stable in the fixture.
CASH_CHECKING = "30000000-0000-4000-8000-000000000002"
PRIMARY_CHECKING = "30000000-0000-4000-8000-000000000006"
CREDIT_ACCOUNT = "30000000-0000-4000-8000-000000000009"


@dataclass(frozen=True)
class ScenarioStep:
    request: InjectRequest
    offset_seconds: float = 0.0  # relative to now; negative means earlier


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    blurb: str  # one line for the audience: what they are about to watch
    expected_level: Level
    steps: tuple[ScenarioStep, ...] = field(default_factory=tuple)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "blurb": self.blurb,
            "expected_level": self.expected_level,
            "step_count": len(self.steps),
        }


SCENARIOS: dict[str, Scenario] = {
    "lookalike_vendor": Scenario(
        id="lookalike_vendor",
        title="Vendor impersonation",
        blurb=(
            "This business pays Crescent Property Group $3,391 in rent by check. "
            "An invoice now arrives from 'Crescent Property Group LLC', by wire, with new bank details."
        ),
        expected_level="alert",
        steps=(
            ScenarioStep(
                InjectRequest(
                    counterparty_name="Crescent Property Group LLC",
                    amount=-3_840_000,
                    account_id=CASH_CHECKING,
                    transaction_type="wire_out",
                    memo="Annual lease prepayment, updated remittance details",
                )
            ),
        ),
    ),
    "large_payment": Scenario(
        id="large_payment",
        title="Large payment, unknown vendor",
        blurb="A quarter of a million dollars leaves Primary Checking for a vendor never paid before.",
        expected_level="alert",
        steps=(
            ScenarioStep(
                InjectRequest(
                    counterparty_name="Totally New Vendor LLC",
                    amount=-25_000_000,
                    account_id=PRIMARY_CHECKING,
                    transaction_type="ach_debit",
                    memo="Consulting retainer",
                )
            ),
        ),
    ),
    "quiet_normal": Scenario(
        id="quiet_normal",
        title="Routine spend (stays green)",
        blurb=(
            "A $51 card charge at a vendor this business already uses. "
            "The answer to 'does it just flag everything?'"
        ),
        expected_level="normal",
        steps=(
            ScenarioStep(
                InjectRequest(
                    counterparty_name="Northstar Office Supply",
                    amount=-5_100,
                    account_id=CREDIT_ACCOUNT,
                    transaction_type="card_debit",
                    card_name="Lucas Bennett",
                )
            ),
        ),
    ),
}


def get_scenario(scenario_id: str) -> Scenario | None:
    return SCENARIOS.get(scenario_id)


def list_scenarios() -> list[dict[str, Any]]:
    return [s.summary() for s in SCENARIOS.values()]


_LEVEL_ORDER: dict[str, int] = {"normal": 0, "warn": 1, "alert": 2}


async def run_scenario(
    scenario: Scenario,
    *,
    graph: TransactionGraph,
    pipeline: Pipeline,
    bus: EventBus,
    broadcaster: SSEBroadcaster,
    fixtures_dir: Path,
    accounts: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    started = time.perf_counter()

    await seed_from_fixture(
        graph=graph,
        pipeline=pipeline,
        bus=bus,
        fixtures_dir=fixtures_dir,
        accounts=accounts,
        broadcaster=broadcaster,
    )
    await broadcaster.publish_control(
        "scenario", {"scenario_id": scenario.id, "title": scenario.title, "blurb": scenario.blurb}
    )

    results: list[ScoredTransaction] = []
    for step in scenario.steps:
        tx = make_injected_transaction(step.request, accounts or [])
        if step.offset_seconds:
            tx = tx.model_copy(
                update={"initiated_at": tx.initiated_at.fromtimestamp(
                    tx.initiated_at.timestamp() + step.offset_seconds, tz=tx.initiated_at.tzinfo
                )}
            )
        await bus.publish(
            TOPIC_TRANSACTIONS_NEW,
            TransactionEvent(kind="new", transaction=tx, backfill=False, source="inject"),
        )
        scored = pipeline.last
        if scored is not None and scored.event.transaction.id == tx.id:
            results.append(scored)

    actual = "normal"
    for r in results:
        if _LEVEL_ORDER[r.anomaly.level] > _LEVEL_ORDER[actual]:
            actual = r.anomaly.level

    log.info("scenario %s -> %s (expected %s)", scenario.id, actual, scenario.expected_level)
    return {
        "scenario_id": scenario.id,
        "title": scenario.title,
        "blurb": scenario.blurb,
        "reset": True,
        "results": results,
        "expected_level": scenario.expected_level,
        "actual_level": actual,
        "matched": actual == scenario.expected_level,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }
