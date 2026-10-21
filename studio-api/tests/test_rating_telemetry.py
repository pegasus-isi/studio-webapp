"""Chat reply ratings out to the collector (Grafana gauge, Langfuse span)."""

from __future__ import annotations

import asyncio

from services import rating_telemetry as rt


class _DB:
    def __init__(self, counts=None, consent=False):
        self._counts = counts or []
        self.consent = consent

    async def message_feedback_counts(self):
        return self._counts

    async def get_setting(self, key, default=None):
        return "1" if self.consent else "0"


def _attrs(kvs):
    return {a["key"]: next(iter(a["value"].values())) for a in kvs}


def test_gauge_sums_by_rating_model_agent_and_always_names_both_ratings():
    p = rt.metrics_payload(
        [{"rating": 1, "model": "m", "agent": "build", "n": 2},
         {"rating": 1, "model": None, "agent": None, "n": 1}],
        now_ns=1,
    )
    metric = p["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]
    assert metric["name"] == "studio.chat.reply_ratings"
    points = {
        (a["rating"], a["model"], a["agent"]): int(dp["asInt"])
        for dp in metric["gauge"]["dataPoints"]
        for a in [_attrs(dp["attributes"])]
    }
    assert points == {
        ("up", "m", "build"): 2,
        ("up", "unknown", "unknown"): 1,
        ("down", "unknown", "unknown"): 0,
    }


def test_resource_carries_the_user_and_says_no_content(monkeypatch):
    monkeypatch.setenv("JUPYTERHUB_USER", "al,ice=x")
    res = _attrs(rt.metrics_payload([], 1)["resourceMetrics"][0]["resource"]["attributes"])
    assert res["enduser.id"] == "al_ice_x"
    assert res["studio.content_capture"] == "false"
    assert res["service.name"] == "studio-api"


def test_span_lands_in_the_langfuse_session():
    p = rt.span_payload(session_id="ses_1", message_id="msg_1", rating=-1,
                        model="m", agent="a", now_ns=5)
    span = p["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    a = _attrs(span["attributes"])
    assert a["session.id"] == "ses_1"
    assert a["studio.rating"] == "-1" and a["studio.rating.label"] == "down"
    assert len(span["traceId"]) == 32 and len(span["spanId"]) == 16
    assert "input.value" not in a and "output.value" not in a


def _capture(monkeypatch):
    sent = []

    async def fake_post(path, payload):
        sent.append(path)
        return True

    monkeypatch.setattr(rt, "_post", fake_post)
    return sent


def test_nothing_is_sent_without_a_collector(monkeypatch):
    monkeypatch.delenv("STUDIO_OTLP_ENDPOINT", raising=False)
    sent = _capture(monkeypatch)
    asyncio.run(rt.export_vote(_DB(consent=True), session_id="s", message_id="m",
                               rating=1, model=None, agent=None))
    assert sent == []


def test_a_vote_sends_the_span_only_with_consent(monkeypatch):
    monkeypatch.setenv("STUDIO_OTLP_ENDPOINT", "http://collector:4318")
    sent = _capture(monkeypatch)
    vote = dict(session_id="s", message_id="m", rating=1, model=None, agent=None)
    asyncio.run(rt.export_vote(_DB(consent=False), **vote))
    assert sent == ["/v1/metrics"]
    sent.clear()
    asyncio.run(rt.export_vote(_DB(consent=True), **vote))
    assert sent == ["/v1/metrics", "/v1/traces"]
    sent.clear()
    asyncio.run(rt.export_vote(_DB(consent=True), **{**vote, "rating": 0}))
    assert sent == ["/v1/metrics"]  # an un-rate updates the gauge, sends no span


def test_a_failing_collector_never_raises(monkeypatch):
    monkeypatch.setenv("STUDIO_OTLP_ENDPOINT", "http://127.0.0.1:9")  # nothing listens
    assert asyncio.run(rt.export_counts(_DB())) is False


def test_db_counts_group_rated_replies(tmp_path):
    from db import Database

    async def go():
        db = Database(db_path=str(tmp_path / "s.db"))
        await db.connect()
        await db.upsert_message_feedback("s", "m1", 1, model="x", agent="build")
        await db.upsert_message_feedback("s", "m2", 1, model="x", agent="build")
        await db.upsert_message_feedback("s", "m3", -1, model="x", agent="build")
        await db.upsert_message_feedback("s", "m3", 1, model="x", agent="build")  # changed vote
        counts = await db.message_feedback_counts()
        await db.close()
        return counts

    assert asyncio.run(go()) == [{"rating": 1, "model": "x", "agent": "build", "n": 3}]
