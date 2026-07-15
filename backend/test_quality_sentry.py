"""T3 (superintelligence epic) — daily prod-sampling quality cron, keyless.
Mirrors test_palo_cron.py's FakeStore/latch conventions."""
from __future__ import annotations

import asyncio

from app import quality_sentry

DAY = 86400.0


def _run(coro):
    return asyncio.run(coro)


class FakeQualityStore:
    def __init__(self, creators=None, profiles=None, posts=None):
        self._creators = creators or []
        self._profiles = profiles or {}
        self._posts = posts or {}
        self.rows: list[dict] = []

    async def load_all_creators(self):
        return self._creators

    async def load_creator_profile(self, creator_id):
        return self._profiles.get(creator_id)

    async def load_creator_posts(self, creator_id):
        return self._posts.get(creator_id)

    async def insert_quality_scorecard(self, row):
        self.rows.append(row)
        return True


_GOOD_SCRIPT = {"hook": "Cost per wear beats cost per cart every single time.",
               "hookSignal": "specificity", "formatId": "myth-buster",
               "body": "A twelve dollar thrift blazer worn forty times costs thirty cents a wear.",
               "cta": "Follow.", "predictedScore": 78, "style": "talking_head", "altHooks": []}
_BAD_SCRIPT = {"hook": "Here I'd break down why fast fashion is a trap.",
              "hookSignal": "curiosity", "formatId": "myth-buster",
              "body": "Step 1 — the myth. Step 2 — the receipt. Step 3 — the takeaway.",
              "cta": "Follow.", "predictedScore": 60, "style": "talking_head", "altHooks": []}


# --- pure math -------------------------------------------------------------------

def test_rotated_roster_skips_creators_with_no_niche():
    creators = [{"creator_id": "a", "niche": "fitness"}, {"creator_id": "b", "niche": ""},
               {"creator_id": "c"}]
    roster = quality_sentry._rotated_roster(creators, now_epoch=0.0, max_creators=5)
    assert [c["creator_id"] for c in roster] == ["a"]


def test_rotated_roster_rotates_by_day():
    creators = [{"creator_id": f"c{i}", "niche": "fitness"} for i in range(5)]
    day0 = quality_sentry._rotated_roster(creators, now_epoch=0.0, max_creators=2)
    day1 = quality_sentry._rotated_roster(creators, now_epoch=DAY, max_creators=2)
    assert [c["creator_id"] for c in day0] != [c["creator_id"] for c in day1]


def test_rotated_roster_caps_at_max_creators():
    creators = [{"creator_id": f"c{i}", "niche": "fitness"} for i in range(20)]
    roster = quality_sentry._rotated_roster(creators, now_epoch=0.0, max_creators=3)
    assert len(roster) == 3


def test_rotated_roster_empty_when_no_real_creators():
    assert quality_sentry._rotated_roster([], now_epoch=0.0, max_creators=5) == []


def test_breached_polarity():
    healthy = {"gate_pass_rate": 1.0, "speakability_violations": 0, "relevance_mean": 80.0}
    assert quality_sentry.breached(healthy) == []
    assert quality_sentry.breached({**healthy, "speakability_violations": 1})
    assert quality_sentry.breached({**healthy, "gate_pass_rate": 0.5})
    assert quality_sentry.breached({**healthy, "relevance_mean": 10.0})


# --- run_quality_cron --------------------------------------------------------------

def test_run_quality_cron_writes_one_row_per_creator_fast_path():
    store = FakeQualityStore(creators=[{"creator_id": "a", "niche": "fitness"},
                                       {"creator_id": "b", "niche": "finance"}])

    async def fake_fast(creator_id, brand, posts):
        return [_GOOD_SCRIPT]

    n = _run(quality_sentry.run_quality_cron(store, 0.0, fake_fast, generate_full=None))
    assert n == 2
    assert {r["creator_id"] for r in store.rows} == {"a", "b"}
    assert all(r["path"] == "feed_fast" for r in store.rows)
    assert all(r["breach"] is False for r in store.rows)


def test_run_quality_cron_flags_breach_row():
    store = FakeQualityStore(creators=[{"creator_id": "a", "niche": "fitness"}])

    async def fake_fast(creator_id, brand, posts):
        return [_BAD_SCRIPT]   # stage-direction body -> speakability violation

    _run(quality_sentry.run_quality_cron(store, 0.0, fake_fast))
    assert store.rows[0]["breach"] is True
    assert store.rows[0]["speakability_violations"] > 0


def test_run_quality_cron_one_creator_gets_full_pipeline():
    store = FakeQualityStore(creators=[{"creator_id": f"c{i}", "niche": "fitness"} for i in range(3)])
    full_calls = []

    async def fake_fast(creator_id, brand, posts):
        return [_GOOD_SCRIPT]

    async def fake_full(creator_id, brand, posts):
        full_calls.append(creator_id)
        return [_GOOD_SCRIPT]

    n = _run(quality_sentry.run_quality_cron(store, 0.0, fake_fast, generate_full=fake_full))
    assert n == 4   # 3 fast + 1 full (QUALITY_CRON_FULL_PIPELINE_N default 1)
    assert len(full_calls) == 1


def test_run_quality_cron_no_creators_is_a_noop():
    store = FakeQualityStore(creators=[])

    async def fake_fast(creator_id, brand, posts):
        return [_GOOD_SCRIPT]

    assert _run(quality_sentry.run_quality_cron(store, 0.0, fake_fast)) == 0
    assert store.rows == []


def test_run_quality_cron_survives_one_creator_raising():
    store = FakeQualityStore(creators=[{"creator_id": "a", "niche": "fitness"},
                                       {"creator_id": "b", "niche": "finance"}])

    async def flaky_fast(creator_id, brand, posts):
        if creator_id == "a":
            raise RuntimeError("boom")
        return [_GOOD_SCRIPT]

    n = _run(quality_sentry.run_quality_cron(store, 0.0, flaky_fast))
    assert n == 1   # "a" failed, "b" still wrote its row
    assert store.rows[0]["creator_id"] == "b"


def test_run_quality_cron_none_store_noop():
    async def fake_fast(creator_id, brand, posts):
        return [_GOOD_SCRIPT]
    assert _run(quality_sentry.run_quality_cron(None, 0.0, fake_fast)) == 0


def test_run_quality_cron_uses_stored_brand_when_present():
    store = FakeQualityStore(
        creators=[{"creator_id": "a", "niche": "fallback-niche"}],
        profiles={"a": {"brand": {"niche": "real stored niche", "catchphrases": ["x"]}}})
    seen = {}

    async def fake_fast(creator_id, brand, posts):
        seen["brand"] = brand
        return [_GOOD_SCRIPT]

    _run(quality_sentry.run_quality_cron(store, 0.0, fake_fast))
    assert seen["brand"]["niche"] == "real stored niche"


def test_alert_logs_without_push_configured(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.ERROR)
    monkeypatch.delenv("OWNER_CREATOR_ID", raising=False)
    _run(quality_sentry.alert([{"creator_id": "a", "path": "feed_fast",
                               "gate_pass_rate": 0.5, "speakability_violations": 1,
                               "relevance_mean": 40.0}]))
    assert any("quality-alert" in r.message for r in caplog.records)
