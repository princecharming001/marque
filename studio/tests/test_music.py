"""Tests for studio.audio.music: requests, ElevenLabs client (mocked HTTP), caching + licences, analysis on
synthetic tracks with known ground truth, fitting/backtiming, ducking, and the timeline render. Keyless and
offline; the one ``real`` test generates a 12 s instrumental via ElevenLabs."""

from __future__ import annotations

import io
import json
import math
from fractions import Fraction
from pathlib import Path

import httpx
import numpy as np
import pytest
import soundfile as sf

from studio.audio import music as M
from studio.audio.music import (
    DuckParams,
    ElevenLabsError,
    ElevenLabsMusicClient,
    MusicChunk,
    MusicRequest,
    analyze_music,
    build_music_request,
    carve_speech_band,
    decode_elevenlabs_audio,
    detect_speech_spans,
    duck_bed,
    ducking_envelope,
    find_music,
    fit,
    gain_to_db,
    generate_music,
    import_music_file,
    infer_pcm_channels,
    integrated_lufs,
    load_audio,
    music_analysis_for,
    pcm16_to_array,
    plan_chunks,
    render_music_bed,
    speech_spans_from_timeline,
    to_mono,
)
from studio.compile.models import Timeline, TimelineMusic, TimelineSegment, WordSpan
from studio.config import Settings
from studio.doc.model import Licence, MusicSpec, StyleDials
from studio.jobs import Job

SR = 48000
KEY = "test-key-0123456789"

# ============================================================================================ synthetic music
_NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_CHORDS = [("C", ["C", "E", "G"]), ("A", ["A", "C", "E"]), ("F", ["F", "A", "C"]), ("G", ["G", "B", "D"])]


def _hz(name: str, octave: int) -> float:
    return 440.0 * 2 ** ((_NOTE[name] - 9) / 12 + (octave - 4))


def make_track(bpm: float = 100.0, bars: int = 12, *, pickup_beats: int = 0, ending: str = "button",
               tail_s: float = 1.6, seed: int = 0) -> tuple[np.ndarray, dict]:
    """Stereo 48 kHz 4/4 groove in C major (C–Am–F–G, one chord per bar): kick on 1 (strong) and 3,
    snare on 2 and 4, hats on every beat, bass on 1 and 3. ``ending``: ``button`` (a final C-major hit with
    a decaying ring-out), ``cut`` (the file stops mid-sound) or ``fade`` (4 s fade to silence)."""
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    bar = 4 * beat
    t0 = pickup_beats * beat
    end_music = t0 + bars * bar
    total = end_music + (tail_s + 0.3 if ending == "button" else 1.0 if ending == "fade" else 0.0)
    n = round(total * SR)
    y = np.zeros(n)

    def add(sig: np.ndarray, at: float) -> None:
        i = round(at * SR)
        if i < n:
            m = min(len(sig), n - i)
            y[i:i + m] += sig[:m]

    def env(m: int, tau: float, att: float = 0.001) -> np.ndarray:
        t = np.arange(m) / SR
        return np.exp(-t / tau) * np.minimum(1, t / att)

    def kick(amp: float, dec: float = 0.18) -> np.ndarray:
        m = round(0.35 * SR)
        t = np.arange(m) / SR
        f = 50 + 70 * np.exp(-t / 0.03)
        return amp * np.sin(2 * np.pi * np.cumsum(f) / SR) * env(m, dec)

    def hat(amp: float) -> np.ndarray:
        m = round(0.06 * SR)
        return amp * np.diff(np.concatenate([[0], rng.standard_normal(m)])) * env(m, 0.012) * 0.3

    def snare(amp: float) -> np.ndarray:
        m = round(0.2 * SR)
        t = np.arange(m) / SR
        noise = rng.standard_normal(m) * np.exp(-t / 0.05) * 0.5
        return amp * (noise + np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.04))

    def chord(notes: list[str], dur: float, amp: float, dec: float | None = None) -> np.ndarray:
        m = round(dur * SR)
        t = np.arange(m) / SR
        e = np.minimum(1, t / 0.01) * (np.exp(-t / dec) if dec else np.exp(-t / (dur * 2)))
        return amp * e * sum(np.sin(2 * np.pi * _hz(x, 4) * t) + 0.3 * np.sin(4 * np.pi * _hz(x, 4) * t)
                             for x in notes) / len(notes)

    def bass(root: str, dur: float, amp: float) -> np.ndarray:
        m = round(dur * SR)
        t = np.arange(m) / SR
        return amp * np.sin(2 * np.pi * _hz(root, 2) * t) * env(m, 0.4, 0.005)

    for k in range(pickup_beats):
        add(hat(0.5), k * beat)
    for b in range(bars):
        root, notes = _CHORDS[b % 4]
        ts = t0 + b * bar
        add(chord(notes, bar, 0.35), ts)
        add(bass(root, 2 * beat, 0.35), ts)
        add(bass(root, 2 * beat, 0.25), ts + 2 * beat)
        add(kick(0.9), ts)
        add(kick(0.55), ts + 2 * beat)
        add(snare(0.35), ts + beat)
        add(snare(0.35), ts + 3 * beat)
        for k in range(4):
            add(hat(0.5), ts + k * beat)
    if ending == "button":
        add(kick(1.0, dec=0.3), end_music)
        add(chord(["C", "E", "G"], tail_s, 0.6, dec=0.35), end_music)
        add(bass("C", tail_s, 0.4), end_music)
    y = y / np.max(np.abs(y)) * 0.8
    if ending == "fade":
        fs = round((end_music - 4.0) * SR)
        y[fs:] *= np.linspace(1, 0, n - fs) ** 2
    st = np.stack([y, 0.97 * y]).astype(np.float32)
    return st, {"bpm": bpm, "beat": beat, "bar": bar, "t0": t0, "end_music": end_music,
                "button_s": end_music if ending == "button" else None}


