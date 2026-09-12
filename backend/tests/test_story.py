"""The attack replay: a paced, narrated scenario whose every stage is scored for real."""

from pathlib import Path

import pytest

from app.bus import EventBus
from app.demo import scenarios as scenarios_module
from app.demo.scenarios import FINALE_PAUSE_SECONDS, get_scenario, list_scenarios, run_scenario
from app.graph.graph import TransactionGraph
from app.pipeline import Pipeline
from app.poller.simulator import load_fixture_accounts
from app.realtime.broadcaster import SSEBroadcaster
from app.scoring.zscore import ZScoreScorer

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
STORY = "attack_story"


class RecordingBroadcaster(SSEBroadcaster):
    """Keeps every control frame, so the timeline the browser would draw can be asserted."""

    def __init__(self) -> None:
        super().__init__()
        self.controls: list[tuple[str, dict]] = []

    async def publish_control(self, event, payload):
        self.controls.append((event, payload))
        await super().publish_control(event, payload)


@pytest.fixture
def rig():
    bus = EventBus()
    graph = TransactionGraph()
    pipeline = Pipeline(graph, ZScoreScorer(), bus)
    return bus, graph, pipeline, RecordingBroadcaster(), load_fixture_accounts(FIXTURES)


async def play(rig, scenario_id: str = STORY, pace: float = 0.0):
    bus, graph, pipeline, broadcaster, accounts = rig
    run = await run_scenario(
        get_scenario(scenario_id),
        graph=graph, pipeline=pipeline, bus=bus, broadcaster=broadcaster,
        fixtures_dir=FIXTURES, accounts=accounts, pace=pace,
    )
    return run, [payload for event, payload in broadcaster.controls if event == "story"]


async def test_stages_score_as_narrated(rig):
    run, _ = await play(rig)

    assert [r.anomaly.level for r in run["results"]] == ["normal", "normal", "alert", "alert"]
    impostor, pivot = run["results"][2:]
    assert impostor.features.lookalike.matched_display_name == "Crescent Property Group"
    assert pivot.features.lookalike.matched_display_name == "Northstar Office Supply"
    assert run["matched"] and run["actual_level"] == "alert"


async def test_story_frames_drive_the_timeline_in_order(rig):
    run, story = await play(rig)

    assert [(e["status"], e.get("stage")) for e in story] == [
        ("started", None),
        ("active", 0), ("scored", 0),
        ("active", 1), ("scored", 1),
        ("active", 2), ("scored", 2),
        ("active", 3), ("scored", 3),
        ("finished", None),
    ]

    stages = story[0]["stages"]
    assert [s["title"] for s in stages] == [step.title for step in get_scenario(STORY).steps]
    assert all(s["title"] and s["narration"] for s in stages)

    scored = [e for e in story if e["status"] == "scored"]
    assert [e["transaction_id"] for e in scored] == [r.event.transaction.id for r in run["results"]]
    assert [e["level"] for e in scored] == ["normal", "normal", "alert", "alert"]
    assert scored[2]["impersonates"] == "Crescent Property Group" and scored[2]["similarity"] >= 0.8
    assert scored[0]["impersonates"] is None

    finished = story[-1]
    assert finished["intercepted_minor"] == 3_840_000 + 1_275_000
    assert (finished["alerts"], finished["warns"], finished["cleared"]) == (2, 0, 2)


async def test_story_frames_come_after_the_reset(rig):
    """The browser clears the timeline on reset; a story frame before it would be wiped."""
    await play(rig)
    events = [event for event, _ in rig[3].controls]
    assert events[:3] == ["reset", "scenario", "story"]


async def test_pace_scales_every_pause(rig, monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(scenarios_module, "_sleep", fake_sleep)

    await play(rig, pace=0.5)
    steps = get_scenario(STORY).steps
    assert slept == [s.pause_before_seconds * 0.5 for s in steps] + [FINALE_PAUSE_SECONDS * 0.5]

    slept.clear()
    await play(rig, pace=0)
    assert slept == []


async def test_single_scenarios_publish_no_story_frames(rig, monkeypatch):
    async def no_sleep(seconds: float) -> None:
        raise AssertionError("a single-step scenario must not pause")

    monkeypatch.setattr(scenarios_module, "_sleep", no_sleep)
    _, story = await play(rig, "lookalike_vendor", pace=1.0)
    assert story == []


def test_story_is_listed_as_a_story():
    kinds = {s["id"]: s["kind"] for s in list_scenarios()}
    assert kinds[STORY] == "story"
    assert {kind for sid, kind in kinds.items() if sid != STORY} == {"scenario"}


def test_story_is_short_enough_to_narrate():
    total = sum(s.pause_before_seconds for s in get_scenario(STORY).steps) + FINALE_PAUSE_SECONDS
    assert 15 <= total <= 40
