"""Voice profile: how THIS creator talks, built from their own spoken reels.

The writer gets three things from a profile (research: few-shot with the author's own
text is the strongest voice lever, 3-5 exemplars saturate, an editable prose description
plus real excerpts is what users understand, and the user's own corrections are the
highest-leverage field):

  1. `paragraph`   4-7 plain second-person sentences ("You call people 'you guys'. ...").
                   Editable in the app; once edited, a rebuild never overwrites it.
  2. `exemplars`   verbatim cleaned transcripts, picked deterministically
                   (select_exemplars; alt=True gives a second set for best-of-2).
  3. `corrections` sentences the creator wrote or that came from their script edits
                   (correction_from_edit). Rendered verbatim, never trimmed for budget.

Pipeline: spoken_samples (transcripts only; caption-only posts are WRITTEN text and are
ignored) -> compute_stats (pure python) -> ONE writer-model call that names phrases
(validated in code as verbatim substrings) -> profile. Keyless / LLM failure still yields
a profile from a stats template; no spoken data yields cold_start_profile from the
brand sliders.

Storage: no new table and no migration. The profile is one JSONB blob in the existing
`reels_cache` key/value table under key "voice:<creator_id>" (store.upsert_reels_cache /
store.load_reels_cache). load/save never raise; a None store is keyless mode.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import re
import statistics
from collections import Counter
from datetime import datetime, timezone

from app.scripts_v2 import flags, llm

STORE_PREFIX = "voice:"
PROFILE_VERSION = 1

_MIN_WORDS, _MAX_WORDS = 25, 400
_MAX_PHRASES = 12
_MAX_CORRECTIONS = 12
_LOW_NOTE = "We only have a little of their real speech, so lean on the niche and keep it plain."

_FILLER = re.compile(r"(?<![\w'])(?:u+m+|u+h+|e+r+m+|uhm+)(?![\w'])[,.]?\s*", re.I)
_REPEAT = re.compile(r"\b(\w+(?:'\w+)?)(?:\s+\1\b)+", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_TOKEN = re.compile(r"[a-z0-9']+")
_CONTRACTION = re.compile(r"\b\w+'(?:s|t|re|m|ve|ll|d)\b", re.I)
_EXPANDED = re.compile(
    r"\b(?:it is|do not|you are|i am|that is|cannot|can not|will not|is not|does not|did not|"
    r"we are|they are|there is|i have|you have|i will|you will|would not|could not|should not|"
    r"are not|was not|have not)\b", re.I)
_SWEAR = re.compile(r"\b(?:damn\w*|hell|shit\w*|fuck\w*|crap\w*|ass|asshole\w*|bitch\w*)\b", re.I)
_ADDR_ANY = re.compile(r"(?<![\w'])(you guys|y'all|bro|fam)(?![\w'])", re.I)
_ADDR_VOCATIVE = re.compile(
    r"(?:\b(?:hey|hi|hello|okay|ok|alright|listen|look|so)\s+(guys|friends)\b"
    r"|,\s*(guys|friends)\s*(?=[,.!?:]|$))", re.I)
_ADDRESS_ORDER = ("you guys", "guys", "bro", "y'all", "friends", "fam")
_STOP = frozenset(
    "a an the and or but so to of in on at for with is it it's that this i you me my your we "
    "be are was were do just like if then not have has had what they them he she as from up out "
    "about can will get go all there its i'm don't gonna really very one gotta yeah okay".split())
# A phrase ending on one of these is a fragment of a longer one ("thing for").
_DANGLING = frozenset("a an the and or but to of in on at for with from as my your".split())
# Discourse tics are voice even though every word is a stopword.
_TICS = frozenset({"you know", "i mean", "and like", "you guys", "like literally", "and so"})
_CTA_EXPLICIT = re.compile(r"\b(?:follow|subscribe|comment|link in (?:my )?bio|save this|share this|dm me)\b", re.I)
_CTA_SOFT = re.compile(r"\b(?:let me know|try (?:it|this)|hope this helps|see you|talk soon)\b", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(text: str) -> str:
    """Lowercase, punctuation to spaces (apostrophes kept), collapsed: the phrase-match space."""
    return " ".join(re.sub(r"[^\w']+", " ", (text or "").lower()).split())


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]


def _tokens(text: str) -> list[str]:
    return [t.strip("'") for t in _TOKEN.findall((text or "").lower()) if t.strip("'")]


def _cap_words(text: str, n: int) -> str:
    words = text.split()
    return text if len(words) <= n else " ".join(words[:n])


def _trim_chars(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "..."


# ------------------------------------------------------------------ samples
def clean_transcript(text: str) -> str:
    t = " ".join((text or "").split())
    if not t:
        return ""
    t = _FILLER.sub("", t)
    t = _REPEAT.sub(r"\1", t)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"([,;:])(?=[,.!?])", "", t)
    t = " ".join(t.split()).strip(" ,;:")
    if not t:
        return ""
    t = t[0].upper() + t[1:]
    if t[-1] not in ".!?":
        t += "."
    return t


def spoken_samples(posts: list[dict] | None, media: list[dict] | None = None) -> list[dict]:
    raw: list[tuple[str, str, int]] = []
    for p in posts or []:
        if isinstance(p, dict) and str(p.get("transcript") or "").strip():
            raw.append((str(p["transcript"]), "reel", _int(p.get("views"))))
    for m in media or []:
        if (isinstance(m, dict) and (m.get("kind") or "video") == "video"
                and str(m.get("transcript") or "").strip()):
            raw.append((str(m["transcript"]), "upload", 0))
    out: list[dict] = []
    seen: set[str] = set()
    for text, source, views in raw:
        clean = clean_transcript(text)
        words = len(clean.split())
        key = _norm(clean)[:200]
        if not (_MIN_WORDS <= words <= _MAX_WORDS) or key in seen:
            continue
        seen.add(key)
        out.append({"text": clean, "source": source, "views": views, "words": words})
    out.sort(key=lambda s: -s["views"])  # stable: ties keep input order
    return out


def _int(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


# ------------------------------------------------------------------ stats
def _address_form(texts: list[str]) -> str:
    counts: Counter = Counter()
    for t in texts:
        for m in _ADDR_ANY.finditer(t):
            counts[m.group(1).lower()] += 1
        for m in _ADDR_VOCATIVE.finditer(t):
            counts[(m.group(1) or m.group(2)).lower()] += 1
    if not counts:
        return "you"
    return max(_ADDRESS_ORDER, key=lambda f: (counts[f], -_ADDRESS_ORDER.index(f)))


def _shared_run(a: str, b: str) -> int:
    """Longest run of consecutive tokens two phrases share."""
    x, y, best = a.split(), b.split(), 0
    for i in range(len(x)):
        for j in range(len(y)):
            k = 0
            while i + k < len(x) and j + k < len(y) and x[i + k] == y[j + k]:
                k += 1
            best = max(best, k)
    return best


def _frequent_phrases(texts: list[str], limit: int = 10) -> list[str]:
    df: Counter = Counter()
    total: Counter = Counter()
    for t in texts:
        grams = []
        for sent in _sentences(t):  # never across a sentence break
            toks = _tokens(sent)
            grams += [" ".join(toks[i:i + n]) for n in (2, 3, 4) for i in range(len(toks) - n + 1)]
        total.update(grams)
        df.update(set(grams))
    cands = [g for g, d in df.items()
             if d >= 2 and (g in _TICS or (not all(w in _STOP for w in g.split())
                                           and g.split()[-1] not in _DANGLING))]
    cands.sort(key=lambda g: (-df[g], -len(g.split()), -total[g], g))
    chosen: list[str] = []
    for g in cands:
        if any(_shared_run(g, c) >= max(2, len(g.split()) - 1) for c in chosen):
            continue  # a sub-phrase or shifted near-twin of one already kept
        chosen.append(g)
        if len(chosen) >= limit:
            break
    return chosen


def compute_stats(samples: list[dict]) -> dict:
    texts = [s["text"] for s in samples or [] if s.get("text")]
    sent_lens = [len(_tokens(s)) for t in texts for s in _sentences(t)]
    sent_lens = [n for n in sent_lens if n > 0]
    srt = sorted(sent_lens)
    joined = " ".join(texts)
    contr, expd = len(_CONTRACTION.findall(joined)), len(_EXPANDED.findall(joined))
    n_words = len(_tokens(joined))
    swear_rate = 100.0 * len(_SWEAR.findall(joined)) / n_words if n_words else 0.0
    return {
        "n": len(texts),
        "median_sentence_words": float(statistics.median(srt)) if srt else 0.0,
        "p90_sentence_words": float(srt[max(0, math.ceil(0.9 * len(srt)) - 1)]) if srt else 0.0,
        "contraction_rate": round(contr / (contr + expd), 3) if (contr + expd) else None,
        "fragment_rate": round(sum(1 for n in srt if n <= 4) / len(srt), 3) if srt else 0.0,
        "typical_words": int(statistics.median([len(t.split()) for t in texts])) if texts else 0,
        "address_form": _address_form(texts),
        "swearing": "none" if swear_rate == 0 else ("light" if swear_rate < 1.0 else "heavy"),
        "openers": [_cap_words((_sentences(t) or [t])[0], 20) for t in texts[:5]],
        "closers": [_cap_words((_sentences(t) or [t])[-1], 20) for t in texts[:5]],
        "frequent_phrases": _frequent_phrases(texts),
    }


def select_exemplars(samples: list[dict], k: int = 3, *, alt: bool = False) -> list[dict]:
    pool = sorted(samples or [], key=lambda s: (0 if 40 <= s.get("words", 0) <= 220 else 1,
                                                -s.get("views", 0), s.get("text", "")))
    if not alt or not pool:
        return pool[:k]
    if len(pool) >= 2 * k:
        return pool[k:2 * k]
    shift = (k % len(pool)) or (1 if len(pool) > 1 else 0)
    return (pool[shift:] + pool[:shift])[:k]


# ------------------------------------------------------------------ profiles
def _blank(creator_id: str) -> dict:
    return {"version": PROFILE_VERSION, "creator_id": creator_id or "", "updated_at": _now(),
            "paragraph": "", "paragraph_edited": False, "phrases": [], "openers": [], "closers": [],
            "openers_style": "", "closers_style": "", "cta_habit": "none", "address": "you",
            "swearing": "none", "typical_words": 0, "never_say": [], "never_say_edited": False,
            "corrections": [], "stats": compute_stats([]), "exemplars": [],
            "data_confidence": "low", "built_from": {"reels": 0, "transcribed": 0, "uploads": 0}}


def _slider(voice: dict, key: str) -> float | None:
    try:
        return min(1.0, max(0.0, float(voice[key])))
    except (KeyError, TypeError, ValueError):
        return None


def cold_start_profile(brand: dict) -> dict:
    brand = brand or {}
    voice = brand.get("voice") if isinstance(brand.get("voice"), dict) else {}
    lines: list[str] = []
    for key, lo, mid, hi in (
        ("funnyToSerious", "You keep it light and joke around between points.",
         "You slip in a joke now and then but mostly stay on the point.",
         "You stay serious and get to the point."),
        ("polishedToRaw", "You speak in clean, finished sentences.",
         "You sound prepared but not scripted.",
         "You talk off the cuff: short sentences, a few fragments, nothing rehearsed."),
        ("teacherToPeer", "You explain like a coach walking someone through it, step by step.",
         "You share what you know without lecturing.",
         "You talk to people like a friend at the same level, not from above."),
    ):
        v = _slider(voice, key)
        if v is not None:
            lines.append(lo if v < 0.35 else (hi if v > 0.65 else mid))
    niche = str(brand.get("niche") or "").strip()
    if niche:
        lines.append(f"You talk about {niche.lower()} in plain words a beginner would use.")
    if not lines:
        lines.append("You talk plainly, in short spoken sentences, like you're telling a friend.")
    prof = _blank(str(brand.get("creator_id") or ""))
    prof["paragraph"] = " ".join(lines)
    seen: set[str] = set()
    for c in brand.get("catchphrases") or []:
        c = " ".join(str(c).split())[:60]
        if c and _norm(c) not in seen and len(prof["phrases"]) < _MAX_PHRASES:
            seen.add(_norm(c))
            prof["phrases"].append({"text": c, "on": True})
    return prof


def _cta_habit(closers: list[str]) -> str:
    joined = " ".join(closers)
    return "explicit" if _CTA_EXPLICIT.search(joined) else ("soft" if _CTA_SOFT.search(joined) else "none")


def _stats_paragraph(st: dict) -> str:
    out: list[str] = []
    addr = st.get("address_form") or "you"
    out.append(f"You call people '{addr}'." if addr != "you" else "You talk straight to one person as 'you'.")
    m = st.get("median_sentence_words") or 0
    if m and m <= 8:
        out.append(f"You talk in short bursts, around {round(m)} words a sentence.")
    elif m and m <= 14:
        out.append(f"Your sentences run about {round(m)} words, like normal talking.")
    elif m:
        out.append("Your sentences run long and keep going with 'and' and 'so'.")
    if (st.get("fragment_rate") or 0) >= 0.2:
        out.append(f"About {round(100 * st['fragment_rate'])}% of your sentences are four words or fewer.")
    cr = st.get("contraction_rate")
    if cr is not None and cr >= 0.8:
        out.append("You almost always use contractions like it's, don't, you're.")
    elif cr is not None and cr < 0.4:
        out.append("You often say the full words, like do not and it is.")
    if st.get("swearing") in ("light", "heavy"):
        out.append("You swear a little." if st["swearing"] == "light" else "You swear freely.")
    if st.get("frequent_phrases"):
        out.append("You keep coming back to " + " and ".join(f"'{p}'" for p in st["frequent_phrases"][:2]) + ".")
    if st.get("typical_words"):
        out.append(f"A typical video of yours is about {st['typical_words']} words.")
    return " ".join(out[:7])


def _verbatim(phrase: str, corpus_norm: str) -> bool:
    p = _norm(phrase)
    return bool(p) and f" {p} " in corpus_norm


_BUILD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["paragraph", "phrases", "openers_style", "closers_style", "cta_habit", "never_say"],
    "properties": {
        "paragraph": {"type": "string"},
        "phrases": {"type": "array", "items": {"type": "string"}},
        "openers_style": {"type": "string"},
        "closers_style": {"type": "string"},
        "cta_habit": {"type": "string", "enum": ["none", "soft", "explicit"]},
        "never_say": {"type": "array", "items": {"type": "string"}},
    },
}
_BUILD_SYSTEM = (
    "You study how one short-form video creator TALKS, from transcripts of their own videos. "
    "Describe what you can observe, not what you admire. Return JSON only.\n"
    "- paragraph: 4-7 short plain sentences in second person about how this person talks "
    "(\"You call people 'you guys'. You start mid-story about half the time.\"). Concrete and "
    "observable: words they use, how they open, sentence length, jokes, swearing, how they end. "
    "No adjective piles, no praise, no advice.\n"
    "- phrases: up to 8 short phrases they ACTUALLY say, copied exactly from the transcripts.\n"
    "- openers_style: one sentence on how they start. closers_style: one sentence on how they end.\n"
    "- cta_habit: none | soft | explicit (do they ask viewers to follow/comment/buy?).\n"
    "- never_say: up to 6 words or phrases that would sound wrong from them (e.g. corporate words)."
)


async def build_profile(creator_id: str, brand: dict, posts: list[dict] | None,
                        media: list[dict] | None = None) -> dict:
    samples = spoken_samples(posts, media)
    built_from = {"reels": len(posts or []),
                  "transcribed": sum(1 for p in posts or [] if str((p or {}).get("transcript") or "").strip()),
                  "uploads": sum(1 for s in samples if s["source"] == "upload")}
    if len(samples) < 2:
        prof = cold_start_profile(brand)
        prof.update(creator_id=creator_id or "", built_from=built_from)
        return prof
    st = compute_stats(samples)
    ordered = select_exemplars(samples, k=len(samples))
    chunks, used = [], 0
    for i, s in enumerate(ordered, 1):
        piece = _trim_chars(s["text"], 900)
        if used + len(piece) > 9000:
            break
        chunks.append(f"[{i}] {piece}")
        used += len(piece)
    stats_view = {k: st[k] for k in ("median_sentence_words", "p90_sentence_words", "contraction_rate",
                                     "fragment_rate", "typical_words", "address_form", "swearing",
                                     "frequent_phrases")}
    user = (f"STATS (measured):\n{json.dumps(stats_view, sort_keys=True)}\n\n"
            "TRANSCRIPTS (their own videos, lightly cleaned):\n" + "\n\n".join(chunks))
    out, _ = await llm.call(model=flags.writer_model(), system=_BUILD_SYSTEM,
                            messages=[llm.text_turn("user", user)], schema=_BUILD_SCHEMA,
                            max_tokens=1600, effort="low", route="voice.build")
    out = out if isinstance(out, dict) else {}
    corpus = " " + " ".join(_norm(s["text"]) for s in samples) + " "
    phrases: list[dict] = []
    seen: set[str] = set()
    for p in list(out.get("phrases") or []) or st["frequent_phrases"]:
        p = " ".join(str(p).split()).strip(" \"'.,!?")
        if p and len(p) <= 60 and len(p.split()) <= 8 and _norm(p) not in seen and _verbatim(p, corpus):
            seen.add(_norm(p))
            phrases.append({"text": p, "on": True})
        if len(phrases) >= 8:
            break
    paragraph = _trim_chars(" ".join(str(out.get("paragraph") or "").split()), 900) or _stats_paragraph(st)
    never = []
    for w in out.get("never_say") or []:
        w = " ".join(str(w).split())[:40]
        if w and w.lower() not in {x.lower() for x in never}:
            never.append(w)
    cta = out.get("cta_habit") if out.get("cta_habit") in ("none", "soft", "explicit") else _cta_habit(st["closers"])
    prof = _blank(creator_id)
    prof.update(
        paragraph=paragraph, phrases=phrases, openers=st["openers"], closers=st["closers"],
        openers_style=_trim_chars(" ".join(str(out.get("openers_style") or "").split()), 200)
        or (f'You open with lines like "{st["openers"][0]}"' if st["openers"] else ""),
        closers_style=_trim_chars(" ".join(str(out.get("closers_style") or "").split()), 200)
        or (f'You end with lines like "{st["closers"][0]}"' if st["closers"] else ""),
        cta_habit=cta, address=st["address_form"], swearing=st["swearing"],
        typical_words=st["typical_words"], never_say=never[:6], stats=st,
        exemplars=[dict(e) for e in select_exemplars(samples, k=8)],
        data_confidence="high" if len(samples) >= 8 else ("medium" if len(samples) >= 4 else "low"),
        built_from=built_from)
    if os.environ.get("VOICE_NEGATIVES") == "1":
        prof["negatives"] = await style_negatives(prof, samples)
    return prof


# ------------------------------------------------------------------ storage
async def load(store, creator_id: str) -> dict | None:
    if store is None or not creator_id:
        return None
    try:
        entry = await store.load_reels_cache(STORE_PREFIX + creator_id)
    except Exception as e:  # never raise from storage
        logging.info("[voice] load failed for %s: %s", creator_id, e)
        return None
    return entry if isinstance(entry, dict) and "paragraph" in entry else None


async def save(store, creator_id: str, profile: dict) -> bool:
    if store is None or not creator_id or not isinstance(profile, dict):
        return False
    try:
        return bool(await store.upsert_reels_cache(STORE_PREFIX + creator_id,
                                                   {**profile, "creator_id": creator_id}))
    except Exception as e:
        logging.info("[voice] save failed for %s: %s", creator_id, e)
        return False


# ------------------------------------------------------------------ user edits
def add_correction(profile: dict, text: str) -> dict:
    prof = copy.deepcopy(profile or {})
    t = " ".join(str(text or "").split())[:200]
    cur = [c for c in prof.get("corrections") or [] if c.lower() != t.lower()]
    if t:
        cur.append(t)
    prof["corrections"] = cur[-_MAX_CORRECTIONS:]
    return prof


def apply_edit(profile: dict, *, paragraph: str | None = None, phrases: list[dict] | None = None,
               never_say: list[str] | None = None, correction: str | None = None) -> dict:
    prof = copy.deepcopy(profile or {})
    if paragraph is not None:
        prof["paragraph"] = _trim_chars(" ".join(paragraph.split()), 1200)
        prof["paragraph_edited"] = True
    if phrases is not None:
        cur = [dict(p) for p in prof.get("phrases") or []]
        index = {_norm(p.get("text", "")): p for p in cur}
        for edit in phrases:
            text = " ".join(str((edit or {}).get("text") or "").split())[:60]
            on = bool((edit or {}).get("on"))
            if not text:
                continue
            if _norm(text) in index:
                index[_norm(text)]["on"] = on
            elif on and len(cur) < _MAX_PHRASES:
                p = {"text": text, "on": True, "user": True}
                cur.append(p)
                index[_norm(text)] = p
        prof["phrases"] = cur[:_MAX_PHRASES]
    if never_say is not None:
        out: list[str] = []
        for w in never_say:
            w = " ".join(str(w).split())[:60]
            if w and w.lower() not in {x.lower() for x in out}:
                out.append(w)
        prof["never_say"] = out[:20]
        prof["never_say_edited"] = True
    if correction is not None:
        prof = add_correction(prof, correction)
    prof["updated_at"] = _now()
    return prof


def merge_rebuild(old: dict | None, new: dict) -> dict:
    merged = copy.deepcopy(new)
    if not old:
        return merged
    if old.get("paragraph_edited") and old.get("paragraph"):
        merged["paragraph"], merged["paragraph_edited"] = old["paragraph"], True
    off = {_norm(p.get("text", "")) for p in old.get("phrases") or [] if not p.get("on", True)}
    phrases = [{**p, "on": False} if _norm(p.get("text", "")) in off else p for p in merged.get("phrases") or []]
    have = {_norm(p.get("text", "")) for p in phrases}
    for p in old.get("phrases") or []:
        if p.get("user") and _norm(p.get("text", "")) not in have:
            phrases.append(dict(p))
    merged["phrases"] = phrases[:_MAX_PHRASES]
    if old.get("never_say_edited"):
        merged["never_say"], merged["never_say_edited"] = list(old.get("never_say") or []), True
    merged["corrections"] = list(old.get("corrections") or [])[-_MAX_CORRECTIONS:]
    if old.get("negatives") and not merged.get("negatives"):
        merged["negatives"] = old["negatives"]
    return merged


# ------------------------------------------------------------------ views
_CTA_LINE = {"none": "They don't usually ask for anything at the end.",
             "soft": "When they ask for something at the end, it's casual.",
             "explicit": "They ask viewers to follow or comment outright."}


def prompt_block(profile: dict | None, *, max_chars: int = 1400) -> str:
    if not profile:
        return ""
    p = profile
    paragraph = str(p.get("paragraph") or "").strip()
    phrases = [x["text"] for x in p.get("phrases") or [] if x.get("on", True) and x.get("text")]
    start = p.get("openers_style") or ("; ".join(f'"{o}"' for o in (p.get("openers") or [])[:2]))
    end = " ".join(x for x in (p.get("closers_style") or "", _CTA_LINE.get(p.get("cta_habit") or "", "")) if x)
    never = [w for w in p.get("never_say") or [] if w]
    corr = [c for c in p.get("corrections") or [] if c]
    negs = [n for n in p.get("negatives") or [] if n.get("why")][:2]

    def render(paragraph: str, phrases: list[str], negs: list[dict]) -> str:
        lines = [_LOW_NOTE] if p.get("data_confidence") == "low" else []
        if paragraph:
            lines.append(paragraph)
        if phrases:
            lines.append("Things they really say: " + "; ".join(f'"{x}"' for x in phrases))
        if start:
            lines.append(f"How they start: {start}")
        if end:
            lines.append(f"How they end: {end}")
        if never:
            lines.append("They never say: " + ", ".join(never))
        if corr:
            lines.append("They told us:\n" + "\n".join(f"- {c}" for c in corr))
        if negs:
            lines.append("Drafts that missed their voice, and why:\n" + "\n".join(f"- {n['why']}" for n in negs))
        return "\n".join(lines)

    out = render(paragraph, phrases, negs)
    # Budget: drop negatives, then phrases from the tail, then paragraph sentences. Corrections stay.
    while len(out) > max_chars and negs:
        negs = negs[:-1]
        out = render(paragraph, phrases, negs)
    while len(out) > max_chars and len(phrases) > 3:
        phrases = phrases[:-1]
        out = render(paragraph, phrases, negs)
    sents = _sentences(paragraph)
    while len(out) > max_chars and len(sents) > 2:
        sents = sents[:-1]
        out = render(" ".join(sents), phrases, negs)
    return out


def lint_profile(profile: dict | None) -> dict:
    p = profile or {}
    addr = str(p.get("address") or "")
    return {"address_form": addr if addr not in ("", "you") else None,
            "exemplar_texts": [e["text"] for e in (p.get("exemplars") or [])[:8] if e.get("text")]}


def public_view(profile: dict | None) -> dict:
    if not profile:
        return {}
    p = profile
    return {"paragraph": p.get("paragraph", ""),
            "phrases": [{"text": x.get("text", ""), "on": bool(x.get("on", True))} for x in p.get("phrases") or []],
            "openers": list(p.get("openers") or []), "closers": list(p.get("closers") or []),
            "address": p.get("address", "you"), "swearing": p.get("swearing", "none"),
            "typical_words": int(p.get("typical_words") or 0), "never_say": list(p.get("never_say") or []),
            "corrections": list(p.get("corrections") or []), "confidence": p.get("data_confidence", "low"),
            "built_from": dict(p.get("built_from") or {}), "updated_at": p.get("updated_at", ""),
            "version": p.get("version", PROFILE_VERSION)}


# ------------------------------------------------------------------ learning from edits
_CORR_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["correction"],
                "properties": {"correction": {"type": "string"}}}
_CORR_SYSTEM = (
    "A creator edited one line of a script written for them to read on camera. In ONE short plain "
    "sentence, describe the STYLE change, quoting their words, e.g. \"Changed 'here's why' to "
    "'and like'.\" or \"Cut the question opener and started with the story.\" If the edit only "
    "changes facts/content or fixes a typo, return an empty string. JSON only.")


def _trivial_edit(before: str, after: str) -> bool:
    squash = lambda s: re.sub(r"[\W_]+", "", s.lower())  # noqa: E731
    return squash(before) == squash(after)


async def correction_from_edit(before: str, after: str) -> str | None:
    before, after = (before or "").strip()[:600], (after or "").strip()[:600]
    if not before or not after or before == after or _trivial_edit(before, after):
        return None
    out, _ = await llm.call(model=flags.helper_model(), system=_CORR_SYSTEM,
                            messages=[llm.text_turn("user", f"BEFORE:\n{before}\n\nAFTER:\n{after}")],
                            schema=_CORR_SCHEMA, max_tokens=200, route="voice.correction")
    text = " ".join(str((out or {}).get("correction") or "").split()) if isinstance(out, dict) else ""
    return text[:200] or None


# ------------------------------------------------------------------ optional: style negatives
_QUOTED = re.compile(r"[\"“‘]([^\"”’]{3,120})[\"”’]")  # straight ' would split on contractions


async def style_negatives(profile: dict, samples: list[dict]) -> list[dict]:
    """VOICE_NEGATIVES=1 only. Draft from the profile alone on a held-out sample's topic, then
    have the model say how the draft differs in STYLE, citing the real transcript."""
    if os.environ.get("VOICE_NEGATIVES") != "1":
        return []
    shown = {_norm(e.get("text", "")) for e in profile.get("exemplars") or []}
    held = [s for s in samples or [] if _norm(s["text"]) not in shown][:4]
    block = prompt_block({**profile, "negatives": []})

    async def one(s: dict) -> dict | None:
        topic = _cap_words(s["text"], 30)
        d, _ = await llm.call(
            model=flags.writer_model(), route="voice.negatives", max_tokens=500, effort="low",
            system="Write a ~60 word talking-head script in this creator's voice.\n\n" + block,
            messages=[llm.text_turn("user", f"Topic (do not copy its wording): {topic}")],
            schema={"type": "object", "additionalProperties": False, "required": ["draft"],
                    "properties": {"draft": {"type": "string"}}})
        draft = str((d or {}).get("draft") or "").strip() if isinstance(d, dict) else ""
        if not draft:
            return None
        w, _ = await llm.call(
            model=flags.writer_model(), route="voice.negatives", max_tokens=200, effort="low",
            system=("In ONE sentence, say how the DRAFT differs in STYLE from the REAL transcript, "
                    "quoting a phrase from the real one in double quotes. JSON only."),
            messages=[llm.text_turn("user", f"REAL:\n{s['text']}\n\nDRAFT:\n{draft}")],
            schema={"type": "object", "additionalProperties": False, "required": ["why"],
                    "properties": {"why": {"type": "string"}}})
        why = " ".join(str((w or {}).get("why") or "").split())[:300] if isinstance(w, dict) else ""
        real = " " + _norm(s["text"]) + " "
        if why and any(_verbatim(q, real) for q in _QUOTED.findall(why)):
            return {"draft": draft[:800], "why": why}
        return None

    results = await asyncio.gather(*(one(s) for s in held))
    return [r for r in results if r][:5]
