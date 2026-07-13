"""LOOP F — golden render-plan corpus generator (format_eval's fixture source).

Builds every corpus fixture as a raw EDL dict, runs it through the REAL
`build_render_plan` (so every fixture is contract-valid by construction — the
same G1 key-set the backend/render side share), and writes each as
`render/fixtures/golden/<id>.json` with the shape CompositionProps expects:
`{sourceUrl, formatId, edl: <plan>}`.

`sourceUrl` is the placeholder literal "__SOURCE__" — format_eval.py's render
driver substitutes the actual local http-server URL at render time (corpus
generation itself is deterministic/keyless and needs no live server).

Corpus (15 fixtures): one per composition style (7, exercising each style's
OWN layout/quirks) + 8 adversarial cases targeting specific formatting risks
found during the hardening pass (overflow, collisions, safe-area, font
fallback, contrast/placeholder copy, pitch preservation).
"""
from __future__ import annotations
import json
from pathlib import Path

from app.edl import build_render_plan

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_DIR = REPO_ROOT / "render" / "fixtures" / "golden"
PLACEHOLDER_SOURCE = "__SOURCE__"


def _captions(words: list[str], start_frame: int = 5, gap: int = 15) -> list[dict]:
    out, f = [], start_frame
    for w in words:
        out.append({"word": w, "frame": f, "end_frame": f + gap - 2})
        f += gap
    return out


def _base_edl(style: str, total_frames: int, **over) -> dict:
    edl = {
        "style": style, "format_id": "myth-buster",
        "segments": [{"src_in": 0, "src_out": total_frames, "speed": 1.0,
                      "tx_scale": 1.0, "tx_x": 0.0, "tx_y": 0.0}],
        "drops": [], "captions": _captions(["hello", "world", "this", "is", "a", "test"]),
        "overlays": [], "broll": [], "layout": {"style": style},
        "caption_style": "clean",
        "caption_options": {"position": "bottom", "size": "medium", "pos_y": None, "scale": None,
                            "accent": None, "uppercase": False, "font": "inter",
                            "grouping": "phrase", "highlight_words": []},
        "transitions": [], "look": {"filter": None, "intensity": 1.0,
                                    "adjust": {"brightness": 0, "contrast": 0, "saturation": 0,
                                              "temperature": 0, "vignette": 0}},
        "audio": {"lufs_target": -14.0, "gain": 0.0, "music": None, "volume_ranges": [], "speech_frames": []},
    }
    edl.update(over)
    return edl


def _fixture(fid: str, edl: dict) -> dict:
    plan = build_render_plan(edl)
    return {"id": fid, "sourceUrl": PLACEHOLDER_SOURCE, "formatId": edl.get("format_id", "myth-buster"), "edl": plan}


