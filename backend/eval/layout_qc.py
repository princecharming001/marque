"""Ralph campaign grader: spatial collision + safe-zone checks (pure math).

Mirrors the render's geometry (render/src/layout.ts + layout.json via
app/layout_constants.py + BrollLayer/Watermark/ProgressBar constants) to
enumerate every on-screen rect per output-frame window, then flags:
  - layout_collision  (P0): two text/media rects genuinely overlapping
  - render_safe_breach (P0): a text rect breaching the render's OWN safe band
    (means the render-side clamp failed — a real bug)
  - safe_zone_breach  (P1): platform-chrome tier (TikTok/IG UI zones)
  - pos_y_unclamped   (P0): a caption pos_y outside the legal clamp survived

CRITICAL fidelity rule: the render NUDGES stickers out of the caption band
(resolveStickerNudge) — we SIMULATE that nudge before flagging, otherwise
every sticker near captions is a false positive. Only residual overlap after
the simulated nudge (the shrink=0.85 no-room case) or pairs the render has no
nudge for (sticker vs b-roll, caption vs b-roll inset...) are findings.

MIRROR CONTRACT: constants below are bound to render/src/layout.ts and
components — update together (same discipline as app/layout_constants.py).
"""
from __future__ import annotations

from app.layout_constants import (CAPTION_ANCHOR_Y, CAPTION_HIDE_AFTER_LAST,
                                  CAPTION_POS_Y_MAX, CAPTION_POS_Y_MIN,
                                  CREDIT_CHIP_TOP_PX, FRAME_H, FRAME_W,
                                  PHRASE_LEN, SAFE_BOTTOM_PX, SAFE_TOP_PX,
                                  SIZE_MULT, STICKER_POS_X_MAX,
                                  STICKER_POS_X_MIN, STICKER_POS_Y_MAX,
                                  STICKER_POS_Y_MIN)
from eval.campaign_common import FPS, clip_out_len, finding, total_out_frames

# --- render-component mirrors (bind: BrollLayer.tsx:33/36, Watermark.tsx:32,
#     ProgressBar (PROGRESS_BAR_HEIGHT_PX=4 in layout.ts:16), layout.ts CHAR_W) ---
_PANEL = (40 / FRAME_W, 130 / FRAME_H, (FRAME_W - 40) / FRAME_W, (130 + 0.46 * FRAME_H) / FRAME_H)
_CARD_W, _CARD_H = 0.44 * FRAME_W, 0.35 * FRAME_H * 0.72
_CARD = ((FRAME_W - 48 - _CARD_W) / FRAME_W, 220 / FRAME_H,
         (FRAME_W - 48) / FRAME_W, (220 + _CARD_H) / FRAME_H)
_WATERMARK = (40 / FRAME_W, (FRAME_H - 336 - 72) / FRAME_H,
              (40 + 260) / FRAME_W, (FRAME_H - 336) / FRAME_H)
_PROGRESS = (0.0, (FRAME_H - SAFE_BOTTOM_PX - 4) / FRAME_H,
             1.0, (FRAME_H - SAFE_BOTTOM_PX) / FRAME_H)
_CREDIT = (0.0, CREDIT_CHIP_TOP_PX / FRAME_H, 1.0, (CREDIT_CHIP_TOP_PX + 60) / FRAME_H)
_CHAR_W_DEFAULT = 0.55
_CLEAN_BASE_PX = 56          # clean/karaoke base font (Captions.tsx)
_BOLD_BASE_PX = 128          # bold-word single-word base
_BAND_INFLATE = 1.15         # conservative over-estimate per design
# Platform UI chrome (owner spec, two-tier): breach = P1.
_PLAT_TOP, _PLAT_BOTTOM, _PLAT_SIDE = 140 / 1920, 484 / 1920, 60 / 1080

# Pairs the render has NO collision handling for (anything unlisted with the
# caption band goes through the nudge simulation instead).
_COLLIDABLE = {
    ("caption", "panel"), ("caption", "card"), ("caption", "smart"),
    ("sticker", "panel"), ("sticker", "card"), ("sticker", "smart"),
    ("sticker", "sticker"), ("caption", "progress_bar"),
    ("sticker", "watermark"), ("caption", "watermark"),
}


