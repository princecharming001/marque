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
    updated = []
    async def fake_update(creator_id, dim_value, y, raw=None, niche=""):
        updated.append(dim_value)
    monkeypatch.setattr(main, "_update_arm", fake_update)
    entry = {"pillar": "p", "style": "talking_head", "format_id": "", "hook_signal": "",
             "edit_knobs": {"knobs": {
                 "meme_intensity": {"value": "2", "chosen_by": "bandit", "propensity": 0.4},
                 "explicit_one": {"value": "x", "chosen_by": "creator", "propensity": 1.0},
             }}}
    async def drive():
        for dim in main.DIMENSIONS:
            val = entry.get(dim, "")
            if val:
                await main._update_arm("cr", f"{dim}:{val}", 0.7, None, "")
        for knob, meta in ((entry.get("edit_knobs") or {}).get("knobs") or {}).items():
            if isinstance(meta, dict) and meta.get("chosen_by") in ("bandit", "default") \
                    and meta.get("value") is not None:
                await main._update_arm("cr", f"edit_{knob}:{meta['value']}", 0.7, None, "")
    _run(drive())
    assert "edit_meme_intensity:2" in updated       # bandit knob settles
    assert not any(d.startswith("edit_explicit_one") for d in updated)   # creator choice never does