@pytest.fixture(scope="module")
def track() -> tuple[np.ndarray, dict]:
    return make_track()


@pytest.fixture(scope="module")
def track_analysis(track: tuple[np.ndarray, dict]) -> M.MusicAnalysis:
    y, meta = track
    return analyze_music(y, SR, bpm_hint=meta["bpm"], detect_voice=False)


def _button_time(audio: np.ndarray, around: float, win: float = 0.3) -> float:
    x = to_mono(audio)
    lo, hi = round((around - win) * SR), round((around + win) * SR)
    e = np.abs(x[lo:hi])
    return (lo + int(np.argmax(e > 0.5 * e.max()))) / SR


def _pcm16(audio: np.ndarray) -> bytes:
    a = np.atleast_2d(audio)
    inter = np.clip(a.T, -1, 1)
    return (inter * 32767).astype("<i2").tobytes()


# ============================================================================================ helpers + utils
def test_k_weighting_matches_bs1770_at_48k() -> None:
    (b1, a1), (b2, a2) = M._biquad_k(48000)
    assert np.allclose(b1, [1.53512485958697, -2.69169618940638, 1.19839281085285], atol=1e-6)
    assert np.allclose(a1, [1.0, -1.69065929318241, 0.73248077421585], atol=1e-6)
    assert np.allclose(b2, [1.0, -2.0, 1.0])
    assert np.allclose(a2, [1.0, -1.99004745483398, 0.99007225036621], atol=1e-6)


def test_window_loudness_matches_integrated_for_steady_noise() -> None:
    x = np.random.default_rng(1).standard_normal((2, SR * 6)).astype(np.float32) * 0.05
    integ = integrated_lufs(x, SR)
    st = M.short_term_lufs_at(x, SR, 3.0)
    assert integ is not None and abs(integ - st) < 0.5


def test_pcm_decoding_and_channel_inference() -> None:
    mono = (np.sin(2 * np.pi * 440 * np.arange(SR) / SR) * 0.5).astype(np.float32)
    stereo = np.stack([mono, -mono])
    assert infer_pcm_channels(SR, SR, 1.0) == 1
    assert infer_pcm_channels(2 * SR, SR, 1.0) == 2
    assert infer_pcm_channels(2 * SR, SR, None, default=1) == 1
    a = pcm16_to_array(_pcm16(stereo), 2)
    assert a.shape == (2, SR) and np.allclose(a[0], -a[1], atol=1e-4)
    dec, sr = decode_elevenlabs_audio(_pcm16(stereo), "pcm_48000", expected_s=1.0)
    assert sr == SR and dec.shape == (2, SR)
    dec2, _ = decode_elevenlabs_audio(_pcm16(mono), "pcm_48000", expected_s=1.0)
    assert dec2.shape == (1, SR)
    # a container is sniffed even if the format says pcm
    buf = io.BytesIO()
    sf.write(buf, stereo.T, 44100, format="WAV", subtype="PCM_16")
    dec3, sr3 = decode_elevenlabs_audio(buf.getvalue(), "pcm_48000", expected_s=None)
    assert sr3 == SR and dec3.shape[0] == 2 and abs(dec3.shape[1] - round(SR * SR / 44100)) < 5
    with pytest.raises(ValueError):
        decode_elevenlabs_audio(b"", "pcm_48000")


# ============================================================================================ requests
def test_build_request_plan_mode_from_doc(cut_doc, take_index) -> None:
    doc = cut_doc.model_copy(deep=True)
    doc.style.primary = "listicle"
    doc.style.dials = StyleDials(energy=0.8)
    req = build_music_request(doc, duration_s=30.0, index=take_index, end_anchor_s=29.4, hits_s=[12.0])
    req.validate_api()
    assert req.mode == "plan" and req.model_id == "music_v2_5" and req.output_format == "pcm_48000"
    assert 96 <= req.bpm <= 118
    bar = 240.0 / req.bpm
    # the button's bar: the first bar line at least 0.3 s after the anchor (the fitter trims the head)
    assert req.ending_gen_s == pytest.approx(bar * math.ceil((29.4 + M.ENDING_PREROLL_S) / bar))
    assert req.gen_offset_s == pytest.approx(req.ending_gen_s - 29.4)
    body = req.body()
    chunks = body["composition_plan"]["chunks"]
    assert body["model_id"] == "music_v2_5" and body["seed"] == 1000
    assert "prompt" not in body and "force_instrumental" not in body
    total = sum(c["duration_ms"] for c in chunks)
    assert abs(total - (req.ending_gen_s + M.ENDING_CHUNK_S) * 1000) <= 2
    assert [c["text"].split("]")[0] for c in chunks] == ["[Intro", "[Lift", "[Ending"]
    edges = np.cumsum([0] + [c["duration_ms"] for c in chunks])[:-1] / 1000.0
    for e in edges:  # every boundary on the requested bar grid
        assert abs(e / bar - round(e / bar)) * bar < 0.002
    # the tempo was nudged so payoff→ending is a whole number of bars: the lift bar lands on the payoff
    assert abs(edges[1] - (12.0 + req.gen_offset_s)) < 0.15
    assert edges[2] == pytest.approx(req.ending_gen_s, abs=0.002)
    assert len(chunks[0]["positive_styles"]) >= 7
    assert f"{req.bpm:.0f} BPM" in chunks[0]["positive_styles"]
    for c in chunks:
        assert M.CHUNK_MIN_MS <= c["duration_ms"] <= M.CHUNK_MAX_MS
        assert {"vocals", "lyrics", "vocal chops"} <= set(c["negative_styles"])
        assert c["context_adherence"] == "high"
    assert "one final chord hit exactly at the start" in chunks[-1]["text"]
    assert "drums after the final hit" in chunks[-1]["negative_styles"]
    assert "in " + req.key in chunks[0]["positive_styles"]


