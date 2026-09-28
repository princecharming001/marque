"""Tests for studio.audio.sfx: the procedural library (non-silent, exact durations, no clipping, clean
edges, correct sync points), sourcing + licences + caching, the ElevenLabs client (mocked HTTP) with
candidate selection, levels against dialogue, and the timeline render. Keyless and offline; the one
``real`` test generates a whoosh via ElevenLabs."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import httpx
import numpy as np
import pytest

from studio.audio import sfx as X
from studio.audio.music import peak_dbfs, short_term_lufs_at, to_mono
from studio.audio.sfx import (
    DEFAULT_CUE_GAIN_DB,
    FAMILIES,
    ElevenLabsSfxClient,
    SfxError,
    detect_sync,
    family_for,
    generate_sfx,
    key_to_pitch,
    level_guidance,
    load_sfx,
    procedural_sfx,
    recommend_level,
    render_sfx_track,
    resolve_sfx,
    sfx_duck_spans,
    synthesize,
)
from studio.compile.models import Timeline, TimelineSegment, TimelineSfx, WordSpan
from studio.config import Settings
from studio.doc.model import AssetRef, Licence, SfxCue
from studio.jobs import Job

SR = 48000
KEY = "test-key-0123456789"


def _env(x: np.ndarray, win_s: float) -> np.ndarray:
    w = max(1, round(win_s * SR))
    return np.sqrt(np.convolve(x.astype(np.float64) ** 2, np.ones(w) / w, mode="same"))


def _centroid(x: np.ndarray) -> float:
    p = np.abs(np.fft.rfft(x * np.hanning(x.size))) ** 2
    f = np.fft.rfftfreq(x.size, 1 / SR)
    return float((f * p).sum() / p.sum())


# ============================================================================================ procedural library
@pytest.mark.parametrize("kind", list(FAMILIES))
def test_procedural_family_is_clean(kind: str) -> None:
    fam = FAMILIES[kind]
    c = synthesize(kind, seed=0)
    x = c.audio
    assert c.sr == SR and x.dtype == np.float32 and x.shape[0] == 2
    assert c.duration_s == pytest.approx(fam.duration_s, abs=1.5 / SR)
    assert np.all(np.isfinite(x))
    assert peak_dbfs(x) == pytest.approx(-1.0, abs=0.05)  # normalised, never clipping
    assert float(np.max(np.abs(x))) < 0.95
    m = to_mono(x)
    active = m[_env(m, 0.01) > 10 ** (-40 / 20) * np.max(np.abs(m))]
    assert 20 * np.log10(np.sqrt(np.mean(active ** 2))) > -40  # non-silent
    assert float(np.max(np.abs(x.mean(axis=1)))) < 1e-3  # no DC
    assert float(np.max(np.abs(x[:, 0]))) < 2e-3 and float(np.max(np.abs(x[:, -1]))) < 1e-3  # click-free edges
    assert 0.0 <= c.sync_s <= c.duration_s
    if fam.sync == "end":
        assert c.sync_s == pytest.approx(c.duration_s, abs=1e-3)
    elif fam.sync == "onset":
        assert c.sync_s < 0.005
    else:
        assert c.sync_s == pytest.approx(detect_sync(x, SR, "peak"), abs=0.002)
    assert c.meta["source"] == "procedural"


def test_whoosh_shapes() -> None:
    w = synthesize("whoosh")
    frac = w.sync_s / w.duration_s
    assert 0.55 <= frac <= 0.85  # swells and peaks late
    m = to_mono(w.audio)
    early, late = m[: round(0.12 * SR)], m[round(w.sync_s * SR) - 2400:round(w.sync_s * SR) + 2400]
    assert _centroid(late) > 1.5 * _centroid(early)  # the band sweeps upward into the peak
    assert abs(float(np.corrcoef(w.audio[0], w.audio[1])[0, 1])) < 0.99  # a little width, still mono-safe
    assert float(np.sum(m)) != 0.0
    d = synthesize("swoosh_down")
    assert 0.15 <= d.sync_s / d.duration_s <= 0.45  # peaks early
    dm = to_mono(d.audio)
    assert _centroid(dm[: round(0.12 * SR)]) > _centroid(dm[round(0.35 * SR):round(0.5 * SR)])


def test_riser_builds_and_stops_dead() -> None:
    r = synthesize("riser")
    m = to_mono(r.audio)
    lvl = lambda a, b: 20 * np.log10(np.sqrt(np.mean(m[round(a * SR):round(b * SR)] ** 2)) + 1e-12)  # noqa: E731
    assert lvl(1.3, 1.49) - lvl(0.0, 0.2) > 15.0
    assert lvl(1.45, 1.495) > lvl(0.0, 0.2)  # still loud right before the dead stop


def test_impact_is_phone_audible_without_sub_boom() -> None:
    c = synthesize("impact")
    m = to_mono(c.audio).astype(np.float64)
    p = np.abs(np.fft.rfft(m, 1 << 16)) ** 2
    f = np.fft.rfftfreq(1 << 16, 1 / SR)
    tot = p[f >= 20].sum()
    assert p[(f >= 20) & (f < 60)].sum() / tot < 0.15
    assert p[(f >= 150) & (f < 4000)].sum() / tot > 0.15


def test_ding_follows_music_key_and_typing_varies() -> None:
    assert key_to_pitch("A minor") == pytest.approx(880.0)
    assert key_to_pitch("C major") == pytest.approx(1046.502, rel=1e-4)
    assert key_to_pitch("F major") == pytest.approx(1396.913, rel=1e-4)
    assert key_to_pitch("Bb") == pytest.approx(932.328, rel=1e-4)
    assert key_to_pitch(None) is None and key_to_pitch("??") is None
    d = synthesize("ding", pitch_hz=key_to_pitch("E minor"))
    m = to_mono(d.audio)
    p = np.abs(np.fft.rfft(m, 1 << 17))
    f0 = np.fft.rfftfreq(1 << 17, 1 / SR)[int(np.argmax(p))]
    assert f0 == pytest.approx(key_to_pitch("E minor"), rel=0.01)
    t = to_mono(synthesize("typing", seed=0).audio)
    peaks = np.nonzero(np.diff((_env(t, 0.002) > 0.3 * np.max(np.abs(t))).astype(int)) == 1)[0]
    assert len(peaks) >= 5
    ivals = np.diff(peaks) / SR
    assert np.std(ivals) > 0.01  # irregular, never a loop


def test_determinism_and_variation() -> None:
    a, b = synthesize("whoosh", seed=3), synthesize("whoosh", seed=3)
    assert np.array_equal(a.audio, b.audio)
    assert not np.array_equal(a.audio, synthesize("whoosh", seed=4).audio)
    assert not np.array_equal(synthesize("click", seed=0).audio, synthesize("click", seed=7).audio)


def test_family_aliases_and_unknown() -> None:
    assert family_for("hit").kind == "impact"
    assert family_for("Swoosh-Down").kind == "swoosh_down"
    assert family_for("chime").kind == "ding"
    assert family_for("camera shutter").kind == "shutter"
    assert family_for("record scratch") is None
    with pytest.raises(SfxError):
        synthesize("record scratch")
    txt = level_guidance()
    assert "whoosh" in txt and "LU" in txt and "-12" in txt


# ============================================================================================ sourcing
def test_resolve_procedural_registers_licence_and_caches(job: Job, settings: Settings) -> None:
    cue = SfxCue(id="fx001", kind="pop", anchor_word="w0018")
    a = resolve_sfx(job, cue, settings=settings)
    assert a.source == "procedural" and a.id and a.id.startswith("sfxp_pop_")
    assert a.licence is not None and a.licence.commercial_use and a.licence.record_path
    rec = json.loads(job.path(a.licence.record_path).read_text())
    assert rec["source"] == "procedural" and rec["sfx_kind"] == "pop" and len(rec["sha256"]) == 64
    side = json.loads(job.path(f"assets/sfx/{a.id}.analysis.json").read_text())
    assert side["sync_mode"] == "onset" and side["sync_s"] < 0.005
    assert side["peak_dbfs"] == pytest.approx(-1.0, abs=0.05)
    wav = job.path(a.path)
    mtime = wav.stat().st_mtime_ns
    again = resolve_sfx(job, cue, settings=settings)
    assert again.id == a.id and wav.stat().st_mtime_ns == mtime
    assert job.load_asset(a.id) is not None
    clip = load_sfx(job, a)
    assert clip.kind == "pop" and clip.sync_s == pytest.approx(side["sync_s"])


def test_resolve_policies(job: Job, settings: Settings) -> None:
    # generative family without a key falls back to the procedural library
    w = resolve_sfx(job, SfxCue(id="fx002", kind="whoosh", anchor_word="w0001"), settings=settings)
    assert w.source == "procedural"
    with pytest.raises(SfxError):
        resolve_sfx(job, SfxCue(id="fx003", kind="record scratch", anchor_word="w0001"), settings=settings)
    with pytest.raises(SfxError):
        resolve_sfx(job, SfxCue(id="fx004", kind="whoosh", anchor_word="w0001"), settings=settings, prefer="elevenlabs")
    # repeated action sounds vary per cue; list markers stay identical
    c1 = resolve_sfx(job, SfxCue(id="fx010", kind="click", anchor_word="w0001"), settings=settings)
    c2 = resolve_sfx(job, SfxCue(id="fx011", kind="click", anchor_word="w0001"), settings=settings)
    t1 = resolve_sfx(job, SfxCue(id="fx012", kind="tick", anchor_word="w0001"), settings=settings)
    t2 = resolve_sfx(job, SfxCue(id="fx013", kind="tick", anchor_word="w0001"), settings=settings)
    assert c1.id != c2.id and t1.id == t2.id
    d1 = resolve_sfx(job, SfxCue(id="fx014", kind="ding", anchor_word="w0001"), settings=settings, music_key="A minor")
    d2 = resolve_sfx(job, SfxCue(id="fx015", kind="ding", anchor_word="w0001"), settings=settings, music_key="D major")
    assert d1.id != d2.id
    # a cue's own asset must carry a licence
    bare = AssetRef(kind="audio", source="local", path="assets/sfx/x.wav")
    with pytest.raises(SfxError):
        resolve_sfx(job, SfxCue(id="fx020", kind="pop", anchor_word="w0001", asset=bare), settings=settings)
    lic = bare.model_copy(update={"licence": Licence(name="creator-owned", source="creator")})
    assert resolve_sfx(job, SfxCue(id="fx021", kind="pop", anchor_word="w0001", asset=lic), settings=settings) is lic


# ============================================================================================ ElevenLabs
def _pcm16(x: np.ndarray) -> bytes:
    return (np.clip(np.atleast_2d(x).T, -1, 1) * 32767).astype("<i2").tobytes()


class _FakeSfx:
    def __init__(self, *, status: int = 200):
        self.status = status
        self.requests: list[httpx.Request] = []
        self.n = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers.get("xi-api-key") == KEY
        if request.url.path == "/v1/user/subscription":
            return httpx.Response(403, json={"detail": "missing permission"})
        assert request.url.path == "/v1/sound-generation"
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": f"bad request {KEY}"})
        body = json.loads(request.content)
        n = round(body["duration_seconds"] * SR)
        w = to_mono(synthesize("whoosh", seed=11).audio)
        x = np.zeros(n, dtype=np.float32)
        lead = round(0.02 * SR)
        x[lead:lead + w.size] = w[: n - lead]
        if self.n == 0:  # first candidate: smeared with a long reverb-like tail (should lose)
            tail = np.random.default_rng(0).standard_normal(n) * np.exp(-np.arange(n) / (0.9 * SR)) * 0.2
            x = x * 0.3 + (np.arange(n) > lead + round(0.3 * SR)) * tail
        self.n += 1
        return httpx.Response(200, content=_pcm16(x * 0.8), headers={"request-id": f"sfx{self.n}"})

    @property
    def gen_calls(self) -> int:
        return sum(1 for r in self.requests if r.url.path == "/v1/sound-generation")


def _client(fake: _FakeSfx) -> ElevenLabsSfxClient:
    return ElevenLabsSfxClient(KEY, transport=httpx.MockTransport(fake), sleep=lambda s: None)


def test_generate_sfx_request_selection_licence_cache(job: Job) -> None:
    fake = _FakeSfx()
    a = generate_sfx(job, "whoosh", n=2, client=_client(fake), detect_voice=False)
    assert fake.gen_calls == 2
    r = next(q for q in fake.requests if q.url.path == "/v1/sound-generation")
    assert r.method == "POST" and r.url.params["output_format"] == "pcm_48000"
    body = json.loads(r.content)
    assert body == {"text": FAMILIES["whoosh"].prompt, "duration_seconds": 0.55, "prompt_influence": 0.75,
                    "loop": False, "model_id": "eleven_text_to_sound_v2"}
    assert "no reverb tail" in body["text"] and "no voice" in body["text"]
    assert a.source == "elevenlabs" and a.id.startswith("elsfx_whoosh_")
    side = json.loads(job.path(f"assets/sfx/{a.id}.analysis.json").read_text())
    assert side["chosen"]["candidate"] == 1  # the clean one won by measurement
    assert len(side["candidates"]) == 2 and side["chosen"]["payload_channels"] == 1
    assert side["sync_mode"] == "peak"
    clip = load_sfx(job, a)
    assert clip.audio.shape[-1] / SR < 0.62  # leading silence trimmed, tail trimmed
    assert to_mono(clip.audio)[0] == pytest.approx(0.0, abs=1e-3)
    rec_txt = job.path(a.licence.record_path).read_text()
    rec = json.loads(rec_txt)
    assert rec["plan_tier"] is None and rec["request"]["body"] == body and KEY not in rec_txt
    assert a.licence.commercial_use and "could not be verified" in a.licence.notes
    again = generate_sfx(job, "whoosh", n=2, client=_client(fake), detect_voice=False)
    assert again.id == a.id and fake.gen_calls == 2
    assert KEY not in job.trace_path.read_text()


def test_auto_sourcing_with_key(job: Job, settings: Settings) -> None:
    fake = _FakeSfx()
    cl = _client(fake)
    w = resolve_sfx(job, SfxCue(id="fx001", kind="whoosh", anchor_word="w0001"), settings=settings, client=cl,
                    n_candidates=1)
    assert w.source == "elevenlabs"
    p = resolve_sfx(job, SfxCue(id="fx002", kind="pop", anchor_word="w0001"), settings=settings, client=cl)
    assert p.source == "procedural"  # synthesis is already ideal for UI pops
    # a failing API falls back to the procedural library for known families
    bad = _client(_FakeSfx(status=422))
    r = resolve_sfx(job, SfxCue(id="fx003", kind="riser", anchor_word="w0001"), settings=settings, client=bad)
    assert r.source == "procedural"
    assert any(t["event"] == "sfx_generation_failed" for t in job.read_trace())
    assert KEY not in job.trace_path.read_text()


def test_sfx_body_validation() -> None:
    with pytest.raises(ValueError):
        X._sfx_body("", duration_s=1.0, prompt_influence=0.5, loop=False, model_id=X.SFX_MODEL)
    with pytest.raises(ValueError):
        X._sfx_body("x", duration_s=0.2, prompt_influence=0.5, loop=False, model_id=X.SFX_MODEL)
    with pytest.raises(ValueError):
        X._sfx_body("x", duration_s=31.0, prompt_influence=0.5, loop=False, model_id=X.SFX_MODEL)
    with pytest.raises(ValueError):
        X._sfx_body("x", duration_s=1.0, prompt_influence=1.5, loop=False, model_id=X.SFX_MODEL)
    assert "duration_seconds" not in X._sfx_body("x", duration_s=None, prompt_influence=0.3, loop=False,
                                                 model_id=X.SFX_MODEL)


# ============================================================================================ levels
def _dialogue(n_s: float, spans: list[tuple[float, float]], amp: float = 0.1) -> np.ndarray:
    rng = np.random.default_rng(9)
    n = round(n_s * SR)
    x = rng.standard_normal(n) * 0.0003
    for a, b in spans:
        i, j = round(a * SR), round(b * SR)
        t = np.arange(j - i) / SR
        x[i:j] += rng.standard_normal(j - i) * amp * (0.55 + 0.45 * np.sin(2 * np.pi * 3.0 * t) ** 2)
    return x.astype(np.float32)


def test_recommend_level_transient_and_texture() -> None:
    spans = [(1.0, 4.0), (8.0, 11.0)]
    dia = _dialogue(12.0, spans)
    tick = synthesize("tick")
    adv = recommend_level("tick", tick, SR, at_s=2.0, dialogue=dia, speech_spans=spans)
    local_pk = peak_dbfs(dia[round(1.0 * SR):round(3.0 * SR)])
    assert adv.basis == "peak" and adv.overlaps_speech and not adv.assumed
    assert adv.target == pytest.approx(local_pk + FAMILIES["tick"].peak_under_speech_db, abs=0.05)
    assert peak_dbfs(tick.audio) + adv.gain_db == pytest.approx(adv.target, abs=0.01)
    wh = synthesize("whoosh")
    over = recommend_level("whoosh", wh, SR, at_s=2.5, dialogue=dia, speech_spans=spans)
    st = short_term_lufs_at(dia, SR, 2.5)
    assert over.basis == "loudness" and over.overlaps_speech
    assert over.target == pytest.approx(st - 10.0, abs=0.05)
    gap = recommend_level("whoosh", wh, SR, at_s=6.0, dialogue=dia, speech_spans=spans)
    assert not gap.overlaps_speech and gap.target == pytest.approx(gap.speech_ref - 6.0, abs=0.05)
    none = recommend_level("tick", tick, SR, at_s=2.0)
    assert none.assumed and none.target == pytest.approx(-3.0 - 6.0)
    trim = recommend_level("tick", tick, SR, at_s=2.0, dialogue=dia, speech_spans=spans, cue_gain_db=-15.0)
    assert trim.target == pytest.approx(adv.target - 3.0, abs=0.05)
    hot = recommend_level("tick", tick, SR, at_s=2.0, dialogue=dia, speech_spans=spans, cue_gain_db=6.0)
    assert hot.clamped and hot.target == pytest.approx(local_pk - 2.0, abs=0.05)
    assert SfxCue(id="fx001", kind="x", anchor_word="w0001").gain_db == DEFAULT_CUE_GAIN_DB


# ============================================================================================ render
def _timeline(sfx: list[TimelineSfx], duration: Fraction = Fraction(12)) -> Timeline:
    us = round(float(duration) * 1_000_000)
    seg = TimelineSegment(seg_id="seg001", src_in_us=0, src_out_us=us, out_start=0, out_end=duration,
                          audio_src_in_us=0, audio_src_out_us=us)
    words = {"w0001": WordSpan(out_start=Fraction(1), out_end=Fraction(4)),
             "w0002": WordSpan(out_start=Fraction(8), out_end=Fraction(11))}
    return Timeline(fps=30, duration=duration, segments=[seg], word_map=words, sfx=sfx)


def test_render_sfx_track_sync_levels_and_skips(job: Job, settings: Settings) -> None:
    sfx = [TimelineSfx(sfx_id="fx001", kind="tick", out_t=Fraction(2)),
           TimelineSfx(sfx_id="fx002", kind="whoosh", out_t=Fraction(6)),
           TimelineSfx(sfx_id="fx003", kind="riser", out_t=Fraction(9), gain_db=-15.0),
           TimelineSfx(sfx_id="fx004", kind="pop", out_t=Fraction(0)),
           TimelineSfx(sfx_id="fx005", kind="record scratch", out_t=Fraction(7))]
    tl = _timeline(sfx)
    dia = _dialogue(12.0, [(1.0, 4.0), (8.0, 11.0)])
    rep: list[dict] = []
    out = render_sfx_track(job, tl, dialogue=dia, settings=settings, report=rep)
    assert out.shape == (2, 12 * SR) and out.dtype == np.float32 and np.all(np.isfinite(out))
    assert float(np.max(np.abs(out))) < 1.0
    assert [r["sfx_id"] for r in rep] == ["fx004", "fx001", "fx002", "fx003"]  # unknown kind skipped
    assert any(t["event"] == "sfx_skipped" and t["sfx_id"] == "fx005" for t in job.read_trace())
    m = to_mono(out)
    # tick transient lands on its frame (sample-accurate)
    seg = np.abs(m[round(1.9 * SR):round(2.1 * SR)])
    onset = (round(1.9 * SR) + int(np.argmax(seg > 0.3 * seg.max()))) / SR
    assert abs(onset - 2.0) < 0.001
    # whoosh loudness peak on its cut
    e = _env(m[round(5.0 * SR):round(6.5 * SR)], 0.03)
    assert abs(5.0 + int(np.argmax(e)) / SR - 6.0) < 0.015
    # riser stops dead on the reveal
    r = np.abs(m[round(7.5 * SR):round(9.5 * SR)])
    last = (round(7.5 * SR) + int(np.nonzero(_env(r, 0.001) > 1e-3 * r.max())[0][-1])) / SR
    assert abs(last - 9.0) < 0.006
    rr = {x["sfx_id"]: x for x in rep}
    assert "trim" not in rr["fx003"]["note"]
    # tick peaks ~6 dB under the local speech peak
    tick_pk = peak_dbfs(out[:, round(1.99 * SR):round(2.15 * SR)])
    assert tick_pk == pytest.approx(peak_dbfs(dia[round(1.0 * SR):round(3.0 * SR)]) - 6.0, abs=0.6)
    mono = render_sfx_track(job, tl, dialogue=dia, settings=settings, channels=1)
    assert mono.shape == (12 * SR,)
    spans = sfx_duck_spans(job, tl, settings=settings)
    assert len(spans) == 4 and all(e_ > s_ and d == 3.0 for s_, e_, d in spans)
    assert any(s_ <= 6.0 <= e_ for s_, e_, _ in spans)


def test_render_sfx_track_empty(job: Job) -> None:
    out = render_sfx_track(job, _timeline([]))
    assert out.shape == (2, 12 * SR) and not np.any(out)


def test_procedural_sfx_direct(job: Job) -> None:
    a = procedural_sfx(job, "impact")
    b = procedural_sfx(job, "impact", duration_s=1.0)
    assert a.id != b.id and b.duration_ms == 1000
    with pytest.raises(SfxError):
        procedural_sfx(job, "banana")


# ============================================================================================ real
@pytest.mark.real
def test_real_elevenlabs_whoosh(tmp_path: Path) -> None:
    from studio.config import get_settings

    s = get_settings()
    if not s.has_key("elevenlabs"):
        pytest.skip("no ElevenLabs key")
    job = Job.create("real-sfx", work_dir=tmp_path)
    a = generate_sfx(job, "whoosh", n=1, settings=s)
    side = json.loads(job.path(f"assets/sfx/{a.id}.analysis.json").read_text())
    clip = load_sfx(job, a)
    print("\nreal whoosh:", side["chosen"], "| sync", side["sync_s"], "| duration", side["duration_s"])
    assert a.source == "elevenlabs" and a.licence is not None and job.path(a.licence.record_path).exists()
    assert 0.2 <= clip.duration_s <= 1.5
    assert peak_dbfs(clip.audio) == pytest.approx(-1.0, abs=0.1)
    assert 0.0 <= clip.sync_s <= clip.duration_s
    assert side["chosen"]["payload_s"] == pytest.approx(0.55, abs=0.1)
