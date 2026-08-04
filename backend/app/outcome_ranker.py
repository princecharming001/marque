"""Outcome ranker — Palo's pairwise outcome predictor at phase-1 scale (R2 port, gaps #7).

Port of Palo_Server/palo_python/outcome_predictor/ (features.py, dataset.py, model.py,
anchors.py; docs/OUTCOME_PREDICTOR.md is the definitive writeup). Palo's measured numbers:
own-history pairwise 68.2% vs Claude zero-shot 49.3% (= coin flip) reading the same docs;
idea-TEXT-only scoring retains 59.4% — a pre-production idea can be ranked before anything
is filmed. That last result is what this module productizes: a linear pairwise ranker
P(A beats B) = sigmoid(w · (x_A − x_B)) trained per creator on their OWN settled posts,
used to silently rerank idea-bank briefs and to read the model back in plain English
(anchor brief: "mechanisms that win here / lose here").

Phase-1 scale reductions vs Palo (deliberate):
  - Pure python, no numpy, no embeddings, no LLM calls. The featurizer is surface
    statistics over title/body text plus small meta one-hots (Palo added 2×256-dim
    field-separated frontier embeddings — that is the phase-2 lever, and where most of
    the 59.4→68.2 gap lives).
  - MIN_VIEWS scaled 200 → 50 (Palo trained on YouTube-scale channels; Marque creators
    are sub-breakout). MIN_RATIO / RIGHT_CENSOR_DAYS / PAIR_WINDOW_DAYS are Palo's
    constants unchanged.
  - Anchor list trimmed to the ~20 most short-form-relevant statements, wording kept.

Pairwise task (why not raw views, verbatim from Palo's dataset.py): raw view counts are
dominated by follower count, channel growth, video age, and algorithmic luck. Pairing
videos from the same creator published within PAIR_WINDOW_DAYS where one got ≥ MIN_RATIO×
the views cancels channel size and era by construction. Controls kept from Palo:
right-censoring guard (posts younger than RIGHT_CENSOR_DAYS at label pull undercount —
excluded unless settled), deterministic alternating pair orientation (so "always pick A"
scores exactly 50% with no RNG), and time-ordered splits (train old, test newest).

HONESTY CONTRACT: this module never fabricates a score. No trained model ⇒ score() /
score_idea_text() return None, rerank() is the identity, anchor_brief() is "". Training
returns None below MIN_PAIRS usable pairs — never a junk model.

Persistence: one JSONB column `creators.outcome_model` (the integrator migrates it; this
module only assumes it exists), read/written via the PaloStore's `_request` in
app/palo_persistence.py style — every failure is caught and degrades (None / False).
Keyless (store=None) leaves the math fully usable in-memory.

Flag: `palo_flags.enabled(palo_flags.OUTCOME_RANKER)` gates train_for (the write path).
Integrators must check the same flag before load_model()+rerank() so OFF means the ported
path is never entered (palo_flags contract).
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime

from app import palo_flags

# --- dataset hygiene constants (Palo dataset.py, ported verbatim unless noted) --------
MIN_RATIO = 2.0            # a training pair requires winner views ≥ 2× loser views
RIGHT_CENSOR_DAYS = 30     # posts younger than this at label pull are excluded unless settled
PAIR_WINDOW_DAYS = 90      # both pair members published within this many days of each other
MIN_VIEWS = 50.0           # below this the outcome is mostly distribution luck (Palo: 200)
MIN_PAIRS = 8              # refuse to mint a model below this — never a junk model
MODEL_VERSION = 1

# --- featurizer -----------------------------------------------------------------------
# Fixed-length dense vector, every dim documented. Deterministic; no external deps.
# Text block (12 dims) over title/body; then small capped one-hot blocks for meta.
_TEXT_FEATURES = [
    "title_words",        # 0  word count of the title
    "log_body_words",     # 1  log1p(word count of the body)
    "avg_sentence_words", # 2  mean words per sentence over title+body
    "title_has_digit",    # 3  1.0 if any 0-9 character appears in the title
    "title_has_question", # 4  1.0 if '?' in title
    "first_person",       # 5  1.0 if i/i'm/my/me/we/our appears in title+body
    "second_person",      # 6  1.0 if you/your/you're appears in title+body
    "caps_words",         # 7  count of ALL-CAPS words (len ≥ 2) in title+body
    "has_quote",          # 8  1.0 if a double-quote character appears anywhere
    "title_negation",     # 9  1.0 if stop/never/don't/avoid/warning/quit/worst in title
    "title_colon_list",   # 10 1.0 if title has ':' or starts with a digit (listicle frame)
    "avg_word_len",       # 11 mean characters per word over title+body
]
# Meta one-hots, capped small on purpose (a few dozen training pairs per creator can't
# support more — same reasoning as Palo's TEXT_HASH_DIMS comment). Vocabularies are
# FIXED copies of prompts.ACTIVE_STYLES / prompts.SIGNAL_LIST — hardcoded here so the
# feature layout can never drift under a trained model when prompts.py evolves.
STYLE_VOCAB = ("talking_head", "green_screen", "broll_cutaway",
               "split_three", "duet_split", "faceless")
HOOK_SIGNAL_VOCAB = ("stakes", "authority", "curiosity", "patternInterrupt",
                     "specificity", "contrarian", "narrative", "callOut")
HOUR_BUCKETS = ("hour_0_5", "hour_6_11", "hour_12_17", "hour_18_23")

FEATURE_NAMES = (_TEXT_FEATURES
                 + [f"style_{s}" for s in STYLE_VOCAB]
                 + [f"hook_{h}" for h in HOOK_SIGNAL_VOCAB]
                 + list(HOUR_BUCKETS))
FEATURE_DIM = len(FEATURE_NAMES)  # 12 + 6 + 8 + 4 = 30

_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_NEGATION_RE = re.compile(r"\b(stop|never|don'?t|avoid|warning|quit|worst)\b", re.I)
_FIRST_PERSON = {"i", "i'm", "im", "my", "me", "we", "our", "mine"}
_SECOND_PERSON = {"you", "your", "you're", "youre", "yours"}


def featurize(title: str, body: str = "", meta: dict | None = None) -> list[float]:
    """Title/body (+ optional meta) -> FEATURE_DIM dense floats. Deterministic, total
    function: bad inputs coerce to str/ignored, output length is always FEATURE_DIM.
    meta keys (all optional): style (STYLE_VOCAB), hook_signal (HOOK_SIGNAL_VOCAB),
    hour (int 0-23). Unknown/absent meta ⇒ all-zero one-hot block (scoring an idea
    with no meta stays valid — the missing blocks shift every idea's score by the same
    constant, leaving the ranking untouched)."""
    title, body = str(title or ""), str(body or "")
    meta = meta if isinstance(meta, dict) else {}
    combined = (title + " " + body).strip()
    words = _WORD_RE.findall(combined)
    title_words = _WORD_RE.findall(title)
    body_words = _WORD_RE.findall(body)
    sentences = [s for s in re.split(r"[.!?]+", combined) if _WORD_RE.search(s)]
    lowered = {w.lower() for w in words}

    vec = [
        float(len(title_words)),
        math.log1p(float(len(body_words))),
        (sum(len(_WORD_RE.findall(s)) for s in sentences) / len(sentences)) if sentences else 0.0,
        1.0 if any(c.isdigit() for c in title) else 0.0,
        1.0 if "?" in title else 0.0,
        1.0 if lowered & _FIRST_PERSON else 0.0,
        1.0 if lowered & _SECOND_PERSON else 0.0,
        float(sum(1 for w in words if len(w) >= 2 and w.isupper())),
        1.0 if any(q in combined for q in ('"', "“", "”")) else 0.0,
        1.0 if _NEGATION_RE.search(title) else 0.0,
        1.0 if (":" in title or (title.strip()[:1].isdigit())) else 0.0,
        (sum(len(w) for w in words) / len(words)) if words else 0.0,
    ]
    style = str(meta.get("style") or "")
    vec += [1.0 if style == s else 0.0 for s in STYLE_VOCAB]
    hook = str(meta.get("hook_signal") or "")
    vec += [1.0 if hook == h else 0.0 for h in HOOK_SIGNAL_VOCAB]
    try:
        hour = int(meta.get("hour"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        hour = -1
    vec += [1.0 if 0 <= hour <= 23 and hour // 6 == b else 0.0 for b in range(4)]
    return vec


# --- small linear algebra (by hand — no numpy in this tree) ---------------------------

def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _parse_ts(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00").split("+")[0])
    except ValueError:
        return None


# --- dataset construction (Palo dataset.py at reduced scale) --------------------------

def usable_samples(samples: list[dict]) -> list[dict]:
    """Hygiene pass: coerce features, drop sub-MIN_VIEWS outcomes, apply the
    right-censoring guard. A sample is {views: float, features: [float], ts: str,
    settled: bool?}. Censor: relative to the newest ts in the batch (proxy for label
    pull time), samples younger than RIGHT_CENSOR_DAYS are excluded — their views are
    still climbing so the label undercounts — unless marked settled (Marque's settle
    machinery fixes the label, the analogue of Palo's fixed-horizon views)."""
    out = []
    for s in samples or []:
        if not isinstance(s, dict):
            continue
        try:
            views = float(s.get("views"))
            feats = [float(x) for x in s.get("features") or []]
        except (TypeError, ValueError):
            continue
        if not feats or views < MIN_VIEWS:
            continue
        out.append({"views": views, "features": feats, "ts": str(s.get("ts") or ""),
                    "settled": bool(s.get("settled", False))})
    dated = [d for d in (_parse_ts(s["ts"]) for s in out) if d]
    if dated:
        pull_proxy = max(dated)
        out = [s for s in out
               if s["settled"] or not _parse_ts(s["ts"])
               or (pull_proxy - _parse_ts(s["ts"])).days >= RIGHT_CENSOR_DAYS]
    return out


def build_pairs(samples: list[dict]) -> list[tuple[list[float], float]]:
    """All qualifying within-batch pairs as (feature_diff, label). Winner needs
    ≥ MIN_RATIO× the loser's views and both published within PAIR_WINDOW_DAYS (when
    both are dated). Each unordered pair emitted once; orientation alternates by index
    parity (Palo's RNG-free class balance — "always pick A" scores exactly 50%)."""
    pairs: list[tuple[list[float], float]] = []
    n = 0
    for i in range(len(samples)):
        for j in range(i + 1, len(samples)):
            a, b = samples[i], samples[j]
            da, db = _parse_ts(a["ts"]), _parse_ts(b["ts"])
            if da and db and abs((da - db).days) > PAIR_WINDOW_DAYS:
                continue
            va, vb = a["views"], b["views"]
            (hi, lo), (vhi, vlo) = ((a, b), (va, vb)) if va >= vb else ((b, a), (vb, va))
            if vlo <= 0 or vhi / max(vlo, 1e-9) < MIN_RATIO:
                continue
            diff = [x - y for x, y in zip(hi["features"], lo["features"])]
            if n % 2 == 0:
                pairs.append((diff, 1.0))
            else:
                pairs.append(([-x for x in diff], 0.0))
            n += 1
    return pairs


def time_split(samples: list[dict], test_frac: float = 0.2) -> tuple[list[dict], list[dict]]:
    """Train on older samples, test on the newest — matches deployment (score tomorrow's
    idea from yesterday's catalog). Undated samples go to train."""
    dated = sorted((s for s in samples if _parse_ts(s.get("ts"))),
                   key=lambda s: _parse_ts(s["ts"]))
    undated = [s for s in samples if not _parse_ts(s.get("ts"))]
    cut = max(1, int(len(dated) * (1 - test_frac)))
    return dated[:cut] + undated, dated[cut:]


# --- trainer (Palo model.py: RankNet with a linear scorer, plain-python SGD) ----------

def train_pairwise(samples: list[dict], epochs: int = 60, lr: float = 0.1,
                   l2: float = 0.01, trained_at: str = "") -> dict | None:
    """Logistic pairwise loss P(A beats B) = sigmoid(w·(x_A − x_B)) over standardized
    feature diffs; deterministic per-pair SGD (fixed order, no RNG — orientation
    alternation already balances classes) with L2. Returns the model dict
    {w, mean, std, n_pairs, trained_at, version} or None below MIN_PAIRS — a junk
    model is worse than no model (the whole API degrades honestly on None).
    `trained_at` is caller-supplied (no wall-clock reads here; determinism)."""
    usable = usable_samples(samples)
    dim = len(usable[0]["features"]) if usable else 0
    usable = [s for s in usable if len(s["features"]) == dim]
    pairs = build_pairs(usable)
    if len(pairs) < MIN_PAIRS:
        return None
    # Standardization stored WITH the model: mean/std over the usable samples' features.
    # Diffs need only the scale (mean cancels); score() uses both so single-item scores
    # center near 0 and anchor signs stay readable.
    n = float(len(usable))
    mean = [sum(s["features"][k] for s in usable) / n for k in range(dim)]
    std = []
    for k in range(dim):
        var = sum((s["features"][k] - mean[k]) ** 2 for s in usable) / n
        sd = math.sqrt(var)
        std.append(sd if sd > 1e-9 else 1.0)
    z_pairs = [([d / sd for d, sd in zip(diff, std)], y) for diff, y in pairs]
    w = [0.0] * dim
    l2_step = l2 / len(z_pairs)  # per-update decay ≈ full-batch L2 per epoch
    for _ in range(max(1, int(epochs))):
        for dx, y in z_pairs:
            g = _sigmoid(_dot(w, dx)) - y
            for k in range(dim):
                w[k] -= lr * (g * dx[k] + l2_step * w[k])
    return {"w": w, "mean": mean, "std": std, "n_pairs": len(pairs),
            "trained_at": str(trained_at or ""), "version": MODEL_VERSION}


def _valid_model(model) -> bool:
    if not isinstance(model, dict):
        return False
    w, mean, std = model.get("w"), model.get("mean"), model.get("std")
    return (isinstance(w, list) and isinstance(mean, list) and isinstance(std, list)
            and len(w) > 0 and len(w) == len(mean) == len(std)
            and all(isinstance(x, (int, float)) for x in w))


def pairwise_accuracy(model: dict | None, pairs: list[tuple[list[float], float]]) -> float:
    """Fraction of (raw feature diff, label) pairs the model orders correctly.
    NaN-free honesty: 0.0 on no model / no pairs (callers gate on n themselves)."""
    if not _valid_model(model) or not pairs:
        return 0.0
    std = model["std"]  # type: ignore[index]
    hits = 0
    for diff, y in pairs:
        if len(diff) != len(std):
            continue
        p = _sigmoid(_dot(model["w"], [d / sd for d, sd in zip(diff, std)]))
        hits += 1 if (p > 0.5) == (y > 0.5) else 0
    return hits / len(pairs)


# --- scoring --------------------------------------------------------------------------

def score(model: dict | None, features: list[float]) -> float | None:
    """w·((x−mean)/std). None model / shape mismatch / bad input ⇒ None — never a
    fabricated number."""
    if not _valid_model(model):
        return None
    try:
        feats = [float(x) for x in features]
    except (TypeError, ValueError):
        return None
    if len(feats) != len(model["w"]):  # type: ignore[index]
        return None
    try:
        z = [(f - m) / sd for f, m, sd in zip(feats, model["mean"], model["std"])]
        return float(_dot(model["w"], z))
    except Exception:
        return None


def score_idea_text(model: dict | None, title: str, content: str = "") -> float | None:
    """Rank a PRE-PRODUCTION idea from its text alone (Palo's idea-text gate: 59.4%
    retained vs 71.1% full-doc — the reason briefs can be reranked before filming)."""
    if model is None:
        return None
    return score(model, featurize(title, content))


def rerank(briefs: list[dict], model: dict | None) -> list[dict]:
    """STABLE sort of idea briefs by outcome score, descending. Identity (same objects,
    same order) when model is None — the feed degrades to today's ordering, exactly as
    the cold-start plan in gaps #7 specifies. Never drops an item: briefs that cannot
    be scored keep their relative order after all scored ones."""
    items = list(briefs or [])
    if model is None or not items:
        return items
    keyed = []
    for idx, b in enumerate(items):
        s = None
        if isinstance(b, dict):
            s = score_idea_text(model, str(b.get("title") or ""),
                                str(b.get("summary") or b.get("body") or ""))
        keyed.append((s is None, -(s or 0.0), idx, b))
    keyed.sort(key=lambda t: (t[0], t[1], t[2]))  # idx tiebreak ⇒ stable
    return [b for _, _, _, b in keyed]


# --- anchors (Palo anchors.py — read the trained model back out in English) -----------
# Trimmed to the ~20 most short-form-relevant statements; wording preserved verbatim.
# At phase-1 scale (no embeddings) each statement reads through the same surface
# featurizer as real ideas — a directional readout, not Palo's full semantic probe.
HOOK_ANCHORS = (
    "opens with a direct question to the viewer",
    "cold-opens mid-action with no setup",
    "opens with a bold claim or hot take",
    "opens with a number or specific statistic",
    "opens by showing the end result first, then explains how",
    "opens with the creator's face talking directly to camera",
    "opens with a warning or 'stop doing this' framing",
    "opens with a relatable everyday situation",
    "opens with a countdown or list promise",
    "opens with a transformation teaser (before state shown)",
    "opens by directly addressing a specific audience segment",
)
STRUCT_ANCHORS = (
    "escalates stakes steadily until the payoff",
    "uses a list structure counting through items",
    "rapid cuts every second or two",
    "tells a personal story with a clear arc",
    "tutorial structure: step by step how-to",
    "delays the payoff until the final seconds",
    "ends with a question or open loop for comments",
    "ends with a call to action to follow or comment",
    "keeps it under 20 seconds, extremely tight",
    "raw unpolished phone-camera feel",
)
ANCHOR_STATEMENTS = HOOK_ANCHORS + STRUCT_ANCHORS


def anchor_brief(model: dict | None) -> str:
    """Plain-English readout of a creator's OWN trained model: score every anchor
    statement, return top 5 / bottom 5 as a readable block. \"\" without a trained
    model — this brief only exists when grounded in real outcomes (honesty contract;
    Palo's central finding is that what wins is strongly creator-specific, so an
    ungrounded version of this text would be exactly the fabrication to avoid)."""
    if not _valid_model(model):
        return ""
    scored = []
    for stmt in ANCHOR_STATEMENTS:
        s = score(model, featurize(stmt))
        if s is not None:
            scored.append((s, stmt))
    if not scored:
        return ""
    scored.sort(key=lambda t: (-t[0], t[1]))
    lines = ["Hooks/mechanisms that WIN here (scored against your own outcome model):"]
    lines += [f"  {s:+.2f}  {stmt}" for s, stmt in scored[:5]]
    lines.append("Hooks/mechanisms that LOSE here:")
    lines += [f"  {s:+.2f}  {stmt}" for s, stmt in scored[-5:]]
    return "\n".join(lines)


# --- persistence (creators.outcome_model JSONB, palo_persistence style) ---------------

async def load_model(store, creator_id: str) -> dict | None:
    """The creator's persisted model, or None (keyless / unreal creator / no row /
    malformed JSONB / any transport failure — every miss degrades identically)."""
    if store is None or not palo_flags.real_creator(creator_id):
        return None
    try:
        r = await store._request(
            "GET", "/creators",
            params={"creator_id": f"eq.{creator_id}", "select": "outcome_model"})
        if not (r and r.status_code == 200):
            return None
        rows = r.json()
        model = rows[0].get("outcome_model") if rows else None
        return model if _valid_model(model) else None
    except Exception as e:
        logging.warning("[ranker] load_model failed for %s: %s", creator_id, e)
        return None


async def save_model(store, creator_id: str, model: dict) -> bool:
    """Upsert onto creators.outcome_model (merge-duplicates on creator_id, mirroring
    PaloStore.set_creator_tier). real_creator-guarded — a demo/default id must never
    own a learned model (audit F13). False on any failure; callers carry on."""
    if store is None or not palo_flags.real_creator(creator_id) or not _valid_model(model):
        return False
    try:
        r = await store._request(
            "POST", "/creators", params={"on_conflict": "creator_id"},
            json={"creator_id": creator_id, "outcome_model": model},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return bool(r and r.status_code < 300)
    except Exception as e:
        logging.warning("[ranker] save_model failed for %s: %s", creator_id, e)
        return False


# --- end-to-end convenience (the insights-settle-sweep hook) --------------------------

def _post_views(post: dict) -> float | None:
    v = post.get("views")
    if v is None and isinstance(post.get("metrics"), dict):
        v = post["metrics"].get("views")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _post_ts(post: dict) -> str:
    for key in ("ts", "published_at", "settled_at", "scheduled_at", "created_at"):
        if post.get(key):
            return str(post[key])
    return ""


async def train_for(store, creator_id: str, settled_posts: list[dict]) -> dict | None:
    """Featurize a creator's settled posts, train, persist. The one call the insights
    settle sweep makes after settling a creator's metrics. Flag-gated + real_creator-
    guarded; None whenever no honest model can be minted (flag off, demo id, thin
    history, any exception). Keyless (store=None) still returns the trained model so
    callers can rerank in-memory — only persistence degrades."""
    if not palo_flags.enabled(palo_flags.OUTCOME_RANKER):
        return None
    if not palo_flags.real_creator(creator_id):
        return None
    try:
        samples = []
        for p in settled_posts or []:
            if not isinstance(p, dict):
                continue
            views = _post_views(p)
            if views is None:
                continue
            ts = _post_ts(p)
            dt = _parse_ts(ts)
            meta = {"style": p.get("style"), "hook_signal": p.get("hook_signal"),
                    "hour": dt.hour if dt else None}
            feats = featurize(str(p.get("title") or ""),
                              str(p.get("summary") or p.get("caption") or ""), meta)
            samples.append({"views": views, "features": feats, "ts": ts,
                            "settled": bool(p.get("settled", True))})
        trained_at = ""
        dated = [d for d in (_parse_ts(s["ts"]) for s in samples) if d]
        if dated:
            trained_at = max(dated).isoformat()  # newest label ts — no wall-clock reads
        model = train_pairwise(samples, trained_at=trained_at)
        if model is None:
            return None
        if store is not None:
            await save_model(store, creator_id, model)
        return model
    except Exception as e:
        logging.warning("[ranker] train_for failed for %s: %s", creator_id, e)
        return None
