"""ASR (studio.perception.transcribe): parsing, word kinds, request formats, caching, fallback.

Keyless tests mock HTTP with ``httpx.MockTransport`` (no network). ``@pytest.mark.real`` tests call
ElevenLabs Scribe v2 / AssemblyAI and need ``STUDIO_REAL=1 STUDIO_ENV_FILE=…``.
"""

from __future__ import annotations

import email
import json
import math
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
import soundfile as sf

from studio.config import MissingKeyError, Settings, get_settings
from studio.jobs import Job
from studio.perception import transcribe as tr
from studio.perception.index import TakeIndex
from studio.perception.takes import analyze_takes
from studio.perception.transcribe import (
    ASSEMBLYAI_VERBATIM_PROMPT,
    AsrError,
    RawToken,
    build_words,
    is_cutoff_token,
    is_event_token,
    normalize_token,
    parse_assemblyai,
    parse_elevenlabs,
    prepare_upload_audio,
    sanitize_keyterms,
    transcribe,
)

HERE = Path(__file__).parent
SCRIBE_D030 = HERE / "transcribe_scribe_v2_d030.json"
FAKE_EL_KEY = "el-test-key-0123456789abcdef"
FAKE_AAI_KEY = "aai-test-key-0123456789abcdef"


# ---------------------------------------------------------------------------------------------- helpers
def toks(script: str, *, gap_ms: int = 60) -> list[RawToken]:
    """Tokens from a script; ``[400]`` = pause before the next token."""
    out: list[RawToken] = []
    t = 100_000
    gap = gap_ms
    for tok in script.split():
        if tok.startswith("[") and tok.endswith("]") and tok[1:-1].isdigit():
            gap = int(tok[1:-1])
            continue
        if out:
            t += gap * 1000
        gap = gap_ms
        d = (100 + 30 * len(tok)) * 1000
        out.append(RawToken(text=tok, start_us=t, end_us=t + d, order=len(out)))
        t += d
    return out


def kinds(script: str) -> list[tuple[str, str]]:
    words, _ = build_words(toks(script))
    return [(w.text, w.kind) for w in words]


def el_word(text: str, start: float, end: float, *, typ: str = "word", speaker: str | None = "speaker_0",
            logprob: float | None = -0.01, chars: list[tuple[str, float | None, float | None]] | None = None) -> dict:
    w: dict[str, Any] = {"text": text, "start": start, "end": end, "type": typ, "speaker_id": speaker,
                         "logprob": logprob}
    if chars is not None:
        w["characters"] = [{"text": c, "start": s, "end": e} for c, s, e in chars]
    return w


def el_response(words: list[dict], **extra: Any) -> dict:
    return {"language_code": "eng", "language_probability": 0.99, "text": " ".join(w["text"] for w in words),
            "words": words, "transcription_id": "tx123", **extra}


SIMPLE_EL = el_response([
    el_word("Um,", 0.10, 0.40, chars=[("U", 0.10, 0.20), ("m", 0.20, 0.38), (",", 0.40, 0.40)]),
    el_word(" ", 0.40, 0.50, typ="spacing"),
    el_word("the", 0.50, 0.62),
    el_word(" ", 0.62, 0.64, typ="spacing"),
    el_word("real", 0.64, 0.90),
    el_word("(laughs)", 0.95, 1.30, typ="audio_event"),
    el_word("secret.", 1.30, 1.80, logprob=-0.6931),
])


def wav(path: Path, data: np.ndarray, sr: int = 48000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), data.astype(np.float32), sr, subtype="FLOAT")
    return path


