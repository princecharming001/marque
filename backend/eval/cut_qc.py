"""Cut-quality graders: pure text/frame math over (EDL, words) — no render needed.

The owner's two complaint classes, made mechanical:
  - "bits and pieces cut off that shouldn't be"  -> overcut_content / overcut_partial
  - "extra stuff kept in that shouldn't be"      -> undercut_stumble / undercut_dupe
plus seam grammar (orphan fragments) and cross-run consistency (graded by the runner).

Everything reasons in SENTENCES built from word timings, then maps each word to
KEPT or CUT using the EDL's segments+drops (same source-coord bookkeeping as the
pipeline). Deterministic, keyless, unit-testable.
"""
from __future__ import annotations

import re

from app.edl import ms_to_frame
from eval.campaign_common import finding

FPS = 30
_FILLERS = {"um", "uh", "uhm", "erm", "hmm", "like", "so", "yeah", "okay", "ok"}
_TERMINAL = (".", "!", "?")


def _norm(w: str) -> str:
    return w.strip(".,!?;:—–-\"'()").lower()


def _kept_predicate(edl: dict):
    segs = [(s.get("src_in", 0), s.get("src_out", 0)) for s in edl.get("segments") or []]
    drops = [(d.get("src_in", 0), d.get("src_out", 0)) for d in edl.get("drops") or []]

    def kept(f: int) -> bool:
        return any(a <= f < b for a, b in segs) and not any(a <= f < b for a, b in drops)
    return kept


def sentences_with_keep(edl: dict, words: list[dict]) -> list[dict]:
    """[{idx, text, words:[(word, frame, kept)], kept_ratio, start_f, end_f}] —
    transcript split at terminal punctuation, each word tagged KEPT/CUT."""
    kept = _kept_predicate(edl)
    sents, cur = [], []
    for w in words or []:
        t = str(w.get("word", ""))
        if not t:
            continue
        f = ms_to_frame(w.get("start_ms", 0))
        cur.append((t, f, kept(f)))
        # Terminal punctuation OR a trailing em-dash: AssemblyAI marks aborted
        # stumbles as "word—" tokens ("Most fusion— most fusion— ..."), and the
        # stumble detector needs those fragments as their own units.
        if t.rstrip("\"'").endswith(_TERMINAL) or t.endswith(("—", "–")):
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    out = []
    for i, sw in enumerate(sents):
        k = sum(1 for _, _, kp in sw if kp)
        out.append({"idx": i, "words": sw, "text": " ".join(t for t, _, _ in sw),
                    "kept_ratio": k / max(1, len(sw)),
                    "start_f": sw[0][1], "end_f": sw[-1][1]})
    return out


def _content_tokens(text: str) -> set:
    return {_norm(w) for w in text.split() if len(_norm(w)) > 2 and _norm(w) not in _FILLERS}


def _overlap(a: set, b: set) -> float:
    return len(a & b) / max(1, min(len(a), len(b)))


