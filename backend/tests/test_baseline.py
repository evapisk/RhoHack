"""The pitch numbers, pinned exactly.

test_scenarios.py proves the scores don't drift between runs; this proves they are the
numbers we say out loud. A feature that moves any of them must change this file on
purpose, in the same commit, with the reason in the message.

Two level counts, because there are two replay paths:
  app seed path (seed_from_fixture)   indexes money movements first  -> 14 warn / 4 alert
  make graph-demo (replay_fixture)    resolves sibling legs by name  -> 15 warn / 3 alert
"""

from pathlib import Path

import pytest

from app.bus import EventBus
from app.demo.scenarios import get_scenario, run_scenario
from app.demo.seed import seed_from_fixture
from app.graph.demo import level_counts, replay_fixture
from app.graph.graph import TransactionGraph
from app.pipeline import Pipeline
from app.poller.simulator import load_fixture_accounts
from app.realtime.broadcaster import SSEBroadcaster
from app.scoring.zscore import ZScoreScorer

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

SCENARIO_SCORES = {
    "lookalike_vendor": 0.9417,
    "large_payment": 0.8226,
    "quiet_normal": 0.2969,
}


@pytest.fixture
def rig():
    bus = EventBus()
    graph = TransactionGraph()
    pipeline = Pipeline(graph, ZScoreScorer(), bus)
    broadcaster = SSEBroadcaster()
    accounts = load_fixture_accounts(FIXTURES)
    return bus, graph, pipeline, broadcaster, accounts


@pytest.mark.parametrize("scenario_id,expected", sorted(SCENARIO_SCORES.items()))
async def test_scenario_score_is_the_pitched_number(rig, scenario_id, expected):
    bus, graph, pipeline, broadcaster, accounts = rig
    run = await run_scenario(
        get_scenario(scenario_id),
        graph=graph, pipeline=pipeline, bus=bus, broadcaster=broadcaster,
        fixtures_dir=FIXTURES, accounts=accounts,
    )
    assert run["results"][0].anomaly.score == expected


async def test_app_seed_path_levels(rig):
    bus, graph, pipeline, _, accounts = rig
    await seed_from_fixture(graph=graph, pipeline=pipeline, bus=bus, fixtures_dir=FIXTURES, accounts=accounts)
    assert pipeline.counters["processed"] == 72
    assert (pipeline.counters["warn"], pipeline.counters["alert"]) == (14, 4)


def test_graph_demo_path_levels():
    _, _, _, scored = replay_fixture(FIXTURES)
    assert level_counts(scored) == {"normal": 54, "warn": 15, "alert": 3}