def test_build_request_prompt_mode_and_validation() -> None:
    req = build_music_request(None, duration_s=12.0, style="tutorial", mode="prompt", bpm=90, key="D major")
    req.validate_api()
    body = req.body()
    assert body["force_instrumental"] is True and "seed" not in body and "composition_plan" not in body
    assert body["music_length_ms"] == round((req.ending_gen_s + M.ENDING_CHUNK_S) * 1000)
    assert "90 BPM" in body["prompt"] and "D major" in body["prompt"] and "no vocals" in body["prompt"]
    assert f"exactly at {req.ending_gen_s:.1f} seconds" in body["prompt"]
    with pytest.raises(ValueError):
        req.model_copy(update={"seed": 3}).validate_api()
    with pytest.raises(ValueError):
        MusicRequest(mode="prompt", prompt="x", chunks=[MusicChunk(text="[A]", duration_ms=5000)]).validate_api()
    with pytest.raises(ValueError):
        MusicRequest(mode="prompt", prompt="x", music_length_ms=1000).validate_api()
    with pytest.raises(ValueError):
        MusicRequest(mode="plan").validate_api()
    with pytest.raises(ValueError):
        MusicRequest(mode="plan", model_id="music_v9", chunks=[MusicChunk(text="[A]", duration_ms=5000)]).validate_api()
    with pytest.raises(ValueError):  # chunk bounds come from the schema
        MusicChunk(text="[A]", duration_ms=2000)


def test_plan_chunks_long_and_short_and_v1_mapping() -> None:
    long = plan_chunks(300.0, bar_s=2.4)
    assert all(M.CHUNK_MIN_MS <= c.duration_ms <= M.CHUNK_MAX_MS for c in long)
    assert sum(c.duration_ms for c in long) == 303_500
    assert long[-1].text.startswith("[Ending]") and sum(c.text.startswith("[Ending]") for c in long) == 1
    edges = np.cumsum([0] + [c.duration_ms for c in long])[:-1] / 1000.0
    assert all(abs(e / 2.4 - round(e / 2.4)) < 1e-3 for e in edges)
    short = plan_chunks(4.8, bar_s=2.4)
    assert [c.text.split("]")[0] for c in short] == ["[Main", "[Ending"] and short[0].duration_ms == 4800
    tiny = plan_chunks(1.0)
    assert len(tiny) == 1 and tiny[0].duration_ms == 4500 and "final chord hit" in tiny[0].text
    req = MusicRequest(mode="plan", model_id="music_v1", chunks=plan_chunks(38.4, bar_s=2.4, hits_s=[15.0],
                                                                          global_styles=["a", "b"]))
    body = req.body()
    plan = body["composition_plan"]
    assert body["respect_sections_durations"] is True
    assert plan["positive_global_styles"] == ["a", "b"]
    assert "vocals" in plan["negative_global_styles"]
    assert [s["section_name"] for s in plan["sections"]] == ["Intro", "Lift", "Ending"]
    assert plan["sections"][0]["duration_ms"] == 14400
    assert all(s["lines"] == [] for s in plan["sections"])


def test_bpm_nudged_to_whole_bars() -> None:
    assert M._bpm_for_bars(100, 17.4) == 97  # 7.03 bars instead of 7.25
    b = M._bpm_for_bars(100, 6.4)  # no whole-bar tempo within ±7%: the closest improvement is chosen

    def resid_s(bpm: int, d: float) -> float:
        bars = d * bpm / 240
        return abs(bars - round(bars)) * 240 / bpm

    assert 93 <= b <= 107 and resid_s(b, 6.4) < resid_s(100, 6.4)
    assert M._bpm_for_bars(100, 0.0) == 100
    req = build_music_request(None, duration_s=40.0, style="founder", end_anchor_s=39.3, hits_s=[20.0], bpm=90)
    assert req.bpm == 90  # an explicit tempo is never changed