def tone(seconds: float = 1.0, sr: int = 48000, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return amp * np.sin(2 * math.pi * 220 * t)


@pytest.fixture
def asr_job(work_dir: Path, media_info) -> Job:
    job = Job.create("asr-job", work_dir=work_dir)
    job.save_media_info(media_info)
    wav(job.audio_path, tone(1.5))
    return job


@pytest.fixture
def keyed(settings: Settings) -> Settings:
    return settings.with_keys(elevenlabs=FAKE_EL_KEY, assemblyai=FAKE_AAI_KEY)


class Recorder:
    """A MockTransport handler that records requests and answers from a route function."""

    def __init__(self, route: Callable[[httpx.Request, int], httpx.Response]):
        self.route = route
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        return self.route(request, len(self.requests))

    def urls(self) -> list[str]:
        return [f"{r.method} {r.url.copy_with(query=None)}" for r in self.requests]


@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch):
    """Install a recorder: ``rec = mock_http(route)``. Sleeps are skipped."""
    monkeypatch.setattr(tr, "_sleep", lambda s: None)

    def install(route: Callable[[httpx.Request, int], httpx.Response]) -> Recorder:
        rec = Recorder(route)
        monkeypatch.setattr(tr, "_make_client", lambda settings: httpx.Client(transport=httpx.MockTransport(rec)))
        return rec

    return install


def multipart_fields(req: httpx.Request) -> tuple[dict[str, list[str]], dict[str, tuple[str, str, bytes]]]:
    """Parse a multipart request → (form fields as lists, files {name: (filename, content_type, bytes)})."""
    raw = b"Content-Type: " + req.headers["content-type"].encode() + b"\r\n\r\n" + req.content
    msg = email.message_from_bytes(raw)
    fields: dict[str, list[str]] = {}
    files: dict[str, tuple[str, str, bytes]] = {}
    for part in msg.get_payload():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_param("filename", header="content-disposition")
        payload = part.get_payload(decode=True)
        if filename:
            files[name] = (filename, part.get_content_type(), payload)
        else:
            fields.setdefault(name, []).append(payload.decode())
    return fields, files


def el_ok(body: dict = SIMPLE_EL) -> Callable[[httpx.Request, int], httpx.Response]:
    def route(req: httpx.Request, n: int) -> httpx.Response:
        assert req.url.host == "api.elevenlabs.io"
        return httpx.Response(200, json=body)
    return route


AAI_DONE = {
    "id": "t-1", "status": "completed", "language_code": "en", "speech_model_used": "universal-3-5-pro",
    "words": [
        {"text": "Um,", "start": 100, "end": 400, "confidence": 0.8, "speaker": "A"},
        {"text": "hello", "start": 500, "end": 800, "confidence": 0.99, "speaker": "A"},
        {"text": "[laughter]", "start": 850, "end": 1100, "confidence": 0.5, "speaker": "A"},
        {"text": "there.", "start": 1100, "end": 1400, "confidence": 0.97, "speaker": "A"},
    ],
}


def aai_route(*, reject_prompt: bool = False, fail_status: bool = False):
    polls = {"n": 0}

    def route(req: httpx.Request, n: int) -> httpx.Response:
        host, path = req.url.host, req.url.path
        if host == "api.elevenlabs.io":
            return httpx.Response(503, json={"detail": "overloaded"})
        assert host == "api.assemblyai.com"
        if path == "/v2/upload":
            return httpx.Response(200, json={"upload_url": "https://cdn.assemblyai.com/upload/abc"})
        if path == "/v2/transcript" and req.method == "POST":
            body = json.loads(req.content)
            if reject_prompt and "prompt" in body:
                return httpx.Response(400, json={"error": "prompt is not supported with this configuration"})
            return httpx.Response(200, json={"id": "t-1", "status": "queued"})
        if path == "/v2/transcript/t-1":
            polls["n"] += 1
            if polls["n"] < 2:
                return httpx.Response(200, json={"id": "t-1", "status": "processing"})
            if fail_status:
                return httpx.Response(200, json={"id": "t-1", "status": "error", "error": "audio too short"})
            return httpx.Response(200, json=AAI_DONE)
        return httpx.Response(404)

    return route


# ---------------------------------------------------------------------------------------------- text helpers
def test_text_helpers():
    assert normalize_token("Um,") == "um"
    assert normalize_token("They’re") == "they're"
    assert normalize_token("months—") == "months"
    assert normalize_token("“Hello!”") == "hello"
    assert is_cutoff_token("restr-") and is_cutoff_token("wh-") and is_cutoff_token("th—")
    assert not is_cutoff_token("months—") and not is_cutoff_token("I—") and not is_cutoff_token("well-known")
    assert not is_cutoff_token("-") and not is_cutoff_token("2-")
    assert is_event_token("(laughter)") and is_event_token("[music]") and not is_event_token("(a")