def grade_cuts(edl: dict, words: list[dict], *, video: str = "", job_id: str = "") -> list[dict]:
    findings: list[dict] = []
    sents = sentences_with_keep(edl, words)
    if not sents:
        return findings
    kept_text_tokens = _content_tokens(" ".join(s["text"] for s in sents if s["kept_ratio"] >= 0.5))

    for s in sents:
        toks = _content_tokens(s["text"])
        n_words = len(s["words"])
        t = s["start_f"] / FPS

        # --- OVERCUT: a fully-cut sentence whose content never reappears -----
        if s["kept_ratio"] == 0 and n_words >= 5 and toks:
            if _overlap(toks, kept_text_tokens) < 0.6:
                findings.append(finding(video, job_id, "overcut_content", t=t,
                    evidence=f"cut sentence with unique content: \"{s['text'][:90]}\"",
                    source="cut_qc", extra={"sentence": s["idx"]}))

        # --- OVERCUT: a PARTIALLY-cut sentence (bits sliced out of it) -------
        if 0 < s["kept_ratio"] < 1 and n_words >= 5:
            # runs of consecutive CUT words inside the sentence
            run, worst = [], []
            for (w, f, kp) in s["words"]:
                if not kp:
                    run.append(w)
                    if len(run) > len(worst):
                        worst = list(run)
                else:
                    run = []
            non_filler = [w for w in worst if _norm(w) not in _FILLERS]
            if len(non_filler) >= 3:
                findings.append(finding(video, job_id, "overcut_partial", t=t,
                    evidence=f"cut mid-sentence: lost \"{' '.join(worst)[:70]}\" from "
                             f"\"{s['text'][:70]}\"", source="cut_qc",
                    extra={"sentence": s["idx"], "lost": len(worst)}))

    # --- UNDERCUT: kept stumble — a kept fragment that restarts as the next
    #     kept sentence (the classic "most fusion— most fusion fails...") -----
    kept_sents = [s for s in sents if s["kept_ratio"] >= 0.5]
    for a, b in zip(kept_sents, kept_sents[1:]):
        a_toks = _content_tokens(a["text"])
        if not a_toks:
            continue
        a_terminal = a["text"].rstrip().endswith(_TERMINAL)
        b_head = _content_tokens(" ".join(t for t, _, _ in b["words"][:max(4, len(a["words"]))]))
        if (not a_terminal or len(a["words"]) <= 4) and _overlap(a_toks, b_head) >= 0.6:
            findings.append(finding(video, job_id, "undercut_stumble", t=a["start_f"] / FPS,
                evidence=f"kept stumble \"{a['text'][:60]}\" restarts as \"{b['text'][:60]}\"",
                source="cut_qc", extra={"sentence": a["idx"]}))

    # --- UNDERCUT: duplicate takes both kept --------------------------------
    for i, a in enumerate(kept_sents):
        a_toks = _content_tokens(a["text"])
        if len(a["words"]) < 5 or not a_toks:
            continue
        for b in kept_sents[i + 1:i + 6]:
            if len(b["words"]) < 5:
                continue
            b_toks = _content_tokens(b["text"])
            # Parallel structure ("flip the FANCY one" / "flip the DRUGSTORE
            # one") is two distinct beats, not a retake: if both sides own
            # content words the other lacks, skip. Demonstratives don't count.
            _demo = {"that", "there", "this", "then", "now", "here", "the"}
            if (a_toks - b_toks) - _demo and (b_toks - a_toks) - _demo:
                continue
            if _overlap(a_toks, b_toks) >= 0.75 \
                    and abs(len(a["words"]) - len(b["words"])) <= max(3, len(a["words"]) // 2):
                findings.append(finding(video, job_id, "undercut_dupe", t=b["start_f"] / FPS,
                    evidence=f"near-duplicate takes both kept: \"{a['text'][:55]}\" ~ \"{b['text'][:55]}\"",
                    source="cut_qc", extra={"a": a["idx"], "b": b["idx"]}))
                break

    # --- SEAM: orphan fragments (a kept island of <=3 words between cuts) ---
    for s in sents:
        kept_ws = [w for w in s["words"] if w[2]]
        if 0 < len(kept_ws) <= 3 and len(s["words"]) >= 6 \
                and all(_norm(w[0]) not in _FILLERS for w in kept_ws):
            findings.append(finding(video, job_id, "orphan_fragment", t=s["start_f"] / FPS,
                evidence=f"orphan kept fragment \"{' '.join(w[0] for w in kept_ws)}\" "
                         f"of \"{s['text'][:60]}\"", source="cut_qc",
                extra={"sentence": s["idx"]}))

    return findings


def kept_word_set(edl: dict, words: list[dict]) -> set:
    """Frozen (frame, word) kept-set for cross-run consistency comparison."""
    kept = _kept_predicate(edl)
    return {(ms_to_frame(w.get("start_ms", 0)), _norm(str(w.get("word", ""))))
            for w in words or [] if kept(ms_to_frame(w.get("start_ms", 0)))}


def consistency(a: set, b: set) -> float:
    """Jaccard similarity of two runs' kept-sets."""
    return len(a & b) / max(1, len(a | b))
