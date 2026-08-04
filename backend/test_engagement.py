"""R2 — engagement feedback loop + soft-no. Keyless deterministic (no network,
no Anthropic, no Supabase): FakeStore captures every _request call."""
from __future__ import annotations

import asyncio

import pytest

from app import engagement as eng
from app import palo_flags


def _run(coro):
    return asyncio.run(coro)


def row(title="T", opened=False, saved=False, dismissed=False, kind="idea",
        suggestion_id="s1"):
    return {"suggestion_id": suggestion_id, "kind": kind, "title": title,
            "opened": opened, "saved": saved, "dismissed": dismissed,
            "shown_at": "2026-08-04T00:00:00+00:00"}


class FakeResp:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = [] if data is None else data

    def json(self):
        return self._data


class FakeStore:
    """Captures _request calls; GET serves canned rows."""

    def __init__(self, rows=None, get_status=200, write_status=201):
        self.rows = rows or []
        self.calls = []
        self.get_status = get_status
        self.write_status = write_status

    async def _request(self, method, path, *, params=None, json=None, headers=None):
        self.calls.append({"method": method, "path": path, "params": params,
                           "json": json, "headers": headers})
        if method == "GET":
            return FakeResp(self.get_status, self.rows)
        return FakeResp(self.write_status)


class BoomStore:
    async def _request(self, *a, **k):
        raise RuntimeError("db down")


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "ENGAGEMENT", True)


# --- tier math (Palo offline/judge.py policy) ----------------------------------

def test_tier_engaged_via_any_save():
    rows = [row(saved=True)] + [row() for _ in range(4)]      # 5 shown, 0 opens, 1 save
    assert eng.tier_from_rows(rows) == "engaged"


def test_tier_engaged_via_open_rate():
    rows = [row(opened=True), row(opened=True), row(), row()]  # 2/4 = 50%
    assert eng.tier_from_rows(rows) == "engaged"


def test_tier_ignoring():
    assert eng.tier_from_rows([row() for _ in range(4)]) == "ignoring"


def test_tier_skimming_middle_ground():
    rows = [row(opened=True), row(), row(), row(), row()]      # 1/5 opens — neither pole
    assert eng.tier_from_rows(rows) == "skimming"


def test_tier_cold_default_under_three_shown():
    assert eng.tier_from_rows([]) == "skimming"
    assert eng.tier_from_rows([row(saved=True), row(opened=True)]) == "skimming"


def test_tier_boundary_three_shown_no_opens_is_skimming():
    # 3 shown / 0 opens: past the cold floor but under the >=4 ignoring bar.
    assert eng.tier_from_rows([row(), row(), row()]) == "skimming"


def test_engagement_tier_reads_last_10(on):
    store = FakeStore(rows=[row(opened=True), row(opened=True), row(), row()])
    assert _run(eng.engagement_tier(store, "c1")) == "engaged"
    get = store.calls[0]
    assert get["method"] == "GET" and get["path"] == "/suggestion_outbox"
    assert get["params"]["creator_id"] == "eq.c1"
    assert get["params"]["order"] == "shown_at.desc"
    assert get["params"]["limit"] == "10"


# --- soft-no -------------------------------------------------------------------

def test_soft_no_titles_seen_not_acted():
    rows = [
        row(title="Opened, ignored", opened=True),             # soft no
        row(title="Dismissed", dismissed=True),                # soft no (saw, declined)
        row(title="Saved", opened=True, saved=True),           # acted — not a soft no
        row(title="Untouched"),                                # never saw — not testimony
    ]
    assert eng.soft_no_titles(rows) == ["Opened, ignored", "Dismissed"]


def test_soft_no_zero_opens_is_undelivered_mail():
    # Nobody has EVER opened a card => untouched suggestions are undelivered mail,
    # not declined ideas — even a dismissed row earns no soft-no read.
    rows = [row(title="Dismissed", dismissed=True), row(title="Untouched")]
    assert eng.soft_no_titles(rows) == []


def test_soft_no_one_open_unlocks_the_gate():
    rows = [row(title="Opened", opened=True),
            row(title="Dismissed", dismissed=True)]
    assert eng.soft_no_titles(rows) == ["Opened", "Dismissed"]


def test_soft_no_dedupes_and_drops_blank_titles():
    rows = [row(title="Same", opened=True), row(title="Same", opened=True),
            row(title="", opened=True)]
    assert eng.soft_no_titles(rows) == ["Same"]


# --- blocks --------------------------------------------------------------------