@pytest.mark.parametrize("script,expected", [
    ("Um, so uh the er thing hmm is ah done", {"Um,": "filler", "uh": "filler", "er": "filler", "hmm": "filler",
                                             "ah": "filler", "so": "word", "is": "word"}),
    ("It's, like, huge.", {"like,": "filler"}),
    ("I like it a lot.", {"like": "word"}),
    ("Stuff I like.", {"like.": "word"}),
    ("things like, uh, cars", {"like,": "word", "uh,": "filler"}),
    ("Like, what are you doing?", {"Like,": "filler"}),
    ("It was, you know, fine.", {"you": "filler", "know,": "filler"}),
    ("Do you know him?", {"you": "word", "know": "word"}),
    ("You know, the thing is", {"You": "filler", "know,": "filler"}),
    ("I mean, it works.", {"I": "filler", "mean,": "filler"}),
    ("That's what I mean.", {"I": "word", "mean.": "word"}),
    ("What kind of car is that?", {"kind": "word", "of": "word"}),
    ("It was kind of weird.", {"kind": "word", "of": "word"}),
    ("It's, kind of, weird.", {"kind": "filler", "of,": "filler"}),
    ("A sort of plan.", {"sort": "word"}),
    ("oh wow", {"oh": "word"}),
    ("restr- restraint", {"restr-": "cutoff", "restraint": "word"}),
    ("months— people", {"months—": "word"}),
    ("It was [400] like [400] amazing", {"like": "filler"}),
    ("(laughs) okay", {"(laughs)": "event"}),
])
def test_word_kinds(script: str, expected: dict[str, str]):
    got = dict(kinds(script))
    for text, kind in expected.items():
        assert got[text] == kind, (text, got)


def test_build_words_orders_clips_and_maps_speakers():
    tokens = [
        RawToken("b", 1_000_000, 1_400_000, speaker="spk_x", order=1),
        RawToken("a", 200_000, 1_200_000, speaker="spk_y", order=0),  # overlaps "b"
        RawToken("(cough)", 1_100_000, 1_300_000, is_event=True, speaker="spk_y", order=2),
        RawToken("c", 1_500_000, 2_000_000, speaker="spk_x", confidence=1.7, order=3),
        RawToken("  ", 1_600_000, 1_700_000, order=4),  # empty text dropped
        RawToken("d", 2_600_000, 2_300_000, speaker=None, order=5),  # reversed times fixed
        RawToken("e", 2_800_000, 9_000_000, speaker="spk_x", order=6),  # clamped to the duration
    ]
    words, smap = build_words(tokens, duration_us=3_000_000)
    assert [w.id for w in words] == ["w0001", "w0002", "w0003", "w0004", "w0005", "w0006"]
    assert [w.text for w in words] == ["a", "b", "(cough)", "c", "d", "e"]
    a, b, ev, c, d, e = words
    assert a.end_us == b.start_us == 1_000_000  # spoken overlap clipped
    assert ev.kind == "event" and ev.start_us == 1_100_000  # events may overlap speech
    assert c.confidence == 1.0
    assert (d.start_us, d.end_us) == (2_300_000, 2_600_000) and d.speaker is None
    assert e.end_us == 3_000_000
    # spk_x talks 0.4 + 0.5 + 0.2 s > spk_y 0.8 s → S1
    assert smap == {"spk_x": "S1", "spk_y": "S2"}
    assert b.speaker == "S1" and a.speaker == "S2"


