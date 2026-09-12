"""Tavily vendor verification: sharpens an existing flag, never creates one, never breaks scoring."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.bus import EventBus
from app.demo.scenarios import get_scenario, run_scenario
from app.demo.seed import seed_from_fixture
from app.graph.graph import TransactionGraph
from app.models import AnomalyScore
from app.pipeline import Pipeline
from app.poller.simulator import load_fixture_accounts
from app.realtime.broadcaster import SSEBroadcaster
from app.scoring.vendor_lookup import (
    TAVILY_SEARCH_URL,
    VERIFICATION_WEIGHT,
    VendorVerification,
    VendorVerifier,
    apply_vendor_verification,
    verify_vendor,
)
from app.scoring.zscore import ZScoreScorer

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

FOUND = {
    "results": [
        {"title": "Unrelated listing", "url": "https://example.org/x", "content": "nope", "score": 0.4},
        {
            "title": "Crescent Property Group | Commercial Real Estate",
            "url": "https://www.crescentpropertygroup.com/about",
            "content": "Crescent Property Group manages office space.",
            "score": 0.9,
        },
    ]
}
NOT_FOUND = {
    "results": [
        {"title": "How to spot invoice fraud", "url": "https://bank.example/blog", "content": "...", "score": 0.3},
    ]
}


class CountingTransport(httpx.AsyncBaseTransport):
    """Answers every call with a fixed response and records what was asked."""

    def __init__(self, status: int = 200, body: object = NOT_FOUND, delay: float = 0.0) -> None:
        self.status = status
        self.body = body
        self.delay = delay
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        content = self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()
        return httpx.Response(self.status, content=content, headers={"content-type": "application/json"})


def client_for(transport: httpx.AsyncBaseTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport)


async def check(name: str, transport: CountingTransport, *, api_key: str = "tvly-test", timeout: float = 1.0):
    async with client_for(transport) as client:
        return await verify_vendor(name, api_key=api_key, client=client, timeout=timeout)


# -- verify_vendor ------------------------------------------------------------


async def test_found_when_a_result_title_carries_the_name():
    transport = CountingTransport(body=FOUND)
    result = await check("Crescent Property Group LLC", transport)

    assert result is not None and result.found
    assert result.domain == "crescentpropertygroup.com"
    assert result.result_count == 2

    (request,) = transport.requests
    assert str(request.url) == TAVILY_SEARCH_URL
    assert request.method == "POST"
    assert request.headers["authorization"] == "Bearer tvly-test"
    assert json.loads(request.content)["query"] == "Crescent Property Group LLC"


async def test_found_by_domain_alone():
    body = {"results": [{"title": "Home", "url": "https://www.northstarofficesupply.com/", "content": ""}]}
    result = await check("Northstar Office Supply", CountingTransport(body=body))
    assert result is not None and result.found and result.domain == "northstarofficesupply.com"


async def test_domain_missing_a_token_is_not_a_match():
    body = {"results": [{"title": "Home", "url": "https://northstaroffice.com/", "content": ""}]}
    result = await check("Northstar Office Supply", CountingTransport(body=body))
    assert result is not None and not result.found


async def test_not_found_when_no_result_names_the_vendor():
    result = await check("Crescent Property Group LLC", CountingTransport(body=NOT_FOUND))
    assert result == VendorVerification(name="Crescent Property Group LLC", found=False, result_count=1)


async def test_timeout_degrades_to_none():
    result = await check("Crescent Property Group LLC", CountingTransport(delay=0.5), timeout=0.05)
    assert result is None


@pytest.mark.parametrize("status", [401, 429, 432, 500])
async def test_http_error_degrades_to_none(status):
    assert await check("Crescent Property Group LLC", CountingTransport(status=status, body={})) is None


async def test_unreadable_body_degrades_to_none():
    assert await check("Crescent Property Group LLC", CountingTransport(body=b"<html>oops")) is None


async def test_no_key_makes_no_call():
    transport = CountingTransport()
    assert await check("Crescent Property Group LLC", transport, api_key="") is None
    assert transport.requests == []


async def test_network_failure_degrades_to_none():
    class Broken(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("no route to host")

    assert await check("Crescent Property Group LLC", Broken()) is None


# -- VendorVerifier cache -----------------------------------------------------


async def test_verifier_caches_answers_but_not_failures():
    transport = CountingTransport(status=500, body={})
    verifier = VendorVerifier("tvly-test", client=client_for(transport))

    assert await verifier.verify("Crescent Property Group LLC") is None
    transport.status, transport.body = 200, NOT_FOUND
    first = await verifier.verify("Crescent Property Group LLC")
    again = await verifier.verify("crescent  property group llc")

    assert first is not None and first == again
    assert len(transport.requests) == 2  # the failure was retried once, the answer was not
    await verifier.aclose()


# -- apply_vendor_verification ------------------------------------------------


def anomaly(score: float, level: str, evidence: float) -> AnomalyScore:
    return AnomalyScore(
        score=score, level=level, reasons=["existing"], components={"combined_evidence": evidence}, model="zscore"
    )


NOT_FOUND_V = VendorVerification(name="X LLC", found=False)


def test_not_found_nudges_an_alert_upward():
    before = anomaly(0.9417, "alert", 8.529)
    after = apply_vendor_verification(before, NOT_FOUND_V, scale=3.0, alert=0.75)

    assert after.level == "alert"
    assert 0.9417 < after.score <= 1.0
    assert after.components["vendor_verification"] == VERIFICATION_WEIGHT
    assert after.reasons == ["existing", "Web check: no web presence found for 'X LLC'"]


def test_warn_is_never_promoted_to_alert():
    before = anomaly(0.74, "warn", 4.04)  # +0.6 would compute 0.787, past the alert line
    after = apply_vendor_verification(before, NOT_FOUND_V, scale=3.0, alert=0.75)

    assert after.level == "warn"
    assert 0.74 <= after.score < 0.75


def test_normal_gains_a_reason_but_no_evidence():
    before = anomaly(0.2969, "normal", 1.057)
    after = apply_vendor_verification(before, NOT_FOUND_V, scale=3.0, alert=0.75)

    assert (after.level, after.score) == ("normal", 0.2969)
    assert after.components["vendor_verification"] == 0.0
    assert "no web presence" in after.reasons[-1]


def test_found_explains_without_moving_the_score():
    before = anomaly(0.9417, "alert", 8.529)
    found = VendorVerification(name="X LLC", found=True, domain="x.com")
    after = apply_vendor_verification(before, found, scale=3.0, alert=0.75)

    assert (after.level, after.score) == ("alert", 0.9417)
    assert after.reasons[-1] == "Web check: found a web presence for 'X LLC' (x.com)"


def test_no_verification_leaves_the_score_untouched():
    before = anomaly(0.9417, "alert", 8.529)
    assert apply_vendor_verification(before, None, scale=3.0, alert=0.75) is before


# -- pipeline integration -----------------------------------------------------


@pytest.fixture
def verified_rig():
    transport = CountingTransport(body=NOT_FOUND)
    bus = EventBus()
    graph = TransactionGraph()
    verifier = VendorVerifier("tvly-test", client=client_for(transport))
    pipeline = Pipeline(graph, ZScoreScorer(), bus, verifier=verifier)
    return transport, bus, graph, pipeline, SSEBroadcaster(), load_fixture_accounts(FIXTURES)


async def _run(rig, scenario_id):
    _, bus, graph, pipeline, broadcaster, accounts = rig
    run = await run_scenario(
        get_scenario(scenario_id),
        graph=graph, pipeline=pipeline, bus=bus, broadcaster=broadcaster,
        fixtures_dir=FIXTURES, accounts=accounts,
    )
    return run["results"][0]


async def test_impostor_inject_gains_the_web_check_and_keeps_its_level(verified_rig):
    transport = verified_rig[0]
    scored = await _run(verified_rig, "lookalike_vendor")

    assert scored.anomaly.level == "alert"
    assert 0.9417 < scored.anomaly.score <= 1.0
    assert scored.anomaly.components["vendor_verification"] == VERIFICATION_WEIGHT
    assert "Web check: no web presence found for 'Crescent Property Group LLC'" in scored.anomaly.reasons
    assert scored.anomaly.components["lookalike_vendor"] == 4.5  # the graph signal is untouched
    assert len(transport.requests) == 1  # the injected row only; the 72-row reseed made none


async def test_repeat_runs_are_identical_and_make_no_further_calls(verified_rig):
    transport = verified_rig[0]
    scores = {(await _run(verified_rig, "lookalike_vendor")).anomaly.score for _ in range(3)}
    assert len(scores) == 1
    assert len(transport.requests) == 1  # cache survives the reseed between runs


async def test_known_vendor_is_never_looked_up(verified_rig):
    transport = verified_rig[0]
    scored = await _run(verified_rig, "quiet_normal")
    assert (scored.anomaly.level, scored.anomaly.score) == ("normal", 0.2969)
    assert "vendor_verification" not in scored.anomaly.components
    assert transport.requests == []


async def test_backfill_never_calls_tavily(verified_rig):
    transport, bus, graph, pipeline, _, accounts = verified_rig
    await seed_from_fixture(graph=graph, pipeline=pipeline, bus=bus, fixtures_dir=FIXTURES, accounts=accounts)

    assert transport.requests == []
    assert (pipeline.counters["warn"], pipeline.counters["alert"]) == (14, 4)


async def test_tavily_outage_scores_exactly_like_no_verifier():
    transport = CountingTransport(status=503, body={})
    bus = EventBus()
    graph = TransactionGraph()
    pipeline = Pipeline(graph, ZScoreScorer(), bus, verifier=VendorVerifier("tvly-test", client=client_for(transport)))
    scored = await _run((transport, bus, graph, pipeline, SSEBroadcaster(), load_fixture_accounts(FIXTURES)), "lookalike_vendor")

    assert scored.anomaly.score == 0.9417
    assert "vendor_verification" not in scored.anomaly.components
    assert len(transport.requests) == 1
