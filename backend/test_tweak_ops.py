"""v6: set_broll_rect direct-manipulation op."""
from app.edl import apply_edl_ops


def test_set_broll_rect_places_and_forces_smart():
    edl = {"style": "broll_cutaway",
           "segments": [{"src_in": 0, "src_out": 900}],
           "broll": [{"src_in": 100, "src_out": 160, "cue_text": "x", "mode": "full"}]}
    out, res = apply_edl_ops(edl, [{"type": "set_broll_rect", "index": 0,
                                    "x": 0.05, "y": 0.08, "w": 0.5, "h": 0.2}])
    assert res[0]["applied"], res
    b = out["broll"][0]
    assert b["mode"] == "smart"
    assert b["inset_rect"] == {"x": 0.05, "y": 0.08, "w": 0.5, "h": 0.2}


def test_set_broll_rect_rejects_bad_index_and_clamps():
    edl = {"style": "broll_cutaway", "segments": [{"src_in": 0, "src_out": 900}],
           "broll": [{"src_in": 100, "src_out": 160, "cue_text": "x", "mode": "panel"}]}
    _, res = apply_edl_ops(edl, [{"type": "set_broll_rect", "index": 5,
                                  "x": 0.1, "y": 0.1, "w": 0.4, "h": 0.2}])
    assert not res[0]["applied"]
    out, res2 = apply_edl_ops(edl, [{"type": "set_broll_rect", "index": 0,
                                     "x": 0.8, "y": 0.85, "w": 0.9, "h": 0.9}])
    assert res2[0]["applied"]
    r = out["broll"][0]["inset_rect"]
    assert r["x"] + r["w"] <= 1.0 and r["y"] + r["h"] <= 1.0, r