# ---------------------------------------------------------------------------------------------- parsers
def test_parse_real_scribe_v2_response():
    raw = json.loads(SCRIBE_D030.read_text())
    words, meta = parse_elevenlabs(raw)
    assert len(words) == 98
    assert [w.id for w in words[:3]] == ["w0001", "w0002", "w0003"]
    assert words[0].text == "Part" and (words[0].start_us, words[0].end_us) == (40_000, 260_000)
    assert all(w.chars for w in words) and all(w.speaker == "S1" for w in words)
    assert all(w.kind == "word" for w in words)  # a clean TTS read: nothing to flag
    assert meta["language"] == "eng" and meta["speaker_map"] == {"speaker_0": "S1"}
    starts = [w.start_us for w in words]
    assert starts == sorted(starts)
    # "one." ends at its last letter (the "." character is excluded from the word edge)
    one = words[1]
    assert one.text == "one." and one.end_us == 640_000
    assert 0.5 < one.confidence < 1.0  # logprob -0.216 → ~0.81


def test_parse_elevenlabs_edges_events_confidence():
    raw = el_response([
        el_word("Hello,", 1.00, 1.60, logprob=-0.6931,
                chars=[("H", 1.10, 1.20), ("e", 1.20, 1.30), ("l", 1.30, 1.35), ("l", 1.35, 1.40),
                       ("o", 1.40, 1.50), (",", 1.50, 1.60)]),
        el_word(" ", 1.60, 1.70, typ="spacing"),
        el_word("(laughter)", 1.70, 2.20, typ="audio_event", logprob=None),
        el_word("world", 2.20, 2.60, speaker="speaker_1", chars=[("w", 9.0, 9.1), ("d", 9.2, 9.3)]),  # malformed
        el_word("again", 2.70, 3.00, chars=[("a", None, None), ("g", 2.75, 2.80), ("n", 2.90, 2.95)]),
        el_word("", 3.0, 3.1),
    ])
    words, meta = parse_elevenlabs(raw)
    assert [w.text for w in words] == ["Hello,", "(laughter)", "world", "again"]
    h, ev, wd, ag = words
    assert (h.start_us, h.end_us) == (1_100_000, 1_500_000)  # letters only
    assert abs(h.confidence - 0.5) < 1e-3
    assert ev.kind == "event" and ev.confidence == 1.0
    assert (wd.start_us, wd.end_us) == (2_200_000, 2_600_000)  # chars disagree wildly → word times
    assert (ag.start_us, ag.end_us) == (2_750_000, 2_950_000)
    assert [c.text for c in ag.chars] == ["g", "n"]  # untimed characters dropped
    assert wd.speaker == "S2" and h.speaker == "S1"


def test_parse_elevenlabs_multichannel():
    raw = {"transcripts": [
        {"channel_index": 0, "words": [el_word("left", 0.1, 0.3, speaker=None)]},
        {"channel_index": 1, "words": [el_word("right", 0.2, 0.4, speaker=None)]},
    ]}
    words, _ = parse_elevenlabs(raw)
    assert [w.text for w in words] == ["left", "right"]
    assert {w.speaker for w in words} == {"S1", "S2"}


def test_parse_assemblyai():
    words, meta = parse_assemblyai(AAI_DONE)
    assert [(w.text, w.kind) for w in words] == [("Um,", "filler"), ("hello", "word"), ("[laughter]", "event"),
                                                 ("there.", "word")]
    assert (words[1].start_us, words[1].end_us) == (500_000, 800_000)
    assert words[0].confidence == 0.8 and words[0].speaker == "S1" and words[0].chars is None
    assert meta["speech_model_used"] == "universal-3-5-pro" and meta["language"] == "en"


# ---------------------------------------------------------------------------------------------- audio
def test_prepare_upload_audio_flac_mono(tmp_path: Path):
    stereo = np.stack([tone(1.0, amp=1.4), tone(1.0, amp=1.2)], axis=1)  # float overs
    src = wav(tmp_path / "in.wav", stereo)
    stats = prepare_upload_audio(src, tmp_path / "out.flac")
    assert (tmp_path / "out.flac").read_bytes()[:4] == b"fLaC"
    info = sf.info(str(tmp_path / "out.flac"))
    assert info.channels == 1 and info.samplerate == 48000 and info.subtype == "PCM_24"
    assert info.frames == 48000
    data, _ = sf.read(str(tmp_path / "out.flac"))
    assert np.max(np.abs(data)) <= 1.0 and stats["gain_db"] < 0 and stats["downmix"] == "mean"