def test_feedback_block_renders(on):
    store = FakeStore(rows=[
        row(title="Hook study", opened=True, saved=True),
        row(title="Cold open", dismissed=True),
        row(title="Fresh one"),
    ])
    block = _run(eng.feedback_block(store, "c1"))
    assert "tier=engaged" in block and "opened 1/3" in block
    assert "ignoring earns fewer, better ones" in block        # tuning framing rides along
    assert "off the mark; tune accordingly" in block
    assert "1. [idea] Hook study [opened, saved]" in block
    assert "2. [idea] Cold open [dismissed]" in block
    assert "3. [idea] Fresh one" in block and "Fresh one [" not in block


def test_feedback_block_empty_when_nothing_shown(on):
    assert _run(eng.feedback_block(FakeStore(rows=[]), "c1")) == ""


def test_soft_no_block_renders(on):
    store = FakeStore(rows=[row(title="Seen idea", opened=True)])
    block = _run(eng.soft_no_block(store, "c1"))
    assert block.startswith("SOFT NOS — ideas the creator saw and did not act on")
    assert "ENGINE, not the title" in block and "- Seen idea" in block
    assert store.calls[0]["params"]["limit"] == "30"           # deeper soft-no window


def test_soft_no_block_empty_when_no_soft_nos(on):
    assert _run(eng.soft_no_block(FakeStore(rows=[row(title="X", dismissed=True)]), "c1")) == ""
    assert _run(eng.soft_no_block(FakeStore(rows=[]), "c1")) == ""


# --- keyless / flag / guard no-ops ---------------------------------------------

def test_store_none_noop(on):
    assert _run(eng.track(None, "c1", "s1", "idea", "T", "shown")) is False
    assert _run(eng.engagement_tier(None, "c1")) == "skimming"
    assert _run(eng.feedback_block(None, "c1")) == ""
    assert _run(eng.soft_no_block(None, "c1")) == ""


def test_flag_off_noop():
    store = FakeStore(rows=[row(opened=True)])
    assert _run(eng.track(store, "c1", "s1", "idea", "T", "shown")) is False
    assert _run(eng.engagement_tier(store, "c1")) == "skimming"
    assert _run(eng.feedback_block(store, "c1")) == ""
    assert _run(eng.soft_no_block(store, "c1")) == ""
    assert store.calls == []                                   # flag off => zero DB traffic


def test_real_creator_guard(on):
    for cid in ("default", "demo", "demo-abc123", ""):
        store = FakeStore()
        assert _run(eng.track(store, cid, "s1", "idea", "T", "shown")) is False
        assert _run(eng.feedback_block(store, cid)) == ""
        assert store.calls == []                               # 'default' never writes


def test_exceptions_degrade(on):
    assert _run(eng.track(BoomStore(), "c1", "s1", "idea", "T", "opened")) is False
    assert _run(eng.engagement_tier(BoomStore(), "c1")) == "skimming"
    assert _run(eng.feedback_block(BoomStore(), "c1")) == ""
    assert _run(eng.soft_no_block(BoomStore(), "c1")) == ""


# --- track wire format ----------------------------------------------------------

def test_track_shown_builds_idempotent_insert(on):
    store = FakeStore()
    assert _run(eng.track(store, "c1", "s1", "idea", "My title", "shown")) is True
    call = store.calls[0]
    assert call["method"] == "POST" and call["path"] == "/suggestion_outbox"
    assert call["params"] == {"on_conflict": "creator_id,suggestion_id"}
    assert "ignore-duplicates" in call["headers"]["Prefer"]    # re-show never resets flags
    body = call["json"]
    assert body["creator_id"] == "c1" and body["suggestion_id"] == "s1"
    assert body["kind"] == "idea" and body["title"] == "My title"
    assert len(body["id"]) == 26                               # ULID pk
    assert body["shown_at"] and body["opened"] is False
    assert body["saved"] is False and body["dismissed"] is False


@pytest.mark.parametrize("event", ["opened", "saved", "dismissed"])
def test_track_flag_events_patch(on, event):
    store = FakeStore()
    assert _run(eng.track(store, "c1", "s1", "idea", "T", event)) is True
    call = store.calls[0]
    assert call["method"] == "PATCH" and call["path"] == "/suggestion_outbox"
    assert call["params"] == {"creator_id": "eq.c1", "suggestion_id": "eq.s1"}
    assert call["json"] == {event: True}


def test_track_rejects_bad_event_and_missing_id(on):
    store = FakeStore()
    assert _run(eng.track(store, "c1", "s1", "idea", "T", "clicked")) is False
    assert _run(eng.track(store, "c1", "", "idea", "T", "shown")) is False
    assert store.calls == []


def test_track_write_failure_returns_false(on):
    store = FakeStore(write_status=404)                        # table not migrated yet
    assert _run(eng.track(store, "c1", "s1", "idea", "T", "shown")) is False
