"""Demo scenarios must be repeatable: a rehearsal must not change what the judges see."""

from pathlib import Path

import pytest

from app.bus import EventBus
from app.demo.scenarios import SCENARIOS, get_scenario, list_scenarios, run_scenario
from app.demo.seed import seed_from_fixture
from app.graph.graph import TransactionGraph
from app.pipeline import Pipeline
from app.poller.simulator import load_fixture_accounts
from app.realtime.broadcaster import SSEBroadcaster
from app.scoring.zscore import ZScoreScorer

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def rig():
    bus = EventBus()
    graph = TransactionGraph()
    pipeline = Pipeline(graph, ZScoreScorer(), bus)
    broadcaster = SSEBroadcaster()
    accounts = load_fixture_accounts(FIXTURES)
    return bus, graph, pipeline, broadcaster, accounts


async def _run(rig, scenario_id: str):
    bus, graph, pipeline, broadcaster, accounts = rig
    return await run_scenario(
        get_scenario(scenario_id),
        graph=graph, pipeline=pipeline, bus=bus, broadcaster=broadcaster,
        fixtures_dir=FIXTURES, accounts=accounts, pace=0,
    )


@pytest.mark.parametrize("scenario_id", sorted(SCENARIOS))
async def test_scenario_is_repeatable(rig, scenario_id):
    """The regression this exists for: the old inject scored 0.82, then 0.51, then 0.4987."""
    runs = [await _run(rig, scenario_id) for _ in range(5)]

    levels = {r["actual_level"] for r in runs}
    scores = {round(r["results"][0].anomaly.score, 6) for r in runs}
    assert len(levels) == 1, f"{scenario_id} drifted across repeats: {levels}"
    assert len(scores) == 1, f"{scenario_id} score drifted across repeats: {scores}"


@pytest.mark.parametrize("scenario_id", sorted(SCENARIOS))
async def test_scenario_matches_its_advertised_level(rig, scenario_id):
    run = await _run(rig, scenario_id)
    assert run["matched"], (
        f"{scenario_id}: advertised {run['expected_level']}, produced {run['actual_level']}"
    )


async def test_impersonation_scenario_names_the_real_vendor(rig):
    run = await _run(rig, "lookalike_vendor")
    scored = run["results"][0]

    assert scored.anomaly.level == "alert"
    assert scored.features.lookalike is not None
    assert scored.features.lookalike.matched_display_name == "Crescent Property Group"
    assert scored.features.lookalike.matched_tx_count == 2
    assert any("impersonation" in r.lower() for r in scored.anomaly.reasons)


async def test_quiet_scenario_stays_green(rig):
    """The answer to 'does it just flag everything?'"""
    run = await _run(rig, "quiet_normal")
    assert run["results"][0].anomaly.level == "normal"


async def test_reset_returns_to_the_same_baseline(rig):
    bus, graph, pipeline, broadcaster, accounts = rig

    first = await seed_from_fixture(graph=graph, pipeline=pipeline, bus=bus,
                                    fixtures_dir=FIXTURES, accounts=accounts)
    baseline = (graph.stats(), dict(pipeline.counters))

    await _run(rig, "large_payment")  # pollute
    second = await seed_from_fixture(graph=graph, pipeline=pipeline, bus=bus,
                                     fixtures_dir=FIXTURES, accounts=accounts)

    assert (graph.stats(), dict(pipeline.counters)) == baseline
    assert first.transactions == second.transactions == 72
    assert first.internal_resolved == second.internal_resolved == 28


def test_every_scenario_is_listed_with_a_story(rig):
    listed = list_scenarios()
    assert len(listed) == len(SCENARIOS)
    for s in listed:
        assert s["blurb"] and s["title"] and s["expected_level"]