def test_prepare_upload_audio_antiphase_and_quiet(tmp_path: Path):
    x = tone(1.0, amp=0.5)
    src = wav(tmp_path / "anti.wav", np.stack([x, -0.9 * x], axis=1))
    stats = prepare_upload_audio(src, tmp_path / "anti.flac")
    data, _ = sf.read(str(tmp_path / "anti.flac"))
    assert stats["downmix"] == "channel0" and np.max(np.abs(data)) > 0.4  # not cancelled
    quiet = wav(tmp_path / "quiet.wav", tone(1.0, amp=0.005))
    stats = prepare_upload_audio(quiet, tmp_path / "quiet.flac")
    data, _ = sf.read(str(tmp_path / "quiet.flac"))
    assert stats["gain_db"] > 30 and 0.6 < np.max(np.abs(data)) <= 1.0


def test_sanitize_keyterms():
    terms = ["Yunicorn", "yunicorn", "  Marque  AI ", "x" * 60, "one two three four five six", "a<b>c",
             "", "Post for Me"]
    assert sanitize_keyterms(terms) == ["Yunicorn", "Marque AI", "a b c", "Post for Me"]
    assert len(sanitize_keyterms([f"t{i}" for i in range(1500)])) == 1000


# ---------------------------------------------------------------------------------------------- ElevenLabs flow
def test_transcribe_elevenlabs_request_format_and_cache(asr_job: Job, keyed: Settings, mock_http):
    rec = mock_http(el_ok())
    res = transcribe(asr_job, keyterms=["Yunicorn", "Marque AI"], settings=keyed)

    assert rec.urls() == ["POST https://api.elevenlabs.io/v1/speech-to-text"]
    req = rec.requests[0]
    assert req.headers["xi-api-key"] == FAKE_EL_KEY
    fields, files = multipart_fields(req)
    assert fields["model_id"] == ["scribe_v2"]
    assert fields["timestamps_granularity"] == ["character"]
    assert fields["no_verbatim"] == ["false"]
    assert fields["tag_audio_events"] == ["true"] and fields["diarize"] == ["true"]
    assert fields["keyterms"] == ["Yunicorn", "Marque AI"]  # repeated form field
    assert "language_code" not in fields  # auto-detect
    fname, ctype, content = files["file"]
    assert fname == "audio.flac" and ctype == "audio/flac" and content[:4] == b"fLaC"

    assert res.asr.provider == "elevenlabs" and res.asr.model == "scribe_v2" and res.language == "eng"
    assert res.asr.raw_path == "index/asr_raw_elevenlabs.json"
    assert [(w.id, w.text, w.kind) for w in res.words] == [
        ("w0001", "Um,", "filler"), ("w0002", "the", "word"), ("w0003", "real", "word"),
        ("w0004", "(laughs)", "event"), ("w0005", "secret.", "word")]
    assert res.words[0].end_us == 380_000  # letter-derived edge
    assert all(w.sentence_id is None and w.cluster_id is None for w in res.words)
    assert json.loads((asr_job.root / res.asr.raw_path).read_text()) == SIMPLE_EL
    meta = json.loads((asr_job.index_dir / "asr_raw_elevenlabs.meta.json").read_text())
    assert meta["params"]["keyterms"] == ["Yunicorn", "Marque AI"] and len(meta["audio_sha256"]) == 64
    assert not list(asr_job.index_dir.glob(".asr_upload_*"))  # temp upload removed
    trace = [t for t in asr_job.read_trace() if t["event"] == "asr"]
    assert trace[-1]["status"] == "ok" and trace[-1]["cached"] is False

    # same audio + params → cache, no network
    res2 = transcribe(asr_job, keyterms=["Yunicorn", "Marque AI"], settings=keyed)
    assert len(rec.requests) == 1 and res2.asr.params["cached"] is True
    assert [w.model_dump() for w in res2.words] == [w.model_dump() for w in res.words]
    # changed params or refresh → new request
    transcribe(asr_job, keyterms=["Yunicorn"], settings=keyed)
    transcribe(asr_job, keyterms=["Yunicorn"], settings=keyed, refresh=True)
    assert len(rec.requests) == 3
    # changed audio → new request
    wav(asr_job.audio_path, tone(1.2))
    transcribe(asr_job, keyterms=["Yunicorn"], settings=keyed)
    assert len(rec.requests) == 4


