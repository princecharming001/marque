"""WS6 (build 49) — editing-knob bandit: selection, propensity logging, settle wiring."""
import asyncio

import main


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_select_edit_knobs_logs_propensities_flag_off(monkeypatch):
    monkeypatch.setattr(main, "EDIT_BANDIT", False)
    out = main._select_edit_knobs("cr-1", {}, "")
    assert out["bandit_active"] is False
    k = out["knobs"]["meme_intensity"]
    assert k["chosen_by"] == "default" and k["value"] == "1"
    # propensities always logged (offline replay needs them from day one), sum ≈ 1
    assert abs(sum(k["propensities"].values()) - 1.0) < 0.05


def test_select_edit_knobs_respects_explicit_creator_choice(monkeypatch):
    monkeypatch.setattr(main, "EDIT_BANDIT", True)
    out = main._select_edit_knobs("cr-1", {"meme_intensity": 3}, "")
    k = out["knobs"]["meme_intensity"]
    assert k["chosen_by"] == "creator" and k["value"] == "3" and k["propensity"] == 1.0


def test_select_edit_knobs_bandit_draw_uses_posteriors(monkeypatch):
    monkeypatch.setattr(main, "EDIT_BANDIT", True)
    # Heavily favor arm "2": alpha huge, beta tiny → the draw must pick it.
    main._arm_stats["cr-bandit"] = {
        "edit_meme_intensity:2": {"alpha": 500.0, "beta": 1.0},
    }
    try:
        out = main._select_edit_knobs("cr-bandit", {}, "")
        k = out["knobs"]["meme_intensity"]
        assert k["chosen_by"] == "bandit"
        assert k["value"] == "2"
        assert k["propensity"] > 0.5           # MC estimate should agree
    finally:
        main._arm_stats.pop("cr-bandit", None)


def test_settle_updates_edit_knob_arms(monkeypatch):
    # Build 53 audit: drive the REAL production settle helper (was re-implemented inline here,
    # so it could never catch a regression in main._settle_edit_knob_arms).
    updated = []
    async def fake_update(creator_id, dim_value, y, raw=None, niche=""):
        updated.append((creator_id, dim_value, y, raw, niche))
    monkeypatch.setattr(main, "_update_arm", fake_update)
    entry = {"pillar": "p", "style": "talking_head", "format_id": "", "hook_signal": "",
             "edit_knobs": {"knobs": {
                 "meme_intensity": {"value": "2", "chosen_by": "bandit", "propensity": 0.4},
                 "cold_open": {"value": "1", "chosen_by": "default", "propensity": 0.5},
                 "explicit_one": {"value": "x", "chosen_by": "creator", "propensity": 1.0},
                 "missing_value": {"chosen_by": "bandit"},   # no value → must be skipped
             }}}
    returned = _run(main._settle_edit_knob_arms("cr", entry, 0.7, 1200.0, "founder"))
    arms = [dv for (_, dv, *_rest) in updated]
    assert "edit_meme_intensity:2" in arms and "edit_meme_intensity:2" in returned   # bandit settles
    assert "edit_cold_open:1" in arms and "edit_cold_open:1" in returned             # default settles
    assert not any(dv.startswith("edit_explicit_one") for dv in arms)   # creator choice never does
    assert not any(dv.startswith("edit_missing_value") for dv in arms)  # value=None skipped
    # reward/raw/niche are threaded through unchanged to _update_arm
    assert all(y == 0.7 and raw == 1200.0 and niche == "founder"
               for (_, _, y, raw, niche) in updated)


def test_ingest_metrics_calls_real_settle(monkeypatch):
    # Guard the wiring: ingest_metrics must delegate to _settle_edit_knob_arms (not a
    # divergent inline copy), so the tested path IS the production path.
    import inspect
    src = inspect.getsource(main.ingest_metrics)
    assert "_settle_edit_knob_arms(" in src
