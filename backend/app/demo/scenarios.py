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

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

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

ScenarioKind = Literal["scenario", "story"]

# A story holds on its final frame this long before announcing the totals, so the last
# alert gets a beat on screen before the finale replaces its narration.
FINALE_PAUSE_SECONDS = 2.5

# Indirection so tests can replace the pauses without patching asyncio globally.
_sleep = asyncio.sleep


@dataclass(frozen=True)
class ScenarioStep:
    request: InjectRequest
    offset_seconds: float = 0.0  # relative to now; negative means earlier
    # Story stages only: the label on the timeline strip, the line the audience reads
    # while it plays, and how long to hold before it so the narrator can talk.
    title: str = ""
    narration: str = ""
    pause_before_seconds: float = 0.0


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    blurb: str  # one line for the audience: what they are about to watch
    expected_level: Level
    steps: tuple[ScenarioStep, ...] = field(default_factory=tuple)
    # "story" plays its steps paced and narrated, publishing a `story` SSE frame per stage.
    kind: ScenarioKind = "scenario"

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "blurb": self.blurb,
            "expected_level": self.expected_level,
            "step_count": len(self.steps),
            "kind": self.kind,
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
    "attack_story": Scenario(
        id="attack_story",
        kind="story",
        title="Anatomy of a vendor-fraud attack",
        blurb=(
            "Two routine payments go out. Then an attacker impersonates the landlord, "
            "gets caught, and pivots to a second vendor."
        ),
        expected_level="alert",
        steps=(
            ScenarioStep(
                InjectRequest(
                    counterparty_name="Northstar Office Supply",
                    amount=-5_100,
                    account_id=CREDIT_ACCOUNT,
                    transaction_type="card_debit",
                    card_name="Lucas Bennett",
                ),
                title="Business as usual",
                narration="A $51 office-supply charge at a vendor this business already uses. It clears without a sound.",
                pause_before_seconds=2.5,
            ),
            ScenarioStep(
                InjectRequest(
                    counterparty_name="Crescent Property Group",
                    amount=-339_100,
                    account_id=CASH_CHECKING,
                    transaction_type="check_payment",
                    memo="Monthly rent",
                ),
                title="Rent goes out",
                narration="$3,391 to the real landlord, by check, exactly like the last two times. Still green.",
                pause_before_seconds=5.0,
            ),
            ScenarioStep(
                InjectRequest(
                    counterparty_name="Crescent Property Group LLC",
                    amount=-3_840_000,
                    account_id=CASH_CHECKING,
                    transaction_type="wire_out",
                    memo="Annual lease prepayment, updated remittance details",
                ),
                title="The impostor invoices",
                narration=(
                    "An invoice from 'Crescent Property Group LLC': new bank details, an urgent $38,400 wire. "
                    "One extra word in the name."
                ),
                pause_before_seconds=6.0,
            ),
            ScenarioStep(
                InjectRequest(
                    counterparty_name="N0rthstar Office Supply",
                    amount=-1_275_000,
                    account_id=PRIMARY_CHECKING,
                    transaction_type="ach_debit",
                    memo="Q3 bulk order, new remittance account",
                ),
                title="The attacker pivots",
                narration=(
                    "Blocked on the landlord, they impersonate the office-supply vendor instead: "
                    "a zero where the O should be, $12,750."
                ),
                pause_before_seconds=7.0,
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
    pace: float = 1.0,
) -> dict[str, Any]:
    """Reseed, then play every step through the live pipeline.

    `pace` scales a story's pauses: 1.0 is stage speed, 0 plays it instantly (tests).
    Single-step scenarios have no pauses, so pace does not affect them.
    """
    started = time.perf_counter()
    story = scenario.kind == "story"

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

    if story:
        await _publish_story(
            broadcaster, scenario, "started",
            stages=[{"title": s.title, "narration": s.narration} for s in scenario.steps],
        )

    results: list[ScoredTransaction] = []
    for index, step in enumerate(scenario.steps):
        if step.pause_before_seconds and pace > 0:
            await _sleep(step.pause_before_seconds * pace)
        if story:
            await _publish_story(broadcaster, scenario, "active", stage=index)
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
            if story:
                await _publish_story(broadcaster, scenario, "scored", stage=index, **_stage_outcome(scored))

    actual = "normal"
    for r in results:
        if _LEVEL_ORDER[r.anomaly.level] > _LEVEL_ORDER[actual]:
            actual = r.anomaly.level

    if story:
        if pace > 0:
            await _sleep(FINALE_PAUSE_SECONDS * pace)
        await _publish_story(
            broadcaster, scenario, "finished",
            **_story_totals(results),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        )

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


async def _publish_story(broadcaster: SSEBroadcaster, scenario: Scenario, status: str, **fields: Any) -> None:
    await broadcaster.publish_control(
        "story", {"scenario_id": scenario.id, "title": scenario.title, "status": status, **fields}
    )


def _stage_outcome(scored: ScoredTransaction) -> dict[str, Any]:
    tx = scored.event.transaction
    match = scored.features.lookalike
    return {
        "transaction_id": tx.id,
        "transaction_status": tx.status,
        "level": scored.anomaly.level,
        "score": scored.anomaly.score,
        "amount_minor": tx.abs_amount_minor,
        "counterparty_name": tx.counterparty_name,
        "impersonates": match.matched_display_name if match else None,
        "similarity": round(match.ratio, 3) if match else None,
    }


def _story_totals(results: list[ScoredTransaction]) -> dict[str, Any]:
    """"Intercepted" is money on alert-level payments, all still pending when flagged."""
    levels = [r.anomaly.level for r in results]
    return {
        "intercepted_minor": sum(r.event.transaction.abs_amount_minor for r in results if r.anomaly.level == "alert"),
        "alerts": levels.count("alert"),
        "warns": levels.count("warn"),
        "cleared": levels.count("normal"),
    }