def test_keys_never_persisted(asr_job: Job, keyed: Settings, mock_http):
    mock_http(el_ok())
    transcribe(asr_job, settings=keyed)
    for p in asr_job.root.rglob("*"):
        if p.is_file() and p.suffix in (".json", ".jsonl", ".md", ".log"):
            txt = p.read_text(errors="ignore")
            assert FAKE_EL_KEY not in txt and FAKE_AAI_KEY not in txt, p


def test_job_meta_keyterms_and_language(work_dir: Path, media_info, keyed: Settings, mock_http):
    job = Job.create("meta-job", work_dir=work_dir, meta={"keyterms": ["Nyra"], "language": "en"})
    job.save_media_info(media_info)
    wav(job.audio_path, tone(1.0))
    rec = mock_http(el_ok())
    transcribe(job, settings=keyed, num_speakers=1)
    fields, _ = multipart_fields(rec.requests[0])
    assert fields["keyterms"] == ["Nyra"] and fields["language_code"] == ["en"] and fields["num_speakers"] == ["1"]


def test_transient_errors_are_retried(asr_job: Job, keyed: Settings, mock_http):
    def route(req: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            return httpx.Response(429, headers={"retry-after": "1"}, json={"detail": "rate limited"})
        if n == 2:
            raise httpx.ConnectError("boom", request=req)
        return httpx.Response(200, json=SIMPLE_EL)

    rec = mock_http(route)
    res = transcribe(asr_job, settings=keyed, provider="elevenlabs")
    assert res.asr.provider == "elevenlabs" and len(rec.requests) == 3


# ---------------------------------------------------------------------------------------------- fallback
def test_fallback_to_assemblyai(asr_job: Job, keyed: Settings, mock_http):
    rec = mock_http(aai_route())
    res = transcribe(asr_job, keyterms=["Yunicorn"], settings=keyed)
    urls = rec.urls()
    assert urls[:3] == ["POST https://api.elevenlabs.io/v1/speech-to-text"] * 3  # 503 retried, then fallback
    assert urls[3:] == ["POST https://api.assemblyai.com/v2/upload", "POST https://api.assemblyai.com/v2/transcript",
                        "GET https://api.assemblyai.com/v2/transcript/t-1",
                        "GET https://api.assemblyai.com/v2/transcript/t-1"]
    up = rec.requests[3]
    assert up.headers["authorization"] == FAKE_AAI_KEY and up.headers["content-type"] == "application/octet-stream"
    assert up.content[:4] == b"fLaC"
    body = json.loads(rec.requests[4].content)
    assert body["audio_url"] == "https://cdn.assemblyai.com/upload/abc"
    assert body["speech_models"] == ["universal-3-5-pro", "universal-2"]
    assert body["disfluencies"] is True and body["speaker_labels"] is True
    assert body["prompt"].startswith(ASSEMBLYAI_VERBATIM_PROMPT) and "Yunicorn" in body["prompt"]
    assert "keyterms_prompt" not in body  # mutually exclusive with prompt

    assert res.asr.provider == "assemblyai" and res.asr.model == "universal-3-5-pro"
    assert res.asr.raw_path == "index/asr_raw_assemblyai.json"
    assert res.asr.params["fallback_from"][0]["provider"] == "elevenlabs"
    assert "HTTP 503" in res.asr.params["fallback_from"][0]["error"]
    assert [w.kind for w in res.words] == ["filler", "word", "event", "word"]
    failed = [t for t in asr_job.read_trace() if t.get("status") == "failed"]
    assert failed and failed[0]["provider"] == "elevenlabs"


def test_auth_error_falls_back_without_retry(asr_job: Job, keyed: Settings, mock_http):
    aai = aai_route()

    def route(req: httpx.Request, n: int) -> httpx.Response:
        if req.url.host == "api.elevenlabs.io":
            return httpx.Response(401, json={"detail": {"status": "invalid_api_key"}})
        return aai(req, n)

    rec = mock_http(route)
    res = transcribe(asr_job, settings=keyed)
    assert sum(1 for u in rec.urls() if "elevenlabs" in u) == 1
    assert res.asr.provider == "assemblyai"


def test_prompt_rejection_retries_without_prompt(asr_job: Job, keyed: Settings, mock_http):
    rec = mock_http(aai_route(reject_prompt=True))
    res = transcribe(asr_job, provider="assemblyai", keyterms=["Yunicorn"], settings=keyed)
    submits = [json.loads(r.content) for r in rec.requests if r.url.path == "/v2/transcript"]
    assert "prompt" in submits[0] and "prompt" not in submits[1]
    assert submits[1]["keyterms_prompt"] == ["Yunicorn"] and submits[1]["disfluencies"] is True
    assert res.asr.params["request"] == {k: v for k, v in submits[1].items() if k != "audio_url"}


def test_assemblyai_error_status(asr_job: Job, keyed: Settings, mock_http):
    mock_http(aai_route(fail_status=True))
    with pytest.raises(AsrError, match="audio too short"):
        transcribe(asr_job, provider="assemblyai", settings=keyed)


def test_explicit_provider_does_not_fall_back(asr_job: Job, keyed: Settings, mock_http):
    rec = mock_http(aai_route())
    with pytest.raises(AsrError, match="HTTP 503"):
        transcribe(asr_job, provider="elevenlabs", settings=keyed)
    assert all("elevenlabs" in u for u in rec.urls())


def test_missing_keys(asr_job: Job, settings: Settings, mock_http):
    rec = mock_http(el_ok())
    with pytest.raises(AsrError, match=r"elevenlabs.*assemblyai"):
        transcribe(asr_job, settings=settings)
    with pytest.raises(MissingKeyError):
        transcribe(asr_job, settings=settings, provider="assemblyai")
    assert rec.requests == []
    # only the fallback key configured → straight to AssemblyAI
    rec = mock_http(aai_route())
    res = transcribe(asr_job, settings=settings.with_keys(assemblyai=FAKE_AAI_KEY))
    assert res.asr.provider == "assemblyai" and not any("elevenlabs" in u for u in rec.urls())


def test_unknown_provider(asr_job: Job, keyed: Settings):
    with pytest.raises(ValueError, match="unknown ASR provider"):
        transcribe(asr_job, provider="whisper", settings=keyed)


def test_no_audio_stream_gives_empty_transcript(work_dir: Path, media_info, keyed: Settings, mock_http):
    job = Job.create("silent-job", work_dir=work_dir)
    job.save_media_info(media_info.model_copy(update={"audio": None}))
    rec = mock_http(el_ok())
    res = transcribe(job, settings=keyed)
    assert res.words == [] and res.asr.provider == "none" and rec.requests == []
    job2 = Job.create("missing-wav", work_dir=work_dir)
    job2.save_media_info(media_info)
    with pytest.raises(AsrError, match=r"audio\.wav"):
        transcribe(job2, settings=keyed)


def test_result_feeds_a_valid_take_index(asr_job: Job, keyed: Settings, mock_http, media_info):
    mock_http(el_ok())
    res = transcribe(asr_job, settings=keyed)
    words, sentences, clusters = analyze_takes(res.words)
    ix = TakeIndex(media=media_info, words=words, sentences=sentences, clusters=clusters, asr=res.asr)
    assert "{Um}" in ix.render_transcript("full") and "(laughs)" in ix.render_transcript("full")


# ---------------------------------------------------------------------------------------------- real API
TESTDATA = Path("/Users/home/studio-testdata")
D030 = TESTDATA / "qa-editor-d030-h264.mov"
FILLER_LINE = "Um, so, uh, the real secret is, um, restraint. Uh, you know, it's hard."


def _ffmpeg_audio(src: Path, dst: Path, *extra: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), *extra, "-vn",
                    "-ac", "1", "-ar", "48000", "-c:a", "pcm_f32le", str(dst)], check=True)