def _overlap(a: tuple, b: tuple) -> float:
    """Intersection area as a fraction of the SMALLER rect (0 = none)."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    area = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return inter / area if area > 0 else 0.0


def _caption_band(plan: dict) -> tuple | None:
    """Estimated caption band rect (normalized) — mirror of captionBandRect."""
    caps = plan.get("captions") or []
    if not caps:
        return None
    opts = plan.get("caption_options") or {}
    style = plan.get("caption_style") or "clean"
    mult = SIZE_MULT.get(opts.get("size") or "medium", 1.0) * float(opts.get("scale") or 1.0)
    base = _BOLD_BASE_PX if style == "bold-word" else _CLEAN_BASE_PX
    lines = 1 if style in ("bold-word",) else 2
    est_h = base * mult * 1.25 * lines * _BAND_INFLATE
    pos_y = opts.get("pos_y")
    anchor = (min(CAPTION_POS_Y_MAX, max(CAPTION_POS_Y_MIN, float(pos_y)))
              if pos_y is not None else CAPTION_ANCHOR_Y.get(opts.get("position") or "bottom", 0.8333))
    half = est_h / 2 / FRAME_H
    lo_c = SAFE_TOP_PX / FRAME_H + half
    hi_c = 1 - SAFE_BOTTOM_PX / FRAME_H - half
    center = anchor if lo_c > hi_c else min(hi_c, max(lo_c, anchor))
    pad_x = (60 if style == "bold-word" else 40) / FRAME_W
    return (pad_x, center - half, 1 - pad_x, center + half)


def _sticker_rect_after_nudge(o: dict, band: tuple | None) -> tuple:
    """Mirror clampSticker + resolveStickerNudge for a text_sticker overlay."""
    x = min(STICKER_POS_X_MAX, max(STICKER_POS_X_MIN, float(o.get("pos_x") or 0.5)))
    y = min(STICKER_POS_Y_MAX, max(STICKER_POS_Y_MIN, float(o.get("pos_y") or 0.3)))
    scale = float(o.get("scale") or 1.0)
    text = str(o.get("text") or "")
    w = min(0.9, max(0.12, len(text) * _CHAR_W_DEFAULT * 64 * scale / FRAME_W))
    half_h = (64 * scale * 1.2) / 2 / FRAME_H
    if band is not None:
        bt, bb = band[1], band[3]
        if not (y + half_h <= bt or y - half_h >= bb):        # intersects band
            above, below = bt - STICKER_POS_Y_MIN, STICKER_POS_Y_MAX - bb
            if above >= half_h * 2 and above >= below:
                y = max(STICKER_POS_Y_MIN + half_h, bt - half_h - 0.01)
            elif below >= half_h * 2:
                y = min(STICKER_POS_Y_MAX - half_h, bb + half_h + 0.01)
            else:
                half_h *= 0.85
                y = min(STICKER_POS_Y_MAX - half_h, max(STICKER_POS_Y_MIN + half_h, y))
    return (x - w / 2, y - half_h, x + w / 2, y + half_h)


def timeline_rects(plan: dict) -> list[dict]:
    """Every on-screen rect with its active output-frame window."""
    rects: list[dict] = []
    total = total_out_frames(plan)
    band = _caption_band(plan)

    caps = plan.get("captions") or []
    if band is not None and caps:
        # One band rect per caption GROUP window (phrase grouping) — simplified
        # to contiguous speech spans: band active from each caption's frame to
        # end_frame (+hide tail). Merge adjacent windows.
        spans, cur = [], None
        for c in caps:
            f0 = int(c.get("frame", 0))
            f1 = int(c.get("end_frame") or f0 + 15) + CAPTION_HIDE_AFTER_LAST
            if cur and f0 <= cur[1] + PHRASE_LEN * 15:
                cur[1] = max(cur[1], f1)
            else:
                cur = [f0, f1]
                spans.append(cur)
        for f0, f1 in spans:
            rects.append({"kind": "caption", "f0": f0, "f1": min(f1, total), "r": band})

    for o in plan.get("overlays") or []:
        if o.get("type") == "text_sticker":
            rects.append({"kind": "sticker", "f0": int(o.get("frame_in", 0)),
                          "f1": int(o.get("frame_out", 0)),
                          "r": _sticker_rect_after_nudge(o, band)})
        elif o.get("type") == "stat_counter":            # v8 hook (future)
            x, y = float(o.get("pos_x") or 0.5), float(o.get("pos_y") or 0.3)
            rects.append({"kind": "sticker", "f0": int(o.get("frame_in", 0)),
                          "f1": int(o.get("frame_out", 0)),
                          "r": (x - 0.18, y - 0.05, x + 0.18, y + 0.05)})

    for b in plan.get("broll") or []:
        mode = b.get("mode") or "full"
        if mode == "full":
            continue                                     # whole frame by design
        if mode == "smart" and b.get("inset_rect"):
            ir = b["inset_rect"]
            r = (ir["x"], ir["y"], ir["x"] + ir["w"], ir["y"] + ir["h"])
        elif mode == "panel":
            r = _PANEL
        else:
            r = _CARD
        rects.append({"kind": mode if mode == "smart" else mode, "f0": int(b.get("frame_in", 0)),
                      "f1": int(b.get("frame_out", 0)), "r": r})

    ec = plan.get("end_card")
    if ec:
        rects.append({"kind": "end_card", "f0": int(ec.get("start_frame", total)),
                      "f1": int(ec.get("start_frame", total)) + int(ec.get("frames", 0)),
                      "r": (0.0, 0.0, 1.0, 1.0)})
    if plan.get("watermark"):
        rects.append({"kind": "watermark", "f0": 0, "f1": total, "r": _WATERMARK})
    if plan.get("progress_bar"):
        rects.append({"kind": "progress_bar", "f0": 0, "f1": total, "r": _PROGRESS})
    if (plan.get("react_source") or {}).get("credit_label"):
        rects.append({"kind": "credit", "f0": 0, "f1": total, "r": _CREDIT})
    return rects


def grade_layout(plan: dict, *, video: str = "", job_id: str = "") -> list[dict]:
    findings: list[dict] = []
    rects = timeline_rects(plan)

    # pos_y clamp bypass check (raw plan value).
    pos_y = (plan.get("caption_options") or {}).get("pos_y")
    if pos_y is not None and not (CAPTION_POS_Y_MIN <= float(pos_y) <= CAPTION_POS_Y_MAX):
        findings.append(finding(video, job_id, "pos_y_unclamped",
            evidence=f"caption_options.pos_y={pos_y} outside [{CAPTION_POS_Y_MIN},{CAPTION_POS_Y_MAX}]",
            source="layout_qc"))

    # Pairwise overlaps for co-active collidable pairs.
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            if a["f1"] <= b["f0"] or b["f1"] <= a["f0"]:
                continue
            pair = tuple(sorted((a["kind"], b["kind"])))
            is_end_card = "end_card" in pair
            if not is_end_card and pair not in {tuple(sorted(p)) for p in _COLLIDABLE}:
                continue
            if is_end_card and pair == ("end_card", "end_card"):
                continue
            frac = 1.0 if is_end_card else _overlap(a["r"], b["r"])
            if is_end_card:
                # anything text/media co-active with the takeover is a finding
                other = a if b["kind"] == "end_card" else b
                if other["kind"] in ("watermark", "progress_bar"):
                    continue
                t0 = max(a["f0"], b["f0"])
                findings.append(finding(video, job_id, "layout_collision", t=t0 / FPS,
                    evidence=f"{other['kind']} window [{other['f0']},{other['f1']}] overlaps the end card",
                    source="layout_qc", extra={"pair": pair}))
            elif frac > 0.02:
                t0 = max(a["f0"], b["f0"])
                findings.append(finding(video, job_id, "layout_collision", t=t0 / FPS,
                    evidence=f"{a['kind']}{a['r']} x {b['kind']}{b['r']} overlap {frac:.0%} "
                             f"during [{t0},{min(a['f1'], b['f1'])}]",
                    source="layout_qc", extra={"pair": pair, "frac": round(frac, 3)}))

    # Safe zones, two tiers (text-bearing rects only).
    for r in rects:
        if r["kind"] not in ("caption", "sticker"):
            continue
        x0, y0, x1, y1 = r["r"]
        if y0 < SAFE_TOP_PX / FRAME_H - 1e-6 or y1 > 1 - SAFE_BOTTOM_PX / FRAME_H + 1e-6:
            findings.append(finding(video, job_id, "render_safe_breach", t=r["f0"] / FPS,
                evidence=f"{r['kind']} rect {r['r']} breaches the render safe band "
                         f"[{SAFE_TOP_PX}px, -{SAFE_BOTTOM_PX}px]", source="layout_qc"))
        elif (y0 < _PLAT_TOP
              or (r["kind"] == "sticker"
                  and (y1 > 1 - _PLAT_BOTTOM or x0 < _PLAT_SIDE
                       or x1 > 1 - _PLAT_SIDE))):
            # Bottom/side tiers are sticker-only: the caption band is a MAX-
            # extent estimate of centered text anchored inside the render's own
            # SAFE_BOTTOM clamp (a product decision) with 40px side padding —
            # the default plan would otherwise mint a permanent false P1 on
            # every captioned video.
            findings.append(finding(video, job_id, "safe_zone_breach", t=r["f0"] / FPS,
                evidence=f"{r['kind']} rect {r['r']} under platform chrome "
                         f"(top {_PLAT_TOP:.3f}/bottom {_PLAT_BOTTOM:.3f}/side {_PLAT_SIDE:.3f})",
                source="layout_qc"))
    return findings
