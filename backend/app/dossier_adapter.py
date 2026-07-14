"""Phase 4 (box 1) — dossier → analysis-block adapter. RISK #1 mitigation.

Palo's strategy compiler + exemplar bank eat a rich per-video "analysis" text (verbatim
hooks, structure atoms, pacing, transcript). Yunicorn has dossiers (app/dossier.py:
first_frame / delivery_curve / visual_events / scenes / gaffes) + transcript + metrics —
sparser and a different schema. Porting the compiler is days; producing the input it
expects is the real project, so this ONE adapter is the seam: it renders a Marque video
(dossier + transcript + metrics) into the compiler's expected analysis block. Built and
tested BEFORE the compiler so Phase 4 has a stable, verified input contract.

Pure + keyless-testable. A thin/absent dossier degrades to whatever signal is present
(title + views + transcript) rather than failing.
"""
from __future__ import annotations


def _transcript_text(video: dict, limit: int = 400) -> str:
    t = video.get("transcript")
    if isinstance(t, str):
        return t[:limit]
    if isinstance(t, list):  # [{word}] or [{text}]
        words = [w.get("text") or w.get("word", "") for w in t if isinstance(w, dict)]
        return " ".join(words)[:limit]
    return ""


def _energy_summary(dossier: dict) -> str:
    curve = dossier.get("delivery_curve") or []
    energies = [c.get("energy") for c in curve
                if isinstance(c.get("energy"), (int, float))]
    if not energies:
        return "unknown"
    return f"opens {energies[0]:.1f}, peaks {max(energies):.1f}, ends {energies[-1]:.1f}"


def _structure_summary(dossier: dict, limit: int = 5) -> str:
    events = dossier.get("visual_events") or []
    parts = [f"{e.get('kind', '?')}: {e.get('desc', '')}".strip()
             for e in events[:limit] if e.get("kind") or e.get("desc")]
    return "; ".join(parts) or "not analyzed"


def dossier_to_analysis_block(video: dict) -> str:
    """Render one video into the compiler's per-video analysis block."""
    title = (video.get("title") or "Untitled").strip()
    views = video.get("views")
    views_s = f"{int(views):,} views" if isinstance(views, (int, float)) else "views n/a"
    dossier = video.get("dossier") or {}
    ff = dossier.get("first_frame") or {}
    hook = ff.get("desc", "") or "not analyzed"
    interrupt = " [pattern interrupt]" if ff.get("pattern_interrupt") else ""
    lines = [
        f"### {title} ({views_s})",
        f"Hook: {hook}{interrupt}",
        f"Energy curve: {_energy_summary(dossier)}",
        f"Structure: {_structure_summary(dossier)}",
    ]
    transcript = _transcript_text(video)
    if transcript:
        lines.append(f"Transcript (open): {transcript}")
    gaffes = dossier.get("gaffes") or []
    if gaffes:
        lines.append("Weak spots: " + "; ".join(g.get("desc", "") for g in gaffes[:3] if g.get("desc")))
    return "\n".join(lines)


def videos_from_clip_sessions(sessions: list[dict],
                              views_by_id: dict | None = None) -> list[dict]:
    """Map stored clip-job states (clip_edit_sessions.state) into the `{title, views,
    transcript, dossier}` video shape the compiler/exemplar builder consume. This is the
    seam that feeds the creator's REAL analyzed content into the brain (RISK #1 closed).
    Only sessions carrying real evidence (a dossier or a transcript) are kept."""
    views_by_id = views_by_id or {}
    out: list[dict] = []
    for s in sessions or []:
        if not isinstance(s, dict):
            continue
        dossier = s.get("dossier") or {}
        words = s.get("words") or []
        if not (dossier or words):                 # in-progress/empty job -> no evidence
            continue
        script = s.get("script") if isinstance(s.get("script"), dict) else {}
        title = (script.get("title") or "").strip() or "Untitled"
        transcript = " ".join(w.get("word", "") for w in words if isinstance(w, dict))[:2000]
        out.append({"title": title, "views": views_by_id.get(s.get("job_id")) or 0,
                    "transcript": transcript, "dossier": dossier})
    return out


def catalog_block(videos: list[dict], limit: int = 20) -> str:
    """The evidence pack: the creator's videos as analysis blocks, best-performing first
    (metrics-ranked so the compiler weights what worked). Empty string for no videos."""
    ranked = sorted(videos or [], key=lambda v: v.get("views") or 0, reverse=True)
    blocks = [dossier_to_analysis_block(v) for v in ranked[:limit]]
    return "\n\n".join(b for b in blocks if b)