def _real_job(tmp_path: Path, name: str) -> Job:
    if shutil.which("ffmpeg") is None or not D030.exists():
        pytest.skip("ffmpeg or test media missing")
    return Job.create(name, work_dir=tmp_path / "work")


def _spoken_filler_clip(tmp_path: Path) -> Path:
    """A few seconds of speech that contains fillers (macOS ``say``)."""
    if shutil.which("say") is None:
        pytest.skip("macOS 'say' not available to synthesize filler speech")
    aiff = tmp_path / "fillers.aiff"
    subprocess.run(["say", "-r", "150", "-o", str(aiff), FILLER_LINE], check=True)
    out = tmp_path / "fillers.wav"
    _ffmpeg_audio(aiff, out)
    return out


@pytest.mark.real
def test_real_scribe_v2_d030_keeps_fillers(tmp_path: Path):
    """Scribe v2 verbatim on d030's audio with a spoken filler line prepended: fillers survive, the
    d030 read is word-accurate, words carry character timings, and the raw response is cached."""
    settings = get_settings()
    if not settings.has_key("elevenlabs"):
        pytest.skip("ELEVENLABS key not configured")
    job = _real_job(tmp_path, "real-scribe")
    d030 = tmp_path / "d030.wav"
    _ffmpeg_audio(D030, d030)
    fillers = _spoken_filler_clip(tmp_path)
    a, sr = sf.read(str(fillers), dtype="float32")
    b, _ = sf.read(str(d030), dtype="float32")
    gap = np.zeros(int(0.8 * sr), dtype=np.float32)
    sf.write(str(job.audio_path), np.concatenate([a, gap, b]), sr, subtype="FLOAT")

    res = transcribe(job, provider="elevenlabs", settings=settings)
    assert res.asr.provider == "elevenlabs" and res.asr.model == "scribe_v2"
    assert (job.index_dir / "asr_raw_elevenlabs.json").exists()
    words = res.words
    assert [w.id for w in words] == [f"w{i:04d}" for i in range(1, len(words) + 1)]
    assert all(w.chars for w in words if w.kind != "event")
    head_end = int(len(a) / sr * 1e6) + 400_000
    head_fillers = [w for w in words if w.start_us < head_end and w.kind == "filler"]
    assert len(head_fillers) >= 2, [(w.text, w.kind) for w in words if w.start_us < head_end]
    assert {normalize_token(w.text) for w in head_fillers} & {"um", "uh"}
    text = " ".join(normalize_token(w.text) for w in words)
    for phrase in ("everyone says you need an authentic brand story", "people will buy",
                   "the frame you hang that proof on"):
        assert phrase in text
    starts = [w.start_us for w in words]
    assert starts == sorted(starts) and max(w.end_us for w in words) <= len(np.concatenate([a, gap, b])) / sr * 1e6

    again = transcribe(job, provider="elevenlabs", settings=settings)
    assert again.asr.params["cached"] is True and len(again.words) == len(words)


@pytest.mark.real
def test_real_assemblyai_fallback_request_is_accepted(tmp_path: Path):
    """One small AssemblyAI call confirming the fallback request format (verbatim prompt + disfluencies)."""
    settings = get_settings()
    if not settings.has_key("assemblyai"):
        pytest.skip("AssemblyAI key not configured")
    job = _real_job(tmp_path, "real-aai")
    clip = _spoken_filler_clip(tmp_path)
    shutil.copy(clip, job.audio_path)
    res = transcribe(job, provider="assemblyai", settings=settings)
    assert res.asr.provider == "assemblyai" and res.words
    assert "prompt" in res.asr.params["request"], res.asr.params["request"]
    assert any(w.kind == "filler" for w in res.words), [(w.text, w.kind) for w in res.words]
