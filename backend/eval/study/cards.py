"""Title-card + CTA detectors (design §6).

Title card — window 0..2.5s. Candidates: (a) an opening shot labeled graphic
(full card), or (b) OCR lines OUTSIDE the caption band whose height reads as a
headline (>8% of frame, or >=1.5x the caption band's median line height).
`present` requires heuristic AND vision agreement — precision over recall for
the headline "% of winners opening with a title card" number.

CTA — three independent channels over the last 3.5s + full transcript + the
post caption: spoken regex classes, text overlay / end card, caption text.
"""
from __future__ import annotations

import re

TITLE_WINDOW_S = 2.5
CTA_WINDOW_S = 3.5
HEADLINE_MIN_H = 0.08
HEADLINE_VS_BAND = 1.5

CTA_CLASSES = {
    "follow": r"\bfollow\b|\bfollow for\b",
    "comment_keyword": r"\bcomment\s+[\"'“]?\w+",
    "link_in_bio": r"\blink in (?:my )?bio\b",
    "dm": r"\bdm\b|\bmessage me\b",
    "save_share": r"\bsave this\b|\bshare this\b",
    "part_2": r"\bpart\s*2\b|\bpart two\b",
    "subscribe": r"\bsubscribe\b",
}


def classify_cta_text(text: str) -> str | None:
    low = (text or "").lower()
    for name, pat in CTA_CLASSES.items():
        if re.search(pat, low):
            return name
    return None


def detect_title_card(frames, shots: list[dict], band: tuple[float, float] | None,
                      vision_confirm: dict | None) -> dict:
    """frames: list[FrameRec]; vision_confirm: {'has_title_card', 'style', 'text'}
    from the Haiku confirm call (or None when vision unavailable -> heuristic-only,
    stamped 'vision_checked': False)."""
    cand_text, cand_t0, cand_t1, style = "", None, None, None
    for s in shots:
        if s["t0"] < TITLE_WINDOW_S and s["label"] == "graphic":
            style, cand_t0, cand_t1 = "full_card", s["t0"], min(s["t1"], TITLE_WINDOW_S + 1.0)
            break
    if style is None:
        band_h = None
        if band:
            heights = [(l["bbox"][3] - l["bbox"][1])
                       for f in frames for l in f.lines
                       if band[0] <= (l["bbox"][1] + l["bbox"][3]) / 2 <= band[1]]
            heights.sort()
            band_h = heights[len(heights) // 2] if heights else None
        hits = []
        for f in frames:
            if f.t > TITLE_WINDOW_S:
                break
            for l in f.lines:
                yc = (l["bbox"][1] + l["bbox"][3]) / 2
                h = l["bbox"][3] - l["bbox"][1]
                outside = band is None or not (band[0] <= yc <= band[1])
                big = h >= HEADLINE_MIN_H or (band_h and h >= HEADLINE_VS_BAND * band_h)
                if outside and big:
                    hits.append((f.t, l))
        if hits:
            style = "overlay_headline"
            cand_t0, cand_t1 = hits[0][0], hits[-1][0]
            cand_text = max((l["text"] for _, l in hits), key=len)
    heur = style is not None
    vis = None if vision_confirm is None else bool(vision_confirm.get("has_title_card"))
    present = heur and (vis is None or vis)
    out = {"present": bool(present), "vision_checked": vis is not None}
    if present:
        text = (vision_confirm or {}).get("text") or cand_text
        out.update({"style": style, "t0": round(cand_t0 or 0.0, 2),
                    "t1": round(cand_t1 or 0.0, 2),
                    "dur_s": round(max(0.0, (cand_t1 or 0) - (cand_t0 or 0)), 2),
                    "text": text[:120], "n_words": len(text.split())})
    elif heur and vis is False:
        out["note"] = "heuristic saw a card, vision refuted — excluded (precision rule)"
    return out


def detect_cta(words: list[dict], frames, band, post_caption: str,
               shots: list[dict], duration_s: float,
               vision_confirm: dict | None) -> dict:
    spoken = {"present": False}
    tail_start = duration_s * 0.75
    tail_text = " ".join(w.get("word", "") for w in words
                         if w.get("start_ms", 0) >= tail_start * 1000)
    cls = classify_cta_text(tail_text) or classify_cta_text(
        " ".join(w.get("word", "") for w in words))
    if cls:
        spoken = {"present": True, "phrase_class": cls}

    end_card = {"present": False}
    text_overlay = {"present": False}
    if shots and shots[-1]["label"] == "graphic" and \
            shots[-1]["t1"] >= duration_s - 0.5:
        end_card = {"present": True, "t0": round(shots[-1]["t0"], 2),
                    "dur_s": round(shots[-1]["t1"] - shots[-1]["t0"], 2)}
    else:
        for f in frames:
            if f.t < duration_s - CTA_WINDOW_S:
                continue
            for l in f.lines:
                yc = (l["bbox"][1] + l["bbox"][3]) / 2
                if (band is None or not (band[0] <= yc <= band[1])) and \
                        classify_cta_text(l["text"]):
                    text_overlay = {"present": True, "text": l["text"][:120]}
                    break
    if vision_confirm is not None:
        if end_card["present"] and not vision_confirm.get("has_end_card"):
            end_card = {"present": False,
                        "note": "heuristic refuted by vision (precision rule)"}
        if vision_confirm.get("end_card_text") and end_card.get("present"):
            end_card["text"] = str(vision_confirm["end_card_text"])[:120]

    return {"spoken": spoken, "text_overlay": text_overlay, "end_card": end_card,
            "post_caption_cta": {"present": bool(classify_cta_text(post_caption or "")),
                                  "phrase_class": classify_cta_text(post_caption or "")}}


def cta_pattern(cta: dict) -> str:
    """The combination label the aggregate ranks."""
    vis_card = cta["end_card"]["present"]
    vis_overlay = cta["text_overlay"]["present"]
    spoken = cta["spoken"]["present"]
    if vis_card:
        return "end_card+spoken" if spoken else "end_card_only"
    if vis_overlay:
        return "overlay+spoken" if spoken else "overlay_only"
    return "spoken_only" if spoken else "none"