def test_cache_key_stable_and_sensitive() -> None:
    a = build_music_request(None, duration_s=20.0, style="founder")
    b = build_music_request(None, duration_s=20.0, style="founder")
    c = build_music_request(None, duration_s=20.0, style="founder", variant=1)
    assert a.cache_key() == b.cache_key() != c.cache_key()
    assert c.instruments != a.instruments  # candidates differ in instrumentation


# ============================================================================================ ElevenLabs client
class _FakeEL:
    """MockTransport handler emulating /v1/music and /v1/user/subscription."""

    def __init__(self, audio: np.ndarray, *, fail_first: int = 0, status: int = 200, tier: str = "creator"):
        self.audio = audio
        self.fail_first = fail_first
        self.status = status
        self.tier = tier
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers.get("xi-api-key") == KEY
        if request.url.path == "/v1/user/subscription":
            return httpx.Response(200, json={"tier": self.tier, "status": "active"})
        assert request.url.path == "/v1/music"
        if self.fail_first > 0:
            self.fail_first -= 1
            return httpx.Response(429, headers={"retry-after": "0"}, json={"detail": "busy"})
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": {"message": "invalid", "echo": KEY}})
        return httpx.Response(200, content=_pcm16(self.audio),
                              headers={"content-type": "audio/pcm", "song-id": "song123", "request-id": "req9",
                                       "x-secret-token": "nope"})

    @property
    def music_calls(self) -> int:
        return sum(1 for r in self.requests if r.url.path == "/v1/music")