def build_corpus() -> dict[str, dict]:
    fixtures: dict[str, dict] = {}

    # --- one per composition style ---
    fixtures["talking_head-default"] = _fixture(
        "talking_head-default", _base_edl("talking_head", 150))

    fixtures["faceless-default"] = _fixture(
        "faceless-default", _base_edl("faceless", 150))

    fixtures["split_three-default"] = _fixture(
        "split_three-default",
        _base_edl("split_three", 150, layout={"style": "split_three", "panels": 3, "panel_boundaries": []}))

    fixtures["fast_cuts-default"] = _fixture(
        "fast_cuts-default", _base_edl("fast_cuts", 150))

    fixtures["green_screen-default"] = _fixture(
        "green_screen-default",
        _base_edl("green_screen", 150,
                 overlays=[{"type": "text_card", "src_in": 10, "src_out": 100,
                           "text": "Original post being reacted to", "scale": 1.0,
                           "pos_x": 0.5, "pos_y": 0.5, "rotation": 0.0, "color": None,
                           "bg": "none", "font": "inter"}]))

    fixtures["broll_cutaway-default"] = _fixture(
        "broll_cutaway-default",
        _base_edl("broll_cutaway", 150,
                 broll=[{"src_in": 20, "src_out": 60, "cue_text": "cutaway", "source": "stock",
                        "resolved_url": PLACEHOLDER_SOURCE}]))

    fixtures["duet_split-default"] = _fixture(
        "duet_split-default",
        _base_edl("duet_split", 150,
                 layout={"style": "duet_split", "panels": 2, "panel_boundaries": [], "split_fraction": 0.58},
                 react_source={"resolved_url": PLACEHOLDER_SOURCE, "kind": "video", "credit_label": "@source"},
                 react_schedule=[{"state": "play", "src_in": 0, "src_out": 150, "clip_from": 0, "audio_gain": 1.0}]))

    # --- adversarial: caption overflow (40-char single word, bold-word style) ---
    fixtures["adv-long-word-boldword"] = _fixture(
        "adv-long-word-boldword",
        _base_edl("talking_head", 90,
                 captions=[{"word": "supercalifragilisticexpialidocious", "frame": 5, "end_frame": 80}],
                 caption_style="bold-word",
                 caption_options={"position": "bottom", "size": "large", "pos_y": None, "scale": None,
                                  "accent": None, "uppercase": True, "font": "inter",
                                  "grouping": "word", "highlight_words": []}))

    # --- adversarial: a long take's worth of phrase-grouped captions ---
    fixtures["adv-long-take-phrase"] = _fixture(
        "adv-long-take-phrase",
        _base_edl("talking_head", 3000,
                 captions=_captions([f"word{i}" for i in range(200)], start_frame=5, gap=14)))

    # --- adversarial: 3 stacked text_stickers near the same position ---
    fixtures["adv-stacked-stickers"] = _fixture(
        "adv-stacked-stickers",
        _base_edl("talking_head", 150,
                 overlays=[
                     {"type": "text_sticker", "src_in": 10, "src_out": 140, "text": "First",
                      "scale": 1.0, "pos_x": 0.5, "pos_y": 0.45, "rotation": 0.0,
                      "color": None, "bg": "box", "font": "inter"},
                     {"type": "text_sticker", "src_in": 10, "src_out": 140, "text": "Second",
                      "scale": 1.0, "pos_x": 0.5, "pos_y": 0.5, "rotation": 0.0,
                      "color": None, "bg": "box", "font": "inter"},
                     {"type": "text_sticker", "src_in": 10, "src_out": 140, "text": "Third",
                      "scale": 1.0, "pos_x": 0.5, "pos_y": 0.55, "rotation": 0.0,
                      "color": None, "bg": "box", "font": "inter"},
                 ]))

    # --- adversarial: sticker requested at an extreme pos_y (must clamp) ---
    fixtures["adv-sticker-extreme-pos"] = _fixture(
        "adv-sticker-extreme-pos",
        _base_edl("talking_head", 90,
                 overlays=[{"type": "text_sticker", "src_in": 10, "src_out": 80, "text": "Bottom sticker",
                           "scale": 1.0, "pos_x": 0.5, "pos_y": 0.98, "rotation": 0.0,
                           "color": None, "bg": "box", "font": "inter"}]))

    # --- adversarial: a 300-char GreenScreen reference card ---
    fixtures["adv-greenscreen-long-card"] = _fixture(
        "adv-greenscreen-long-card",
        _base_edl("green_screen", 150,
                 overlays=[{"type": "text_card", "src_in": 10, "src_out": 140,
                           "text": "word " * 60, "scale": 1.0, "pos_x": 0.5, "pos_y": 0.5,
                           "rotation": 0.0, "color": None, "bg": "none", "font": "inter"}]))

    # --- adversarial: a 200-char DuetSplit pull-quote + credit chip ---
    fixtures["adv-duetsplit-long-quote"] = _fixture(
        "adv-duetsplit-long-quote",
        _base_edl("duet_split", 150,
                 layout={"style": "duet_split", "panels": 2, "panel_boundaries": [], "split_fraction": 0.58},
                 overlays=[{"type": "text_card", "src_in": 10, "src_out": 140,
                           "text": "quote " * 35, "scale": 1.0, "pos_x": 0.5, "pos_y": 0.5,
                           "rotation": 0.0, "color": None, "bg": "none", "font": "inter"}],
                 react_source={"resolved_url": PLACEHOLDER_SOURCE, "kind": "video", "credit_label": "@a_fairly_long_handle_name"},
                 react_schedule=[{"state": "play", "src_in": 0, "src_out": 150, "clip_from": 0, "audio_gain": 1.0}]))

    # --- adversarial: faceless + mono grade + broll (formatting fix #7 regression guard) ---
    fixtures["adv-faceless-mono-broll"] = _fixture(
        "adv-faceless-mono-broll",
        _base_edl("faceless", 90,
                 broll=[{"src_in": 0, "src_out": 90, "cue_text": "b-roll", "source": "stock",
                        "resolved_url": PLACEHOLDER_SOURCE}],
                 look={"filter": "mono", "intensity": 1.0,
                      "adjust": {"brightness": 0, "contrast": 0, "saturation": 0, "temperature": 0, "vignette": 0}}))

    # --- adversarial: speed 2.0x clip (pitch-preservation regression guard, #8) ---
    fixtures["adv-speed-2x-pitch"] = _fixture(
        "adv-speed-2x-pitch",
        _base_edl("talking_head", 45,
                 segments=[{"src_in": 0, "src_out": 90, "speed": 2.0,
                           "tx_scale": 1.0, "tx_x": 0.0, "tx_y": 0.0}],
                 captions=[]))

    return fixtures


def write_corpus() -> list[str]:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    fixtures = build_corpus()
    for fid, fx in fixtures.items():
        (GOLDEN_DIR / f"{fid}.json").write_text(json.dumps(fx, indent=2))
    return sorted(fixtures.keys())


if __name__ == "__main__":
    written = write_corpus()
    print(f"[make_format_corpus] wrote {len(written)} fixtures to {GOLDEN_DIR}")
    for fid in written:
        print(f"  {fid}")
