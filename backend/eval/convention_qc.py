"""Convention graders (Wave 3): assert a job's output honors the measured
winner conventions that are DETERMINISTIC from its inputs. Runs on the job JSON
of an edl_only authoring run (edl + words + config echo).

P0 = a convention the code must enforce mechanically was violated.
P1 = drift that needs judgment / may be legitimate for the specific take.
"""
from __future__ import annotations

import json

from app.conventions import (CAPTION_CONVENTIONS, TITLE_CARD_POLICY,
                             seed_fraction)
from eval.campaign_common import finding

FPS = 30
GLIMPSE_NEEDS = ("entity", "data", "meme")
SUBSTANTIVE_NEEDS = ("evidence", "action", "concept")


def grade_conventions(job: dict, *, video: str, config: dict | None = None) -> list[dict]:
    """`config` is what the CLIENT SENT. The clip payload does not echo config, so
    reading job["config"] left every config-dependent check silently vacuous — the CTA
    and profile graders below could never fire. Callers must pass the submitted config.
    """
    out: list[dict] = []
    edl = job.get("edl") or {}
    jid = job.get("job_id", "")
    cfg = config if config is not None else (job.get("config") or {})
    opts = edl.get("caption_options") or {}
    style = edl.get("caption_style") or ""
    # "Explicit" = the creator chose this look, whether by tapping it for this video or
    # by teaching it to their style profile. The auto-path conventions (sentence case,
    # phrase chunking, no bold-word) are a DEFAULT for creators who expressed nothing —
    # applying them to a learned taste would grade the feature as a defect.
    explicit = cfg.get("caption_style") in ("clean", "bold-word", "karaoke")
    if not explicit and cfg.get("style_profile"):
        from app import style_profile as _sp
        try:
            _m = _sp.map_profile_to_config(_sp.normalize(
                json.loads(cfg["style_profile"]) if isinstance(cfg["style_profile"], str)
                else cfg["style_profile"]))
            explicit = _m.get("caption_style") in ("clean", "bold-word", "karaoke")
        except Exception:
            pass

    # 1. auto style allow-list (study: caps/single-word = explicit pick only)
    if not explicit and style and style not in CAPTION_CONVENTIONS["auto_style_allowed"]:
        out.append(finding(video, jid, "caption_auto_style",
                           evidence=f"auto path produced style={style}",
                           source="convention_qc"))
    # 2. sentence case on the auto clean path
    if not explicit and style == "clean" and opts.get("uppercase"):
        out.append(finding(video, jid, "caption_case",
                           evidence="auto clean captions are uppercase", source="convention_qc"))
    # 3. chunking: auto path never single-word grouping
    if not explicit and opts.get("grouping") == "word":
        out.append(finding(video, jid, "caption_chunking",
                           evidence="auto path grouping=word (winners: 3-word phrases)",
                           source="convention_qc"))
    # 4. stroke default (winners 95% stroked)
    if not explicit and style == "clean" and not opts.get("stroke_px"):
        out.append(finding(video, jid, "caption_stroke_default",
                           evidence="clean auto captions carry no stroke", source="convention_qc"))
    # 5. position band validated by the study
    pos_y = opts.get("pos_y")
    if pos_y is not None and not (0.5 <= float(pos_y) <= 0.76):
        out.append(finding(video, jid, "caption_position",
                           evidence=f"pos_y {pos_y} outside winners' IQR 0.50-0.76",
                           source="convention_qc"))

    # 6. title-card gate: deterministic recompute. PRESENCE against a closed
    # gate is a P0; absence with an open gate is legitimate (other skips).
    # The hook TITLE is place_hook_overlay's sticker: scale 1.05, centered,
    # multi-word. Single-word keyword pops from the interrupts/emphasis passes
    # are a different feature (round-1 grader false positive).
    hook_stickers = [o for o in (edl.get("overlays") or [])
                     if o.get("type") == "text_sticker" and o.get("src_in", 1) <= 90
                     and float(o.get("scale") or 1.0) == 1.05
                     and len(str(o.get("text") or "").split()) >= 2]
    rates = TITLE_CARD_POLICY["rate"]
    rate = float(rates.get("default", 0.2))   # content_type unknown here: default row
    gate_open = seed_fraction(jid, "title_card") < max(
        rate, max(rates.values()))            # tolerant: open if ANY row could fire
    if hook_stickers and not gate_open:
        out.append(finding(video, jid, "title_card_gate",
                           evidence="title sticker present but the seeded gate is closed for every content type",
                           source="convention_qc"))
    for o in hook_stickers:
        span = int(o.get("src_out", 0)) - int(o.get("src_in", 0))
        if span > 110:   # 72f output target + drop/remap headroom
            out.append(finding(video, jid, "title_card_hold",
                               evidence=f"title sticker spans {span} src frames (target ≤72 out)",
                               source="convention_qc", severity="P1"))

    # 7. b-roll norms (winners n=37): holds per need + face-hiding share
    broll = edl.get("broll") or []
    segs = edl.get("segments") or []
    total = sum(max(0, s.get("src_out", 0) - s.get("src_in", 0)) for s in segs)
    full_frames = 0
    for b in broll:
        need = b.get("need", "action")
        span = int(b.get("src_out", 0)) - int(b.get("src_in", 0))
        if b.get("mode") == "full":
            full_frames += span
        if need in SUBSTANTIVE_NEEDS and b.get("mode") == "full" and span and \
                not (45 <= span <= 165):
            out.append(finding(video, jid, "broll_hold_band",
                               evidence=f"{need} full hold {span}f outside the 2-5s band",
                               source="convention_qc", severity="P1"))
        if need in GLIMPSE_NEEDS and span > 75:
            out.append(finding(video, jid, "broll_glimpse_linger",
                               evidence=f"{need} glimpse holds {span}f (>2.5s)",
                               source="convention_qc", severity="P1"))
    if total:
        share = full_frames / total
        if share > 0.18:
            out.append(finding(video, jid, "broll_share",
                               evidence=f"face-hiding b-roll covers {share:.0%} of the take "
                                        "(budget 15%, winners median 5%)",
                               source="convention_qc"))
        per30 = len([b for b in broll if b.get("mode") == "full"]) * (30 * FPS) / total
        if per30 > 2.4:
            out.append(finding(video, jid, "broll_density",
                               evidence=f"{per30:.1f} full cutaways/30s (winners IQR ceiling 1.2)",
                               source="convention_qc"))

    # 8. CTA template contract (v8). An explicit pick must be honored exactly —
    # including "none", which must leave NO visual CTA at all.
    from app import cta_styles
    want_cta = str(cfg.get("cta_style_id") or "").strip()
    ec = edl.get("end_card") or {}
    if want_cta == cta_styles.NONE_STYLE and ec:
        out.append(finding(video, jid, "cta_style_honored",
                           evidence="creator chose No CTA but an end_card was stamped",
                           source="convention_qc"))
    elif want_cta and cta_styles.is_known(want_cta):
        if not ec:
            out.append(finding(video, jid, "cta_style_honored",
                               evidence=f"creator chose {want_cta} but no CTA was placed",
                               source="convention_qc"))
        elif ec.get("style_id") != want_cta:
            out.append(finding(video, jid, "cta_style_honored",
                               evidence=f"creator chose {want_cta}, got {ec.get('style_id')}",
                               source="convention_qc"))
    if ec and not cta_styles.is_known(ec.get("style_id")):
        out.append(finding(video, jid, "cta_style_unknown",
                           evidence=f"end_card carries an unrenderable style {ec.get('style_id')!r}",
                           source="convention_qc"))

    # 9. profile-mapped knobs must actually reach the RENDERED OUTPUT. Graded on what
    # the job produced, not on a config echo — the earlier version compared the mapped
    # values against the config the client sent, which of course never contains them
    # (the server fills them), so it could only ever pass vacuously or fire falsely.
    prof = cfg.get("style_profile")
    if prof:
        from app import style_profile as sp
        try:
            mapped = sp.map_profile_to_config(sp.normalize(
                json.loads(prof) if isinstance(prof, str) else prof))
        except Exception:
            mapped = {}
        # theme: the job records the resolved theme top-level AND on the EDL.
        want_theme = mapped.get("theme_id", "")
        got_theme = job.get("theme_id") or edl.get("theme_id") or ""
        if want_theme and not cfg.get("theme_id") and got_theme != want_theme:
            out.append(finding(video, jid, "profile_knob_dropped",
                               evidence=f"profile maps theme {want_theme}, job rendered {got_theme!r}",
                               source="convention_qc"))
        # captions: the one mapped knob with a directly observable EDL field.
        want_cap = mapped.get("caption_style", "")
        if want_cap and not cfg.get("caption_style") and style and style != want_cap:
            out.append(finding(video, jid, "profile_knob_dropped",
                               evidence=f"profile maps caption_style {want_cap}, EDL has {style!r}",
                               source="convention_qc"))

    # 10. zero auto text cards (Wave 1 directive)
    autos = [o for o in (edl.get("overlays") or []) if o.get("type") == "text_card"]
    if autos and not any(e.get("action") == "text_card"
                         for e in (job.get("broll_log") or [])):
        pass  # plan-authored cards (green_screen/duet) are allowed
    for e in (job.get("broll_log") or []):
        if e.get("action") == "text_card":
            out.append(finding(video, jid, "auto_text_card",
                               evidence=f"resolve chain emitted a text card: {e}",
                               source="convention_qc"))
    return out