def _client(handler: _FakeEL) -> ElevenLabsMusicClient:
    return ElevenLabsMusicClient(KEY, transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_client_request_format_and_retry(track) -> None:
    y, _ = track
    fake = _FakeEL(y[0], fail_first=1)
    cl = _client(fake)
    req = build_music_request(None, duration_s=12.0, style="listicle")
    data, headers = cl.compose(req)
    assert fake.music_calls == 2  # one 429, one success
    r = [q for q in fake.requests if q.url.path == "/v1/music"][-1]
    assert r.method == "POST" and r.url.params["output_format"] == "pcm_48000"
    assert r.url.host == "api.elevenlabs.io"
    body = json.loads(r.content)
    assert body == req.body()
    assert headers == {"content-type": "audio/pcm", "song-id": "song123", "request-id": "req9"}
    assert len(data) == 2 * y.shape[1]
    assert KEY not in repr(cl)


def test_client_error_is_redacted(track) -> None:
    fake = _FakeEL(track[0][0], status=422)
    with pytest.raises(ElevenLabsError) as ei:
        _client(fake).compose(build_music_request(None, duration_s=10.0))
    assert ei.value.status == 422 and KEY not in str(ei.value) and "422" in str(ei.value)


def test_client_requires_key() -> None:
    from studio.config import MissingKeyError

    with pytest.raises(MissingKeyError):
        ElevenLabsMusicClient("")
    with pytest.raises(MissingKeyError):
        ElevenLabsMusicClient.from_settings(Settings.load(env={}))


def test_generate_music_caches_and_writes_licence(job: Job, track) -> None:
    y, meta = track
    fake = _FakeEL(y[0])
    cl = _client(fake)
    req = build_music_request(None, duration_s=meta["end_music"] - 0.2, style="listicle", bpm=100)
    spec = generate_music(job, req, client=cl, detect_voice=False)
    assert fake.music_calls == 1
    assert spec.source == "elevenlabs" and spec.asset is not None and spec.asset_id == spec.asset.id
    assert spec.asset_id.startswith("elmusic_")
    wav = job.path(spec.asset.path)
    info = sf.info(str(wav))
    assert info.samplerate == 48000 and info.channels == 1  # mono PCM in, mono file out
    lic = spec.asset.licence
    assert lic is not None and lic.commercial_use and not lic.attribution_required
    assert lic.record_path == f"assets/music/{spec.asset_id}.licence.json"
    rec = json.loads(job.path(lic.record_path).read_text())
    assert rec["plan_tier"] == "creator" and rec["request"]["body"] == req.body()
    assert rec["response_headers"]["song-id"] == "song123" and "x-secret-token" not in rec["response_headers"]
    assert KEY not in job.path(lic.record_path).read_text()
    assert job.load_asset(spec.asset_id) is not None
    an = music_analysis_for(job, spec.asset)
    assert abs(an.tempo_bpm - 100) < 1.5
    # cache hit: no second HTTP call
    spec2 = generate_music(job, req, client=cl, detect_voice=False)
    assert fake.music_calls == 1 and spec2.asset_id == spec.asset_id
    assert any(t["event"] == "music_cache_hit" for t in job.read_trace())
    assert KEY not in job.trace_path.read_text()


def test_free_tier_licence_requires_attribution(job: Job, track) -> None:
    fake = _FakeEL(track[0][0][: SR * 10], tier="free")
    spec = generate_music(job, build_music_request(None, duration_s=8.0), client=_client(fake), detect_voice=False)
    assert spec.asset.licence.attribution_required and spec.asset.licence.attribution == "Eleven Music"
    assert not spec.asset.licence.commercial_use


def test_find_music_paths(job: Job, cut_doc, track, settings) -> None:
    doc = cut_doc.model_copy(deep=True)
    # no key, no client -> no music (valid) + trace
    assert find_music(job, doc, duration_s=15.0, settings=settings) == []
    assert any(t["event"] == "music_unavailable" for t in job.read_trace())
    # source none
    doc.audio.music = MusicSpec(source="none")
    assert find_music(job, doc, duration_s=15.0, settings=settings) == []
    # generated candidates via a mocked client, keeping the Director's mix settings
    doc.audio.music = MusicSpec(source="elevenlabs", level_lu_under_speech=-20.0, mood="warm")
    fake = _FakeEL(track[0][0])
    specs = find_music(job, doc, duration_s=20.0, settings=settings, client=_client(fake), n=2, detect_voice=False)
    assert len(specs) == 2 and fake.music_calls == 2
    assert all(s.asset is not None and s.asset.licence is not None for s in specs)
    assert all(s.level_lu_under_speech == -20.0 for s in specs)
    assert len({s.asset_id for s in specs}) == 2
    # an already-chosen registered asset is returned as-is
    doc.audio.music = MusicSpec(source="elevenlabs", asset_id=specs[0].asset_id)
    again = find_music(job, doc, duration_s=20.0, settings=settings)
    assert len(again) == 1 and again[0].asset.id == specs[0].asset_id


# ============================================================================================ analysis
def test_analysis_tempo_downbeats_key_ending(track, track_analysis) -> None:
    _, meta = track
    a = track_analysis
    assert abs(a.tempo_bpm - 100.0) < 1.0
    assert a.tempo_stability < 0.02
    assert a.beats_per_bar == 4
    assert abs(a.bar_s - meta["bar"]) < 0.02
    assert abs(a.downbeats_s[0] - meta["t0"]) < 0.03
    pos = [(d - meta["t0"]) / meta["bar"] for d in a.downbeats_s]
    assert all(abs(p - round(p)) < 0.02 for p in pos)
    assert a.key == "C major"
    assert a.ending.kind == "button" and abs(a.ending.ending_s - meta["button_s"]) < 0.015
    assert a.ending.ring_out_s > 1.0
    assert a.integrated_lufs is not None and -30 < a.integrated_lufs < -10
    assert a.speech_band_ratio_db is not None
    assert "BPM" in a.summary()


@pytest.mark.parametrize("pickup", [1, 2])
def test_analysis_downbeat_with_pickup(pickup: int) -> None:
    y, meta = make_track(pickup_beats=pickup, bars=10)
    a = analyze_music(y, SR, bpm_hint=100, detect_voice=False)
    assert abs(a.downbeats_s[0] - meta["t0"]) < 0.03 or abs(a.downbeats_s[1] - meta["t0"]) < 0.03


def test_analysis_octave_correction_uses_hint() -> None:
    y, _ = make_track(bpm=124, bars=12)
    a = analyze_music(y, SR, bpm_hint=124, detect_voice=False)
    assert abs(a.tempo_bpm - 124) < 1.5
    b = analyze_music(y, SR, bpm_hint=62, detect_voice=False)  # half-time request -> reported near the hint's octave
    assert abs(b.tempo_bpm - 62) < 1.5 or abs(b.tempo_bpm - 124) < 1.5


def test_analysis_cut_and_fade_endings() -> None:
    y, _ = make_track(ending="cut", bars=10)
    assert analyze_music(y, SR, bpm_hint=100, detect_voice=False).ending.kind == "cut"
    y2, meta = make_track(ending="fade", bars=12)
    e = analyze_music(y2, SR, bpm_hint=100, detect_voice=False).ending
    assert e.kind == "fade" and e.ending_s is not None and e.ending_s < meta["end_music"] - 2.0


def test_analysis_silence_and_voice_detector() -> None:
    a = analyze_music(np.zeros((2, SR // 2), np.float32), SR)
    assert a.tempo_bpm == 0 and a.notes
    y, _ = make_track(bars=3)
    b = analyze_music(y, SR, bpm_hint=100, detect_voice=True)
    assert b.voice_ratio is not None and b.voice_ratio < 0.2


# ============================================================================================ fit
@pytest.mark.parametrize("L,anchor", [(20.0, 19.4), (12.0, 11.5), (45.0, 44.3), (70.0, 69.0)])
def test_fit_backtimes_button_onto_anchor(track, track_analysis, L: float, anchor: float) -> None:
    y, _ = track
    fr = fit(y, L, sr=SR, analysis=track_analysis, end_anchor_s=anchor, entry="any")
    assert fr.audio.shape == (2, round(L * SR)) and fr.audio.dtype == np.float32
    assert abs(_button_time(fr.audio, anchor) - anchor) < 0.012
    assert fr.ending_kind == "button" and fr.ending_out_s == pytest.approx(anchor)
    assert np.max(np.abs(fr.audio[:, :5])) < 1e-3 and np.max(np.abs(fr.audio[:, -5:])) < 1e-3
    assert np.all(np.isfinite(fr.audio)) and np.max(np.abs(fr.audio)) <= 1.0
    if track_analysis.duration_s < L:
        assert any(j["similarity"] > 0.8 for j in fr.junctions)
        assert any("repeating whole bars" in n for n in fr.notes)
        for j in fr.junctions:  # every edit is on the bar grid
            assert min(abs(j["from_src_s"] - d) for d in track_analysis.downbeats_s) < 1e-3
            assert min(abs(j["to_src_s"] - d) for d in track_analysis.downbeats_s) < 1e-3


def test_fit_junction_is_click_free(track, track_analysis) -> None:
    y, _ = track
    fr = fit(y, 45.0, sr=SR, analysis=track_analysis, end_anchor_s=44.3, entry="any")
    assert fr.junctions
    x = to_mono(fr.audio).astype(np.float64)
    for j in fr.junctions:
        i = round(j["out_s"] * SR)
        seg = np.abs(np.diff(x[i - 2000:i - 200]))  # the crossfade region before the incoming downbeat
        ref = np.abs(np.diff(x[i - 30000:i - 10000]))
        assert seg.max() <= ref.max() * 1.5 + 1e-3


def test_fit_lands_payoff_hit_on_a_section_downbeat(track, track_analysis) -> None:
    y, _ = track
    fr = fit(y, 20.0, [9.7], sr=SR, analysis=track_analysis, end_anchor_s=19.4, entry="any")
    assert fr.hits and abs(fr.hits[0]["error_ms"]) <= 35.0
    assert 0.97 <= fr.stretch <= 1.03
    assert abs(_button_time(fr.audio, 19.4) - 19.4) < 0.012  # the ending stays exact under the stretch


def test_fit_unreachable_hit_is_reported_not_forced(track, track_analysis) -> None:
    y, _ = track
    fr = fit(y, 20.0, [8.0], sr=SR, analysis=track_analysis, end_anchor_s=19.4, entry="any")
    assert fr.stretch == 1.0 and fr.hits[0]["error_ms"] != 0.0


@pytest.mark.parametrize("L,anchor", [(26.0, 25.3), (6.0, 5.3)])
def test_fit_downbeat_entry(track, track_analysis, L: float, anchor: float) -> None:
    y, _ = track
    fr = fit(y, L, sr=SR, analysis=track_analysis, end_anchor_s=anchor, fade_in_ms=40)  # auto -> downbeat
    beats = np.asarray(track_analysis.beats_s)
    assert float(np.min(np.abs(beats - fr.entry_src_s))) < 0.02
    assert abs(_button_time(fr.audio, anchor) - anchor) < 0.012
    if fr.lead_silence_s:
        assert np.max(np.abs(fr.audio[:, : round(fr.lead_silence_s * SR) - 10])) == 0.0


def test_fit_bed_without_ending_fades_after_last_word() -> None:
    y, _ = make_track(ending="cut", bars=16)
    a = analyze_music(y, SR, bpm_hint=100, detect_voice=False)
    fr = fit(y, 20.0, sr=SR, analysis=a, end_anchor_s=19.0, soft_end_anchor_s=19.4)
    assert fr.ending_kind == "none" and any("no musical ending" in n for n in fr.notes)
    x = to_mono(fr.audio)
    assert np.max(np.abs(x[round(19.95 * SR):])) < 0.05 * np.max(np.abs(x))
    downs = np.asarray(fr.downbeats_out_s)
    assert np.min(np.abs(downs - 19.4)) < 0.012


def test_fit_not_backtimed_and_ambient_fallback(track, track_analysis) -> None:
    y, _ = track
    fr = fit(y, 10.0, sr=SR, analysis=track_analysis, backtime=False)
    assert fr.audio.shape == (2, 10 * SR) and fr.ending_out_s is None
    assert np.max(np.abs(fr.audio[:, -10:])) < 1e-3
    # a beatless drone: no grid -> tiled with long crossfades when too short
    t = np.arange(SR * 4) / SR
    drone = np.stack([0.2 * np.sin(2 * np.pi * 220 * t)] * 2).astype(np.float32)
    fr2 = fit(drone, 9.0, sr=SR, backtime=False)
    assert fr2.audio.shape == (2, 9 * SR) and np.all(np.isfinite(fr2.audio))


# ============================================================================================ ducking
def _speechy(n: int, spans: list[tuple[float, float]]) -> np.ndarray:
    rng = np.random.default_rng(3)
    x = rng.standard_normal(n) * 0.0005
    for a, b in spans:
        i, j = round(a * SR), round(b * SR)
        x[i:j] += rng.standard_normal(j - i) * 0.1
    return x.astype(np.float32)


def test_ducking_envelope_shape() -> None:
    spans = [(1.0, 3.0), (3.6, 5.0), (7.5, 9.0)]
    n = SR * 12
    g = gain_to_db(ducking_envelope(None, DuckParams(depth_db=6.0), sr=SR, speech_spans=spans, n=n))
    at = lambda t: float(g[round(t * SR)])  # noqa: E731
    assert at(0.5) == pytest.approx(0.0, abs=1e-3)
    assert at(0.99) == pytest.approx(-6.0, abs=0.05)  # pre-ducked before the first word
    assert at(3.3) == pytest.approx(-6.0, abs=0.05)  # held through a 0.6 s gap
    assert at(6.5) == pytest.approx(0.0, abs=1e-3)  # released in a 2.5 s gap (swell)
    assert -6.0 < at(5.3) < 0.0  # releasing
    rel = g[round(5.0 * SR):round(5.8 * SR)]
    assert np.all(np.diff(rel) >= -1e-6)  # monotone release
    # detection path agrees with word times on clean synthetic speech
    dia = _speechy(n, spans)
    assert [(round(a, 1), round(b, 1)) for a, b in detect_speech_spans(dia, SR)] == [(1.0, 3.0), (3.6, 5.0), (7.5, 9.0)]
    g2 = gain_to_db(ducking_envelope(dia, {"depth_db": 6.0}, sr=SR))
    assert np.max(np.abs(g2[round(1.2 * SR):round(2.8 * SR)] + 6.0)) < 0.05
    # extra SFX dips
    g3 = gain_to_db(ducking_envelope(None, DuckParams(depth_db=6.0), sr=SR, speech_spans=spans, n=n,
                                     extra_spans=[(6.0, 6.4, 3.0)]))
    assert float(g3[round(6.2 * SR)]) == pytest.approx(-3.0, abs=0.05)
    with pytest.raises(ValueError):
        ducking_envelope(None)


def test_duck_params_from_music() -> None:
    tm = TimelineMusic(out_start=0, out_end=10, duck=False, duck_db=9.0)
    assert DuckParams.from_music(tm).depth_db == 0.0
    assert DuckParams.from_music(MusicSpec(duck_db=9.0)).depth_db == 9.0
    assert DuckParams.from_music({"duck": True, "duck_db": 4.0}).depth_db == 4.0


def test_carve_and_duck_bed() -> None:
    from scipy.signal import welch

    n = SR * 8
    spans = [(1.0, 3.0), (5.0, 6.0)]
    bed = (np.random.default_rng(5).standard_normal((2, n)) * 0.05).astype(np.float32)
    _, amt = ducking_envelope(None, DuckParams(), sr=SR, speech_spans=spans, n=n, return_amount=True)
    c = carve_speech_band(bed, amt, sr=SR, depth_db=3.0)

    def band(x: np.ndarray, lo: float, hi: float) -> float:
        f, p = welch(x, fs=SR, nperseg=4096)
        return 10 * np.log10(p[(f >= lo) & (f <= hi)].mean())

    s = slice(round(1.5 * SR), round(2.5 * SR))
    assert band(c[0, s], 1800, 2200) - band(bed[0, s], 1800, 2200) == pytest.approx(-3.0, abs=0.3)
    assert abs(band(c[0, s], 150, 250) - band(bed[0, s], 150, 250)) < 0.3
    gap = slice(round(3.9 * SR), round(4.1 * SR))
    assert np.max(np.abs(c[:, gap] - bed[:, gap])) < 1e-5
    ducked = duck_bed(bed, sr=SR, speech_spans=spans, params=DuckParams(depth_db=6.0))
    assert ducked.shape == bed.shape
    r = lambda x: 20 * np.log10(np.sqrt(np.mean(x ** 2)))  # noqa: E731
    assert r(ducked[:, s]) - r(bed[:, s]) == pytest.approx(-6.4, abs=0.6)
    assert r(ducked[:, gap]) == pytest.approx(r(bed[:, gap]), abs=0.05)
    mono = duck_bed(bed[0], sr=SR, speech_spans=spans)
    assert mono.shape == (n,)
    # exact calibration: the ducked bed sits level_lu_under_speech under the voice, measured over speech
    voice = _speechy(n, spans)
    info: dict = {}
    cal = duck_bed(bed, sr=SR, dialogue=voice, speech_spans=spans, level_lu_under_speech=-18.0, info=info)
    v = M.loudness_over_spans(voice, SR, spans)
    assert M.loudness_over_spans(cal, SR, spans) == pytest.approx(v - 18.0, abs=0.05)
    assert info["speech_lufs"] == pytest.approx(v, abs=0.01) and "calibration_gain_db" in info
    fixed = duck_bed(bed, sr=SR, speech_spans=spans, speech_lufs=-14.0, level_lu_under_speech=-20.0)
    assert M.loudness_over_spans(fixed, SR, spans) == pytest.approx(-34.0, abs=0.05)
    assert M.loudness_over_spans(bed, SR, []) is None


# ============================================================================================ import + render
def _timeline(duration: Fraction, *, last_onset: Fraction, music: TimelineMusic | None, hits=()) -> Timeline:
    words = {"w0001": WordSpan(out_start=Fraction(1), out_end=Fraction(3, 2)),
             "w0002": WordSpan(out_start=Fraction(8), out_end=Fraction(17, 2)),
             "w0009": None,
             "w0010": WordSpan(out_start=last_onset, out_end=last_onset + Fraction(2, 5))}
    us = round(float(duration) * 1_000_000)
    seg = TimelineSegment(seg_id="seg001", src_in_us=0, src_out_us=us, out_start=0, out_end=duration,
                          audio_src_in_us=0, audio_src_out_us=us)
    return Timeline(fps=30, duration=duration, segments=[seg], word_map=words, music=music, seams=[Fraction(6)])


def _import(job: Job, tmp_path: Path, y: np.ndarray, name: str = "bed") -> MusicSpec:
    src = tmp_path / f"{name}.wav"
    sf.write(str(src), y.T, SR, subtype="PCM_24")
    lic = Licence(name="Synthetic test fixture", source="test", commercial_use=True)
    return import_music_file(job, src, licence=lic, source="file", description="synthetic groove", bpm_hint=100,
                             detect_voice=False)


def test_import_music_file_registers_licence(job: Job, tmp_path: Path, track) -> None:
    spec = _import(job, tmp_path, track[0])
    assert spec.source == "file" and spec.asset.licence.record_path == f"assets/music/{spec.asset_id}.licence.json"
    rec = json.loads(job.path(spec.asset.licence.record_path).read_text())
    assert rec["original_filename"] == "bed.wav" and rec["licence"]["name"] == "Synthetic test fixture"
    assert job.path(f"assets/music/{spec.asset_id}.analysis.json").exists()
    assert load_audio(job.path(spec.asset.path)).shape[0] == 2
    with pytest.raises(ValueError):
        import_music_file(job, tmp_path / "bed.wav", licence=None)  # type: ignore[arg-type]


def test_render_music_bed_backtimed_and_levelled(job: Job, tmp_path: Path, track) -> None:
    y, _ = track
    spec = _import(job, tmp_path, y)
    last = Fraction(582, 30)  # 19.4 s
    tm = TimelineMusic(asset=spec.asset, out_start=0, out_end=20, level_lu_under_speech=-18.0, duck=True, duck_db=6.0,
                       fade_in_ms=500, fade_out_ms=1500)
    tl = _timeline(Fraction(20), last_onset=last, music=tm)
    bed = render_music_bed(job, spec, tl)
    assert bed.shape == (2, 20 * SR) and bed.dtype == np.float32
    assert abs(_button_time(bed, 19.4) - 19.4) < 0.012
    lufs = integrated_lufs(bed, SR)
    assert lufs == pytest.approx(-14.0 - 18.0 + 6.0, abs=0.3)
    louder = render_music_bed(job, spec, tl, speech_lufs=-10.0)
    assert integrated_lufs(louder, SR) == pytest.approx(-22.0, abs=0.3)
    raw = render_music_bed(job, spec, tl, level=False, mono=True)
    assert raw.shape == (20 * SR,)
    assert any(t["event"] == "music_fit" for t in job.read_trace())


def test_render_music_bed_region_from_start_word(job: Job, tmp_path: Path, track) -> None:
    y, _ = track
    spec = _import(job, tmp_path, y).model_copy(update={"start_word": "w0002", "fade_in_ms": 40})
    tl = _timeline(Fraction(20), last_onset=Fraction(582, 30), music=None)
    bed = render_music_bed(job, spec, tl)
    assert np.max(np.abs(bed[:, : 8 * SR - 10])) == 0.0  # nothing before the entry word
    assert np.max(np.abs(bed[:, 8 * SR + SR // 2:9 * SR])) > 0.0
    assert abs(_button_time(bed, 19.4) - 19.4) < 0.012


def test_render_music_bed_none(job: Job) -> None:
    tl = _timeline(Fraction(10), last_onset=Fraction(9), music=None)
    out = render_music_bed(job, MusicSpec(source="none"), tl)
    assert out.shape == (2, 10 * SR) and not np.any(out)


def test_speech_spans_from_timeline() -> None:
    tl = _timeline(Fraction(20), last_onset=Fraction(582, 30), music=None)
    spans = speech_spans_from_timeline(tl)
    assert spans[0] == (1.0, 1.5) and len(spans) == 3


# ============================================================================================ real
@pytest.mark.real
def test_real_elevenlabs_music_12s(tmp_path: Path) -> None:
    from studio.config import get_settings

    s = get_settings()
    if not s.has_key("elevenlabs"):
        pytest.skip("no ElevenLabs key")
    job = Job.create("real-music", work_dir=tmp_path)
    req = build_music_request(None, duration_s=12.0, style="listicle", end_anchor_s=11.4, hits_s=[5.0])
    spec = generate_music(job, req, settings=s)
    wav = job.path(spec.asset.path)
    info = sf.info(str(wav))
    rec = json.loads(job.path(spec.asset.licence.record_path).read_text())
    an = music_analysis_for(job, spec.asset)
    print("\nreal music:", info.samplerate, "Hz", info.channels, "ch", f"{info.frames / info.samplerate:.2f}s",
          "requested", req.expected_duration_s(), "s | tier", rec.get("plan_tier"), "|", an.summary())
    assert info.samplerate == 48000
    dur = info.frames / info.samplerate
    assert 0.6 * req.expected_duration_s() <= dur <= 1.6 * req.expected_duration_s()
    audio = load_audio(wav)
    assert float(np.max(np.abs(audio))) > 10 ** (-30 / 20)
    assert an.tempo_bpm > 0 and an.integrated_lufs is not None
    ratio = an.tempo_bpm / req.bpm
    assert min(abs(math.log(ratio * k)) for k in (0.5, 1.0, 2.0)) < math.log(1.1)
    assert spec.asset.licence is not None and job.path(spec.asset.licence.record_path).exists()
    fr = fit(audio, 12.0, [5.0], sr=SR, analysis=an, end_anchor_s=11.4)
    print("planned button at", req.ending_gen_s, "s (generated time) | measured", an.ending, "| fit",
          [(p.src_start_s, p.src_end_s) for p in fr.pieces], fr.hits, fr.notes)
    assert fr.audio.shape == (2, 12 * SR)
