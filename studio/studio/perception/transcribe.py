"""ASR: a verbatim, word-ID'd transcript of the dialogue track (ARCHITECTURE §4).

What it produces
----------------
:func:`transcribe` returns an :class:`~studio.perception.index.AsrResult`: words ``w0001``… in time
order with ``kind`` = ``word`` | ``filler`` | ``event`` | ``cutoff``, ``confidence``, ``speaker``
(``S1`` = the dominant speaker) and, for Scribe, per-character timings (``Word.chars``).
``sentence_id`` / ``cluster_id`` / ``prosody`` stay unset (see :mod:`studio.perception.takes` and
:mod:`studio.perception.prosody`). The raw provider response is cached verbatim in
``index/asr_raw_<provider>.json`` with a sidecar ``index/asr_raw_<provider>.meta.json`` (request params,
audio SHA-256, timings) — a re-run with the same audio and params re-parses the cache instead of
calling the API, so every index is reproducible from the job directory.

Quality choices (the only goal is the final cut)
------------------------------------------------
* **ElevenLabs Scribe v2 is the default** (``POST https://api.elevenlabs.io/v1/speech-to-text``,
  ``model_id=scribe_v2``): best available verbatim ASR (disfluency F1 90.3, filler 95.5, cut-off 80.1,
  vocal sounds 83.4 on the Nyra verbatim benchmark; AA-WER 2.2). Fillers, stutters and false starts are
  the raw material of the cut, so ``no_verbatim=false`` is sent explicitly (``true`` would remove
  "filler words, false starts and non-speech sounds"). ``timestamps_granularity=character`` gives
  per-character times; a word's edges are taken from its **letter/digit characters** (punctuation
  characters are excluded so ``"months—"`` ends where the ``s`` ends). ``tag_audio_events`` keeps
  laughs/breaths/coughs as ``event`` words (they are cut candidates and must never be swallowed by a
  neighbouring word). ``diarize`` separates an off-camera voice from the creator. ``keyterms`` (≤1000,
  <50 chars, ≤5 words, no ``<>{}[]\\``) bias names/brands. A fixed ``seed`` keeps re-runs stable.
* **Upload = FLAC 24-bit at the dialogue track's native rate (48 kHz), mono.** Lossless, ~half the
  bytes of float WAV, no resampling. Stereo is averaged unless the channels are anti-phase (then the
  louder channel is used, since averaging would cancel the voice). Only a pure gain is applied (to avoid
  clipping float overs, or to lift a very quiet take above -30 dBFS peak) — gain never moves time.
* **AssemblyAI is the fallback** (``provider=None/"auto"`` when Scribe fails, or ``"assemblyai"``):
  ``speech_models=["universal-3-5-pro","universal-2"]``, ``disfluencies=true``, ``speaker_labels``, and
  the documented verbatim prompt ("Mandatory: Preserve linguistic speech patterns including
  disfluencies, filler words, …"). ``prompt`` and ``keyterms_prompt`` are mutually exclusive at the API,
  so key terms are appended to the prompt; if the API rejects the prompt (400/422) the request is retried
  without it (keyterms then go to ``keyterms_prompt``). AssemblyAI misses most cut-offs and all vocal
  sounds, which is why it is only the fallback.
* **Word kinds are decided by code, conservatively.** Single fillers: um/uh/er/erm/ah/hmm (+ spelling
  variants). ``like`` and the multi-word markers *you know*, *I mean*, *sort of*, *kind of* are fillers
  **only when isolated** — bounded on both sides by a sentence edge, comma/dash, a ≥300 ms pause or
  another filler ("it's, like, huge" / "it was, you know, fine") — never in "I like it", "what kind of
  car", "do you know him". A token ending in a hyphen (``restr-``) is a ``cutoff``; an em-dash marks an
  interruption of the *utterance* (``months—`` stays a ``word``) unless the stem is a 1–2 letter
  fragment (``th—``). Bracketed/parenthesised tokens are ``event``.
* Times are integer microseconds; overlapping spoken words are clipped to their successor's start and
  everything is clamped to the audio duration. Acoustic refinement of edges happens later in
  :mod:`studio.perception.gaps` (never by a model).

Keys come only from :class:`studio.config.Settings`; they are sent in headers and never logged, cached
or put in error messages (error bodies are passed through :func:`studio.config.redact`).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import re
import tempfile
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import numpy as np
import soundfile as sf

from studio.config import MissingKeyError, Settings, get_settings, redact
from studio.perception.index import AsrInfo, AsrResult, CharTime, Word, word_id

if TYPE_CHECKING:  # pragma: no cover
    from studio.jobs import Job

__all__ = [
    "ELEVENLABS_URL", "ELEVENLABS_MODEL", "ASSEMBLYAI_BASE", "ASSEMBLYAI_MODELS", "ASSEMBLYAI_VERBATIM_PROMPT",
    "PROVIDERS", "FILLER_WORDS", "MULTIWORD_FILLERS",
    "AsrError", "RawToken",
    "transcribe", "transcribe_file", "prepare_upload_audio", "sanitize_keyterms",
    "parse_elevenlabs", "parse_assemblyai", "build_words",
    "normalize_token", "is_cutoff_token", "ends_with_dash", "is_event_token",
]

# ---------------------------------------------------------------------------------------------- constants
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/speech-to-text"
ELEVENLABS_MODEL = "scribe_v2"
ELEVENLABS_SEED = 7

ASSEMBLYAI_BASE = "https://api.assemblyai.com"
ASSEMBLYAI_MODELS = ["universal-3-5-pro", "universal-2"]
ASSEMBLYAI_VERBATIM_PROMPT = (
    "Mandatory: Preserve linguistic speech patterns including disfluencies, filler words, hesitations, "
    "repetitions, stutters, false starts, and colloquialisms."
)
ASSEMBLYAI_POLL_S = 3.0
ASSEMBLYAI_MAX_WAIT_S = 3600.0

PROVIDERS = ("elevenlabs", "assemblyai")

#: Tokens that are always fillers (compared after :func:`normalize_token`).
FILLER_WORDS: frozenset[str] = frozenset({
    "um", "umm", "ummm", "uhm", "uhmm", "erm", "uh", "uhh", "uhhh", "er", "err", "ah", "ahh", "ahhh",
    "hmm", "hmmm", "hm", "mm", "mmm", "eh",
})
#: Discourse markers that are fillers only when isolated (both sides bounded).
MULTIWORD_FILLERS: tuple[tuple[str, ...], ...] = (("you", "know"), ("i", "mean"), ("sort", "of"), ("kind", "of"))
_ISOLATED_SINGLE_FILLERS = frozenset({"like"})

#: 1–2 letter real words (an em-dash after these is an interruption, not a partial word).
_SHORT_WORDS = frozenset({
    "a", "i", "an", "am", "as", "at", "be", "by", "do", "go", "he", "hi", "if", "in", "is", "it", "me", "my",
    "no", "of", "oh", "ok", "on", "or", "so", "to", "up", "us", "we", "ya", "yo", "ah", "uh", "um", "er",
})

_ISOLATION_PAUSE_US = 300_000
_TERMINAL_RE = re.compile(r"[.!?…。！？]+$")
_CLOSERS = "\"'”’)]}»"
_DASHES = ("—", "–", "--", "―")

_RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_KEYTERM_BAD = re.compile(r"[<>{}\[\]\\]")


class AsrError(RuntimeError):
    """ASR request/parse failure (message never contains key values)."""


# test seams (monkeypatched in unit tests)
_sleep: Callable[[float], None] = time.sleep


def _make_client(settings: Settings) -> httpx.Client:
    """HTTP client for ASR calls (long read timeout: Scribe is synchronous for the whole file)."""
    return httpx.Client(timeout=httpx.Timeout(connect=30.0, read=1800.0, write=600.0, pool=30.0),
                        follow_redirects=True)


# ---------------------------------------------------------------------------------------------- text helpers
def normalize_token(text: str) -> str:
    """Lower-cased token without edge punctuation (keeps inner apostrophes/hyphens): ``"Um,"`` → ``"um"``,
    ``"They’re"`` → ``"they're"``, ``"months—"`` → ``"months"``."""
    t = unicodedata.normalize("NFKC", text or "").lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"^[^\w']+|[^\w']+$", "", t)
    return t.strip("'_")


def _strip_closers(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").strip()
    while t and t[-1] in _CLOSERS:
        t = t[:-1]
    return t


def ends_with_dash(text: str) -> bool:
    """True when the token ends in an em/en dash or ``--`` (an interrupted utterance)."""
    t = _strip_closers(text)
    return t.endswith(_DASHES)


def _ends_with_hyphen(text: str) -> bool:
    t = _strip_closers(text)
    return t.endswith("-") and not t.endswith(_DASHES)


def is_cutoff_token(text: str) -> bool:
    """A partial word: ``"restr-"``, ``"wh-"``, ``"th—"`` (1–2 letter non-word stem before a dash)."""
    t = _strip_closers(text)
    if _ends_with_hyphen(t):
        stem = normalize_token(t[:-1])
        return bool(stem) and any(c.isalpha() for c in stem)
    for d in _DASHES:
        if t.endswith(d):
            stem = normalize_token(t[: -len(d)])
            return bool(stem) and stem.isalpha() and len(stem) <= 2 and stem not in _SHORT_WORDS
    return False


def is_event_token(text: str) -> bool:
    """``(laughter)`` / ``[laughs]`` style tokens."""
    t = (text or "").strip()
    return len(t) >= 3 and ((t[0] == "(" and t[-1] == ")") or (t[0] == "[" and t[-1] == "]")
                            or (t[0] == "<" and t[-1] == ">"))


def _ends_clause(text: str) -> bool:
    """Token ends with punctuation that isolates what follows (comma, dash, colon, terminal)."""
    t = _strip_closers(text)
    return bool(t) and (t[-1] in ",;:.!?…" or t.endswith(_DASHES) or t.endswith("-"))


def _ends_sentence(text: str) -> bool:
    return bool(_TERMINAL_RE.search(_strip_closers(text)))


# ---------------------------------------------------------------------------------------------- tokens → words
@dataclass
class RawToken:
    """Provider-neutral token before IDs/kinds are assigned."""

    text: str
    start_us: int
    end_us: int
    is_event: bool = False
    confidence: float = 1.0
    speaker: str | None = None
    chars: list[CharTime] | None = None
    order: int = 0  # provider order (stable tie-break)
    extra: dict[str, Any] = field(default_factory=dict)


def _classify_kinds(toks: list[RawToken]) -> list[str]:
    """Kinds for time-ordered tokens (see module docstring for the conservative filler rules)."""
    kinds = ["event" if (t.is_event or is_event_token(t.text)) else "word" for t in toks]
    spoken = [i for i, k in enumerate(kinds) if k != "event"]
    norms = {i: normalize_token(toks[i].text) for i in spoken}

    for i in spoken:
        if is_cutoff_token(toks[i].text):
            kinds[i] = "cutoff"
        elif norms[i] in FILLER_WORDS:
            kinds[i] = "filler"

    def left_isolated(p: int) -> bool:
        """Spoken position p starts after a boundary (sentence start, clause punctuation, pause, filler)."""
        if p == 0:
            return True
        prev, cur = spoken[p - 1], spoken[p]
        return (_ends_clause(toks[prev].text) or kinds[prev] in ("filler", "cutoff")
                or toks[cur].start_us - toks[prev].end_us >= _ISOLATION_PAUSE_US)

    def right_isolated(p: int) -> bool:
        cur = spoken[p]
        if _ends_clause(toks[cur].text) or p == len(spoken) - 1:
            return True
        nxt = spoken[p + 1]
        return kinds[nxt] == "filler" or toks[nxt].start_us - toks[cur].end_us >= _ISOLATION_PAUSE_US

    # multi-word discourse markers ("you know", "I mean", "sort of", "kind of")
    p = 0
    while p < len(spoken) - 1:
        i, j = spoken[p], spoken[p + 1]
        pair = (norms[i], norms[j])
        if (pair in MULTIWORD_FILLERS and kinds[i] == "word" and kinds[j] == "word"
                and not _ends_clause(toks[i].text) and left_isolated(p) and right_isolated(p + 1)):
            kinds[i] = kinds[j] = "filler"
            p += 2
            continue
        p += 1
    # "like" as a discourse filler: isolated on both sides only
    for p, i in enumerate(spoken):
        if kinds[i] == "word" and norms[i] in _ISOLATED_SINGLE_FILLERS and left_isolated(p) and right_isolated(p):
            # sentence-final "like." is a verb ("stuff I like.") unless a comma sets it off
            if _ends_sentence(toks[i].text) and p > 0 and not _ends_clause(toks[spoken[p - 1]].text):
                continue
            kinds[i] = "filler"
    return kinds


def build_words(tokens: Sequence[RawToken], *, duration_us: int | None = None) -> tuple[list[Word], dict[str, str]]:
    """Sort, clean, classify and ID provider tokens. Returns ``(words, speaker_map)`` where
    ``speaker_map`` maps raw provider speaker ids to ``S1``, ``S2``… (``S1`` = most speech time)."""
    toks: list[RawToken] = []
    for k, t in enumerate(tokens):
        text = (t.text or "").strip()
        if not text:
            continue
        s, e = max(0, int(t.start_us)), max(0, int(t.end_us))
        if e < s:
            s, e = e, s
        if duration_us is not None and duration_us > 0:
            s, e = min(s, duration_us), min(e, duration_us)
        toks.append(RawToken(text=text, start_us=s, end_us=e, is_event=t.is_event,
                             confidence=float(min(1.0, max(0.0, t.confidence))), speaker=t.speaker,
                             chars=t.chars, order=t.order if t.order else k))
    toks.sort(key=lambda t: (t.start_us, t.order))

    # clip spoken-word overlaps (events may overlap speech: laughter during a word)
    spoken_idx = [i for i, t in enumerate(toks) if not (t.is_event or is_event_token(t.text))]
    for a, b in pairwise(spoken_idx):
        if toks[a].end_us > toks[b].start_us:
            toks[a].end_us = max(toks[a].start_us, toks[b].start_us)

    kinds = _classify_kinds(toks)

    # speakers: S1 = most speech time, ties by first appearance
    talk: dict[str, int] = {}
    first: dict[str, int] = {}
    for i, t in enumerate(toks):
        if t.speaker is None:
            continue
        sp = str(t.speaker)
        first.setdefault(sp, i)
        if kinds[i] != "event":
            talk[sp] = talk.get(sp, 0) + (t.end_us - t.start_us)
        else:
            talk.setdefault(sp, 0)
    ranked = sorted(first, key=lambda sp: (-talk.get(sp, 0), first[sp]))
    smap = {sp: f"S{n}" for n, sp in enumerate(ranked, start=1)}

    words: list[Word] = []
    for n, (t, kind) in enumerate(zip(toks, kinds, strict=True), start=1):
        chars = None
        if t.chars:
            chars = [c.model_copy(update={"start_us": max(0, c.start_us), "end_us": max(c.start_us, c.end_us)})
                     for c in t.chars]
        words.append(Word(
            id=word_id(n), text=t.text, start_us=t.start_us, end_us=t.end_us, kind=kind,  # type: ignore[arg-type]
            confidence=round(t.confidence, 4), speaker=smap.get(str(t.speaker)) if t.speaker is not None else None,
            chars=chars,
        ))
    return words, smap


# ---------------------------------------------------------------------------------------------- parsers
def _s_to_us(x: Any) -> int | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return math.floor(v * 1_000_000 + 0.5)


def _logprob_to_conf(lp: Any) -> float:
    if lp is None:
        return 1.0
    try:
        v = float(lp)
    except (TypeError, ValueError):
        return 1.0
    if math.isnan(v):
        return 1.0
    return float(min(1.0, max(0.0, math.exp(min(0.0, v)))))


def _el_entries(raw: Mapping[str, Any]) -> list[tuple[int, Mapping[str, Any]]]:
    if isinstance(raw.get("transcripts"), list):  # multichannel response
        out = []
        for ch, tr in enumerate(raw["transcripts"]):
            ci = tr.get("channel_index", ch)
            out.extend((ci if ci is not None else ch, w) for w in tr.get("words") or [])
        return out
    return [(raw.get("channel_index") or 0, w) for w in raw.get("words") or []]


def parse_elevenlabs(raw: Mapping[str, Any], *, duration_us: int | None = None) -> tuple[list[Word], dict[str, Any]]:
    """Scribe response → words. Word edges come from letter/digit character timings when present."""
    tokens: list[RawToken] = []
    multi = isinstance(raw.get("transcripts"), list)
    for k, (channel, w) in enumerate(_el_entries(raw)):
        typ = w.get("type") or "word"
        if typ == "spacing":
            continue
        text = str(w.get("text") or "").strip()
        if not text:
            continue
        ws, we = _s_to_us(w.get("start")), _s_to_us(w.get("end"))
        chars: list[CharTime] = []
        for c in w.get("characters") or []:
            cs, ce = _s_to_us(c.get("start")), _s_to_us(c.get("end"))
            ctext = str(c.get("text") or "")
            if cs is None or ce is None or not ctext:
                continue
            chars.append(CharTime(text=ctext, start_us=max(0, cs), end_us=max(max(0, cs), ce)))
        core = [c for c in chars if any(ch.isalnum() for ch in c.text)]
        s, e = ws, we
        if core:
            cs, ce = min(c.start_us for c in core), max(c.end_us for c in core)
            # trust characters unless they disagree wildly with the word span (malformed payload)
            if ws is None or we is None or (abs(cs - ws) <= 500_000 and abs(ce - we) <= 500_000):
                s, e = cs, ce
        if s is None or e is None:
            if chars:
                s, e = chars[0].start_us, chars[-1].end_us
            else:
                continue
        speaker = w.get("speaker_id")
        if multi and speaker is None:
            speaker = f"ch{channel}"
        tokens.append(RawToken(text=text, start_us=s, end_us=e, is_event=typ == "audio_event",
                               confidence=_logprob_to_conf(w.get("logprob")), speaker=speaker,
                               chars=chars or None, order=k))
    words, smap = build_words(tokens, duration_us=duration_us)
    meta = {
        "language": raw.get("language_code"),
        "language_probability": raw.get("language_probability"),
        "speaker_map": smap,
        "transcription_id": raw.get("transcription_id"),
    }
    return words, meta


def parse_assemblyai(raw: Mapping[str, Any], *, duration_us: int | None = None) -> tuple[list[Word], dict[str, Any]]:
    """AssemblyAI transcript → words (times are milliseconds; no character timings)."""
    tokens: list[RawToken] = []
    for k, w in enumerate(raw.get("words") or []):
        text = str(w.get("text") or "").strip()
        if not text or w.get("start") is None or w.get("end") is None:
            continue
        conf = w.get("confidence")
        tokens.append(RawToken(
            text=text, start_us=round(float(w["start"]) * 1000), end_us=round(float(w["end"]) * 1000),
            is_event=is_event_token(text), confidence=1.0 if conf is None else float(conf),
            speaker=w.get("speaker"), order=k,
        ))
    words, smap = build_words(tokens, duration_us=duration_us)
    meta = {
        "language": raw.get("language_code"),
        "language_probability": raw.get("language_confidence"),
        "speaker_map": smap,
        "transcription_id": raw.get("id"),
        "speech_model_used": raw.get("speech_model_used") or raw.get("speech_model"),
    }
    return words, meta


# ---------------------------------------------------------------------------------------------- audio
def prepare_upload_audio(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> dict[str, Any]:
    """Write ``dst`` as mono 24-bit FLAC at the source rate (see module docstring). Returns stats."""
    src, dst = Path(src), Path(dst)
    info = sf.info(str(src))
    sr, ch, frames = int(info.samplerate), int(info.channels), int(info.frames)
    block = sr * 30
    peak = 0.0
    use_channel: int | None = None
    if ch > 1:
        # detect anti-phase stereo (averaging would cancel the voice)
        sxy = sxx = syy = 0.0
        for blk in sf.blocks(str(src), blocksize=block, dtype="float32", always_2d=True):
            x, y = blk[:, 0].astype(np.float64), blk[:, 1].astype(np.float64)
            sxy += float(np.dot(x, y))
            sxx += float(np.dot(x, x))
            syy += float(np.dot(y, y))
        corr = sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 1.0
        if corr < -0.3:
            use_channel = 0 if sxx >= syy else 1

    def mono(blk: np.ndarray) -> np.ndarray:
        if blk.shape[1] == 1:
            return blk[:, 0]
        if use_channel is not None:
            return blk[:, use_channel]
        return blk.mean(axis=1)

    for blk in sf.blocks(str(src), blocksize=block, dtype="float32", always_2d=True):
        m = mono(blk)
        if m.size:
            peak = max(peak, float(np.max(np.abs(m))))
    gain = 1.0
    if peak > 0.999:
        gain = 0.999 / peak
    elif 0.0 < peak < 10 ** (-30 / 20):
        gain = 10 ** (-3 / 20) / peak
    dst.parent.mkdir(parents=True, exist_ok=True)
    with sf.SoundFile(str(dst), "w", samplerate=sr, channels=1, format="FLAC", subtype="PCM_24") as out:
        for blk in sf.blocks(str(src), blocksize=block, dtype="float32", always_2d=True):
            m = mono(blk).astype(np.float64) * gain
            out.write(np.clip(m, -1.0, 1.0).astype(np.float32))
    return {"sample_rate": sr, "channels_in": ch, "frames": frames, "peak": round(peak, 6),
            "gain_db": round(20 * math.log10(gain), 3), "downmix": "mono" if ch == 1 else (
                f"channel{use_channel}" if use_channel is not None else "mean")}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize_keyterms(keyterms: Sequence[str] | None, *, limit: int = 1000) -> list[str]:
    """Scribe keyterm rules: <50 chars, ≤5 words, none of ``<>{}[]\\``, ≤1000, de-duplicated (case-insensitive)."""
    out: list[str] = []
    seen: set[str] = set()
    for t in keyterms or []:
        s = _KEYTERM_BAD.sub(" ", unicodedata.normalize("NFKC", str(t)))
        s = " ".join(s.split())
        if not s or len(s) >= 50 or len(s.split()) > 5:
            continue
        k = s.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------------------------- HTTP
def _safe_body(resp: httpx.Response, settings: Settings) -> str:
    try:
        txt = resp.text
    except Exception:  # pragma: no cover
        txt = "<unreadable body>"
    return str(redact(txt[:500], settings.secret_values()))


def _retry_after(resp: httpx.Response | None, attempt: int) -> float:
    base = 2.0 * (2 ** attempt)
    if resp is not None:
        ra = resp.headers.get("retry-after")
        if ra:
            try:
                return min(60.0, max(base, float(ra)))
            except ValueError:
                pass
    return min(60.0, base)


def _request(client: httpx.Client, method: str, url: str, *, settings: Settings, what: str,
             build: Callable[[], dict[str, Any]]) -> httpx.Response:
    """Send with retries on transient failures. ``build`` returns fresh request kwargs per attempt."""
    last_err = ""
    for attempt in range(_MAX_ATTEMPTS):
        resp: httpx.Response | None = None
        try:
            resp = client.request(method, url, **build())
        except httpx.TransportError as e:
            last_err = f"{what}: transport error {type(e).__name__}"
        else:
            if resp.status_code < 400:
                return resp
            last_err = f"{what}: HTTP {resp.status_code}: {_safe_body(resp, settings)}"
            if resp.status_code not in _RETRY_STATUS:
                raise AsrError(last_err)
        if attempt < _MAX_ATTEMPTS - 1:
            _sleep(_retry_after(resp, attempt))
    raise AsrError(last_err or f"{what}: failed")


def _json(resp: httpx.Response, what: str) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError as e:
        raise AsrError(f"{what}: response is not JSON") from e
    if not isinstance(data, dict):
        raise AsrError(f"{what}: unexpected JSON payload")
    return data


def _elevenlabs_params(keyterms: list[str], language: str | None, num_speakers: int | None) -> dict[str, Any]:
    p: dict[str, Any] = {
        "model_id": ELEVENLABS_MODEL,
        "timestamps_granularity": "character",
        "tag_audio_events": "true",
        "diarize": "true",
        "no_verbatim": "false",
        "seed": str(ELEVENLABS_SEED),
    }
    if language:
        p["language_code"] = language
    if num_speakers:
        p["num_speakers"] = str(int(num_speakers))
    if keyterms:
        p["keyterms"] = list(keyterms)
    return p


def _call_elevenlabs(client: httpx.Client, key: str, audio: Path, params: dict[str, Any],
                     settings: Settings) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        return {
            "headers": {"xi-api-key": key, "accept": "application/json"},
            "data": params,
            "files": {"file": ("audio.flac", audio.read_bytes(), "audio/flac")},
        }

    resp = _request(client, "POST", ELEVENLABS_URL, settings=settings, what="elevenlabs speech-to-text",
                    build=build)
    data = _json(resp, "elevenlabs speech-to-text")
    if "words" not in data and "transcripts" not in data:
        raise AsrError("elevenlabs speech-to-text: response has no words")
    return data


def _assemblyai_bodies(keyterms: list[str], language: str | None, num_speakers: int | None) -> list[dict[str, Any]]:
    """Request variants, best first (the next is tried only if the API rejects a parameter)."""
    base: dict[str, Any] = {
        "speech_models": list(ASSEMBLYAI_MODELS),
        "disfluencies": True,
        "speaker_labels": True,
        "punctuate": True,
        "format_text": True,
    }
    if language:
        base["language_code"] = language
        base["language_detection"] = False
    else:
        base["language_detection"] = True
    if num_speakers:
        base["speakers_expected"] = int(num_speakers)
    prompt = ASSEMBLYAI_VERBATIM_PROMPT
    if keyterms:
        prompt += " Key terms that may be spoken: " + ", ".join(keyterms[:200]) + "."
    first = {**base, "prompt": prompt}
    second = dict(base)
    if keyterms:
        second["keyterms_prompt"] = list(keyterms[:1000])
    third = {k: v for k, v in base.items() if k not in ("speakers_expected",)}
    out: list[dict[str, Any]] = []
    for b in (first, second, third):
        if b not in out:
            out.append(b)
    return out


def _call_assemblyai(client: httpx.Client, key: str, audio: Path, bodies: list[dict[str, Any]],
                     settings: Settings) -> tuple[dict[str, Any], dict[str, Any]]:
    headers = {"authorization": key}
    up = _request(client, "POST", f"{ASSEMBLYAI_BASE}/v2/upload", settings=settings, what="assemblyai upload",
                  build=lambda: {"headers": {**headers, "content-type": "application/octet-stream"},
                                 "content": audio.read_bytes()})
    upload_url = _json(up, "assemblyai upload").get("upload_url")
    if not upload_url:
        raise AsrError("assemblyai upload: no upload_url in response")

    job: dict[str, Any] | None = None
    used: dict[str, Any] = {}
    errors: list[str] = []
    for body in bodies:
        payload = {"audio_url": upload_url, **body}
        try:
            resp = _request(client, "POST", f"{ASSEMBLYAI_BASE}/v2/transcript", settings=settings,
                            what="assemblyai submit", build=lambda p=payload: {"headers": headers, "json": p})
        except AsrError as e:
            msg = str(e)
            errors.append(msg)
            if " HTTP 400" in msg or " HTTP 422" in msg:
                continue  # a parameter was rejected: try the next, simpler variant
            raise
        job, used = _json(resp, "assemblyai submit"), body
        break
    if job is None:
        raise AsrError("; ".join(errors) or "assemblyai submit failed")
    tid = job.get("id")
    if not tid:
        raise AsrError("assemblyai submit: no transcript id")

    waited = 0.0
    while True:
        status = job.get("status")
        if status == "completed":
            return job, used
        if status == "error":
            err = redact(str(job.get("error"))[:300], settings.secret_values())
            raise AsrError(f"assemblyai transcript error: {err}")
        if waited >= ASSEMBLYAI_MAX_WAIT_S:
            raise AsrError(f"assemblyai transcript {tid}: timed out after {waited:.0f}s (status {status})")
        _sleep(ASSEMBLYAI_POLL_S)
        waited += ASSEMBLYAI_POLL_S
        resp = _request(client, "GET", f"{ASSEMBLYAI_BASE}/v2/transcript/{tid}", settings=settings,
                        what="assemblyai poll", build=lambda: {"headers": headers})
        job = _json(resp, "assemblyai poll")


# ---------------------------------------------------------------------------------------------- orchestration
def _provider_order(provider: str | None, settings: Settings) -> tuple[list[str], bool]:
    """Providers to try, and whether falling through to the next is allowed."""
    p = (provider or "auto").strip().lower()
    if p in ("auto", "default"):
        return list(PROVIDERS), True
    if p in ("elevenlabs", "scribe", "scribe_v2", "eleven"):
        return ["elevenlabs"], False
    if p in ("assemblyai", "assembly", "aai"):
        return ["assemblyai"], False
    raise ValueError(f"unknown ASR provider {provider!r} (use 'auto', 'elevenlabs' or 'assemblyai')")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_json(p: Path) -> Any:
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(p: Path, obj: Any) -> None:
    from studio.jobs import write_json_atomic

    write_json_atomic(p, obj)


def _run_provider(name: str, *, audio_path: Path, audio_sha: str, cache_dir: Path | None, keyterms: list[str],
                  language: str | None, num_speakers: int | None, settings: Settings, refresh: bool,
                  duration_us: int | None, flac_holder: dict[str, Any]) -> tuple[list[Word], AsrInfo, dict[str, Any]]:
    """Run (or re-parse the cache of) one provider. Returns words, info and a trace dict."""
    if name == "elevenlabs":
        params: dict[str, Any] = _elevenlabs_params(keyterms, language, num_speakers)
        model = ELEVENLABS_MODEL
    else:
        bodies = _assemblyai_bodies(keyterms, language, num_speakers)
        params = {"variants": bodies}
        model = ASSEMBLYAI_MODELS[0]
    raw_path = cache_dir / f"asr_raw_{name}.json" if cache_dir is not None else None
    meta_path = cache_dir / f"asr_raw_{name}.meta.json" if cache_dir is not None else None

    raw: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    cached = False
    if raw_path is not None and meta_path is not None and not refresh and raw_path.exists() and meta_path.exists():
        try:
            m = _read_json(meta_path)
            if m.get("audio_sha256") == audio_sha and m.get("params") == json.loads(json.dumps(params)):
                raw, meta, cached = _read_json(raw_path), m, True
        except (OSError, ValueError):
            raw = None
    latency_ms = 0
    if raw is None:
        key = settings.require_key(name)
        if "flac" not in flac_holder:
            tmpdir = cache_dir if cache_dir is not None else Path(tempfile.gettempdir())
            tmpdir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".asr_upload_", suffix=".flac", dir=tmpdir)
            os.close(fd)
            flac_holder["stats"] = prepare_upload_audio(audio_path, tmp)
            flac_holder["flac"] = Path(tmp)
        flac: Path = flac_holder["flac"]
        t0 = time.monotonic()
        with _make_client(settings) as client:
            if name == "elevenlabs":
                raw = _call_elevenlabs(client, key, flac, params, settings)
                used: dict[str, Any] = params
            else:
                raw, used = _call_assemblyai(client, key, flac, params["variants"], settings)
        latency_ms = int((time.monotonic() - t0) * 1000)
        meta = {
            "provider": name, "model": model, "params": params, "request_used": used,
            "audio_sha256": audio_sha, "upload": flac_holder.get("stats"), "created_at": _now_iso(),
            "latency_ms": latency_ms,
        }
        if raw_path is not None and meta_path is not None:
            _write_json(raw_path, raw)
            _write_json(meta_path, meta)
    assert raw is not None and meta is not None

    try:
        if name == "elevenlabs":
            words, pmeta = parse_elevenlabs(raw, duration_us=duration_us)
        else:
            words, pmeta = parse_assemblyai(raw, duration_us=duration_us)
    except (KeyError, TypeError, ValueError) as e:
        raise AsrError(f"{name}: could not parse response ({type(e).__name__}: {str(e)[:200]})") from e
    if name == "assemblyai" and pmeta.get("speech_model_used"):
        model = str(pmeta["speech_model_used"])
    info_params: dict[str, Any] = {
        "request": meta.get("request_used", params),
        "keyterms_count": len(keyterms),
        "speaker_map": pmeta.get("speaker_map", {}),
        "language_probability": pmeta.get("language_probability"),
        "transcription_id": pmeta.get("transcription_id"),
        "audio_sha256": audio_sha,
        "cached": cached,
    }
    info = AsrInfo(provider=name, model=model, language=pmeta.get("language"), params=info_params,
                   raw_path=str(raw_path) if raw_path is not None else None)
    trace = {"provider": name, "model": model, "cached": cached, "latency_ms": latency_ms, "words": len(words)}
    return words, info, trace


def transcribe_file(
    audio_path: str | os.PathLike[str],
    *,
    provider: str | None = None,
    keyterms: Sequence[str] | None = None,
    settings: Settings | None = None,
    language: str | None = None,
    num_speakers: int | None = None,
    cache_dir: str | os.PathLike[str] | None = None,
    refresh: bool = False,
    duration_us: int | None = None,
    on_event: Callable[..., Any] | None = None,
) -> AsrResult:
    """Transcribe any audio file (the job-level :func:`transcribe` and the QA round-trip use this).

    ``provider``: ``None``/``"auto"`` (Scribe v2, then AssemblyAI on failure), ``"elevenlabs"`` or
    ``"assemblyai"`` (no fallback). Raw responses are cached in ``cache_dir`` when given.
    """
    settings = settings or get_settings()
    audio = Path(audio_path)
    if not audio.is_file():
        raise AsrError(f"audio file not found: {audio}")
    if duration_us is None:
        try:
            inf = sf.info(str(audio))
            duration_us = round(inf.frames * 1_000_000 / inf.samplerate) if inf.samplerate else None
        except RuntimeError:
            duration_us = None
    order, fallthrough = _provider_order(provider, settings)
    terms = sanitize_keyterms(keyterms)
    cdir = Path(cache_dir) if cache_dir is not None else None
    audio_sha = _sha256(audio)
    flac_holder: dict[str, Any] = {}
    failures: list[dict[str, str]] = []
    try:
        for name in order:
            try:
                words, info, trace = _run_provider(
                    name, audio_path=audio, audio_sha=audio_sha, cache_dir=cdir, keyterms=terms, language=language,
                    num_speakers=num_speakers, settings=settings, refresh=refresh, duration_us=duration_us,
                    flac_holder=flac_holder)
            except (AsrError, MissingKeyError, httpx.HTTPError) as e:
                msg = str(redact(str(e), settings.secret_values()))[:600]
                failures.append({"provider": name, "error": msg})
                if on_event:
                    on_event("asr", provider=name, status="failed", error=msg)
                if not fallthrough:
                    if isinstance(e, MissingKeyError):
                        raise
                    raise AsrError(msg) from e
                continue
            if failures:
                info.params["fallback_from"] = failures
            if on_event:
                on_event("asr", status="ok", **trace)
            return AsrResult(words=words, asr=info, language=info.language)
    finally:
        f = flac_holder.get("flac")
        if f is not None:
            Path(f).unlink(missing_ok=True)
    raise AsrError("all ASR providers failed: " + "; ".join(f"{f['provider']}: {f['error']}" for f in failures))


def transcribe(
    job: Job,
    *,
    provider: str | None = None,
    keyterms: Sequence[str] | None = None,
    settings: Settings | None = None,
    language: str | None = None,
    num_speakers: int | None = None,
    refresh: bool = False,
) -> AsrResult:
    """Transcribe ``job.audio_path`` (``media/audio.wav``) into ``w0001``… words.

    ``provider``: ``None``/``"auto"`` (default: Scribe v2, AssemblyAI on failure) | ``"elevenlabs"`` |
    ``"assemblyai"``. ``keyterms`` default to ``job.json`` ``meta.keyterms`` and ``language`` to
    ``meta.language`` when present. Raw responses: ``index/asr_raw_<provider>.json`` (reused when the
    audio hash and request params match, unless ``refresh``). A job whose media has no audio stream
    yields an empty transcript.
    """
    from studio.jobs import JobError

    settings = settings or get_settings()
    media = None
    try:
        media = job.load_media_info()
    except JobError:
        media = None
    audio = job.audio_path
    if not audio.is_file():
        if media is not None and not media.has_audio:
            return AsrResult(words=[], asr=AsrInfo(provider="none", model="none", params={"reason": "no audio"}))
        raise AsrError(f"job {job.id} has no media/audio.wav (run ingest first)")
    jmeta = (job.meta or {}).get("meta", {}) if isinstance(job.meta, dict) else {}
    if keyterms is None and isinstance(jmeta.get("keyterms"), list):
        keyterms = [str(k) for k in jmeta["keyterms"]]
    if language is None and isinstance(jmeta.get("language"), str):
        language = jmeta["language"]

    def on_event(event: str, **fields: Any) -> None:
        job.trace(event, stage="index", step="transcribe", **fields)

    # times are clamped to the dialogue track's own length (the clock the ASR saw)
    res = transcribe_file(
        audio, provider=provider, keyterms=keyterms, settings=settings, language=language,
        num_speakers=num_speakers, cache_dir=job.index_dir, refresh=refresh, on_event=on_event,
    )
    if res.asr.raw_path:
        try:
            rel = Path(res.asr.raw_path).resolve().relative_to(job.root)
            res = res.model_copy(update={"asr": res.asr.model_copy(update={"raw_path": rel.as_posix()})})
        except ValueError:
            pass
    return res
