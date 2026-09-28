"""End-to-end integration smoke test (keyless, offline): every engine stage on a 6 s synthetic take.

``studio index`` (ingest + real refine/gaps/takes/prosody/audio metrics/visual; only the ASR call is replaced
by the synthetic ground truth) → Director-style edit through :class:`studio.agent.tools.EditSession` (ops
only: story with a false start and a filler removed, a shortened breath pause, punch-in, hook title, card,
creator b-roll split, SFX, imported music bed, colour, pins) → compile → ``studio render`` at full quality
(A-roll, Remotion overlays when the overlay project is installed, audio, master) → ``studio qa`` (all ten
invariants) → ``studio report``. The CLI runs in-process with ``--json``. The point is that the modules
genuinely fit together; each module's own tests cover its craft. ``STUDIO_SMOKE_KEEP=1`` keeps the job dir.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from gaps_prosody_synth import SR, build_take

from studio.compile import overlays as overlays_mod
from studio.compile.models import Timeline
from studio.compile.timeline import compile as compile_timeline
from studio.config import get_settings
from studio.doc.model import Licence
from studio.jobs import Job
from studio.perception import transcribe as transcribe_mod
from studio.perception.index import AsrInfo, AsrResult, Word, word_id

pytestmark = pytest.mark.slow

FFMPEG = shutil.which("ffmpeg")
DURATION_S = 6.0

# (text, start, dur, f0, dB, kind, tail) — a false start, its clean retake, a breath pause, a filler.
_SCRIPT = [
    ("Most", 0.25, 0.25, 130, -20, "word", None),
    ("people", 0.52, 0.30, 125, -21, "word", None),
    ("cut—", 0.85, 0.23, 128, -21, "word", None),  # interrupted: the retake follows
    ("Most", 1.40, 0.25, 132, -20, "word", None),
    ("people", 1.67, 0.30, 126, -20, "word", None),
    ("cut", 2.00, 0.25, 124, -20, "word", None),
    ("too", 2.28, 0.18, 130, -19, "word", None),
    ("much.", 2.48, 0.30, 150, -17, "word", (0.08, -27)),  # payoff: louder, higher
    ("Keep", 3.45, 0.27, 128, -20, "word", None),
    ("the", 3.74, 0.12, 122, -22, "word", None),
    ("pauses", 3.89, 0.41, 126, -20, "word", (0.07, -27)),
    ("um", 4.62, 0.28, 110, -26, "filler", None),
    ("that", 5.05, 0.20, 128, -20, "word", None),
    ("matter.", 5.27, 0.28, 120, -20, "word", (0.07, -28)),
]
_BREATHS = [(3.12, 0.30)]  # pre-onset inhale before "Keep" (inside the kept, shortened pause)
_ASR_JITTER_S = [(0.02, -0.015), (-0.01, 0.02), (0.015, 0.0), (-0.02, 0.01), (0.0, -0.02), (0.01, 0.015),
                 (-0.015, 0.0), (0.02, -0.02), (-0.01, 0.01), (0.0, 0.0), (0.015, -0.01), (0.0, 0.02),
                 (-0.02, 0.0), (0.01, -0.015)]


# ============================================================================================ media
def _synth_take() -> tuple[np.ndarray, list[Word]]:
    events = []
    for text, t, dur, f0, db, _kind, tail in _SCRIPT:
        ev = {"type": "word", "text": text, "t": t, "dur": dur, "f0": f0, "db": db}
        if tail:
            ev["tail"] = tail
        events.append(ev)
    events += [{"type": "breath", "t": t, "dur": d, "db": -38} for t, d in _BREATHS]
    take = build_take(events, total_s=DURATION_S, floor_db=-60.0)
    words = []
    for i, ((text, _t, _d, _f0, _db, kind, _tail), tw) in enumerate(zip(_SCRIPT, take.words, strict=True)):
        ds, de = _ASR_JITTER_S[i]  # ASR word edges are never exact: refinement must fix them
        s = max(0.0, tw.start_s + ds)
        words.append(Word(id=word_id(i + 1), text=text, start_us=round(s * 1e6),
                          end_us=round(max(s + 0.05, tw.end_s + de) * 1e6), kind=kind,  # type: ignore[arg-type]
                          confidence=0.98 if kind == "word" else 0.85, speaker="S1"))
    return take.x.astype(np.float32), words


def _write_source(dst: Path, audio: np.ndarray) -> Path:
    wav = dst.with_suffix(".wav")
    sf.write(wav, audio, SR, subtype="PCM_24")
    # a smooth moving gradient (no face): exercises the no-face framing/caption fallbacks cheaply
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"gradients=s=1080x1920:r=30:d={DURATION_S}:speed=0.015:n=3:seed=7",
                    "-i", str(wav), "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-crf", "14",
                    "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709",
                    "-colorspace", "bt709", "-c:a", "pcm_s24le", "-shortest", str(dst)], check=True)
    wav.unlink()
    return dst


def _write_still(path: Path) -> Path:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1350, 1080), (38, 70, 120))
    d = ImageDraw.Draw(img)
    for k in range(12):
        d.rectangle([80 + 90 * k, 200 + 25 * k, 150 + 90 * k, 880 - 20 * k], fill=(200 - 12 * k, 140, 60 + 14 * k))
    d.ellipse([500, 300, 850, 650], fill=(240, 220, 90))
    img.save(path)
    return path


def _write_music(path: Path, seconds: float = 9.0, bpm: float = 100.0) -> Path:
    """A plain 4/4 bed (kick, pad chords changing per bar, a final hit) at about -20 dBFS."""
    n = round(seconds * SR)
    t = np.arange(n) / SR
    beat = 60.0 / bpm
    x = np.zeros(n)
    chords = [(261.63, 329.63, 392.0), (220.0, 261.63, 329.63), (174.61, 220.0, 261.63), (196.0, 246.94, 293.66)]
    bar = 4 * beat
    for b in range(int(seconds / bar) + 1):
        a, e = round(b * bar * SR), min(n, round((b + 1) * bar * SR))
        if a >= n:
            break
        tt = t[a:e] - t[a]
        env = np.minimum(1.0, tt / 0.05) * np.exp(-tt / 3.0)
        x[a:e] += sum(0.12 * np.sin(2 * np.pi * f * t[a:e]) for f in chords[b % 4]) * env
    for k in range(int(seconds / beat)):
        a = round(k * beat * SR)
        m = min(n - a, round(0.25 * SR))
        tt = np.arange(m) / SR
        x[a:a + m] += 0.5 * np.sin(2 * np.pi * (50 + 60 * np.exp(-tt / 0.03)) * tt) * np.exp(-tt / 0.08)
    x *= 10 ** (-20 / 20) / (np.sqrt(np.mean(x ** 2)) + 1e-12)
    st = np.stack([x, np.roll(x, 7)], axis=1)
    sf.write(path, st.astype(np.float32), SR, subtype="PCM_24")
    return path


# ============================================================================================ helpers
def _overlay_ready() -> bool:
    root = get_settings().overlay_dir
    return (root / "node_modules" / "@remotion" / "renderer").is_dir() and shutil.which("node") is not None


def _gap_between(index, left: str, right: str) -> str:
    g = next(g for g in index.gaps if g.after_word_id == left and g.before_word_id == right)
    return g.id


def _apply(session, ops: list[dict]) -> None:
    out = session.apply_ops(ops)
    rejected = [r for r in out.results if not r.applied]
    assert not rejected, "ops rejected:\n" + "\n".join(f"{r.op}: {r.reason}" for r in rejected)


# ============================================================================================ the test
def _cli(capsys: pytest.CaptureFixture[str], work: Path, *argv: str) -> dict:
    """Run ``studio --json <argv>`` in-process and return its result object."""
    from studio import cli

    capsys.readouterr()
    rc = cli.main(["--work-dir", str(work), "--json", *argv])
    out, err = capsys.readouterr()
    assert rc == 0, f"studio {' '.join(argv)} exited {rc}: {err[-2000:]}"
    return json.loads(out)["result"]


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")
def test_smoke_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    from studio.agent.tools import EditSession
    from studio.audio.music import import_music_file
    from studio.broll.sources import creator_media
    from studio.media.models import MediaInfo

    work = tmp_path / "work"
    job: Job | None = None
    try:
        # ---------------------------------------------------------------- `studio index` (ASR = ground truth)
        audio, asr_words = _synth_take()
        src = _write_source(tmp_path / "take.mov", audio)

        def fake_transcribe(job_, **kw):
            return AsrResult(words=[w.model_copy() for w in asr_words],
                             asr=AsrInfo(provider="fixture", model="synthetic-truth", language="en"), language="en")

        monkeypatch.setattr(transcribe_mod, "transcribe", fake_transcribe)
        res = _cli(capsys, work, "index", str(src))
        job = Job.open(res["job_dir"])
        src.unlink()
        info: MediaInfo = job.load_media_info()
        assert (info.width, info.height, info.fps) == (1080, 1920, Fraction(30))
        for p in (job.mezz_path, job.audio_path, job.proxy_path, job.index_path):
            assert p.exists(), p
        index = job.load_index()
        assert res["words"] == len(_SCRIPT) and [w.text for w in index.words] == [s[0] for s in _SCRIPT]
        assert index.words[11].kind == "filler"
        truth = {word_id(i + 1): (t, t + d) for i, (_x, t, d, *_r) in enumerate(_SCRIPT)}
        for w in index.words:  # acoustic refinement pulled the jittered ASR edges back to the signal
            assert abs(w.start_us / 1e6 - truth[w.id][0]) < 0.03, (w.id, w.start_us)
        breath_gap = _gap_between(index, "w0008", "w0009")
        g = index.gap(breath_gap)
        assert g.has_breath and g.breaths_us, g
        b0, b1 = g.breaths_us[0]
        assert abs(b0 / 1e6 - _BREATHS[0][0]) < 0.06 and abs(b1 / 1e6 - sum(_BREATHS[0])) < 0.06, g.breaths_us
        assert len(index.sentences) >= 3 and index.audio is not None and index.energy is not None
        assert index.visual is not None and index.visual.samples

        # ---------------------------------------------------------------- assets (keyless, licensed)
        still = creator_media(_write_still(tmp_path / "chart.png"), job=job, description="bar chart")[0].asset
        music = import_music_file(job, _write_music(tmp_path / "bed.wav"),
                                  licence=Licence(name="creator-owned", source="creator", holder="fixture"),
                                  source="creator", bpm_hint=100)

        # ---------------------------------------------------------------- the edit (ops only, as the Director)
        session = EditSession(job, index, platforms=("tiktok",))
        _apply(session, [
            {"op": "set_brief", "brief": {"goal": "one editing tip", "cta": "keep the pauses that matter"}},
            {"op": "set_story", "reason": "retake chosen, filler removed", "segments": [
                {"from_word": "w0004", "to_word": "w0011"},
                {"from_word": "w0013", "to_word": "w0014"},
            ], "removed": [{"from_word": "w0001", "to_word": "w0003", "reason": "false start"},
                           {"from_word": "w0012", "to_word": "w0012", "reason": "filler"}]},
        ])
        _apply(session, [
            {"op": "set_gap", "gap_id": breath_gap, "ms": 300},
            {"op": "set_framing", "seg_id": "seg002", "scale": 1.2, "ease": "cut"},
            {"op": "pin", "word_ids": ["w0008"], "kind": "payoff"},
            {"op": "pin", "word_ids": ["w0014"], "kind": "cta"},
            {"op": "add_text", "kind": "hook_title", "text": "Stop over-editing", "anchor_from_word": "w0004",
             "anchor_to_word": "w0006", "job": "hook title"},
            {"op": "add_insert", "anchor_from_word": "w0009", "anchor_to_word": "w0011", "mode": "split_top",
             "asset_id": still.id, "job": "show the pause chart while the rule is said"},
            {"op": "add_insert", "anchor_from_word": "w0013", "anchor_to_word": "w0014", "mode": "card",
             "card": {"template": "quote", "title": "Keep the pauses that matter"},
             "job": "make the rule visible"},
            {"op": "add_sfx", "kind": "pop", "anchor_word": "w0008", "at": "end", "job": "land the payoff"},
            {"op": "set_music", "spec": {"asset_id": music.asset_id, "source": "creator"}},
            {"op": "set_voice_chain", "spec": {"breath_atten_db": 6.0}},
            {"op": "set_color", "spec": {"exposure": 0.1, "contrast": 1.05, "saturation": 1.05}},
        ])
        doc = session.doc
        assert doc.version >= 2 and len(doc.segments) == 2 and len(doc.inserts) == 2
        assert session.q_transcript("cut") and session.q_overview()

        # ---------------------------------------------------------------- compile
        tl = compile_timeline(doc, index, job=job)
        assert tl.frame_count > 0 and tl.duration < Fraction(5)
        assert len(tl.seams) >= 2  # the trimmed breath pause and the filler cut
        assert not tl.grid_violations()
        assert tl.captions and tl.texts and tl.sfx and tl.music is not None
        assert {i.mode for i in tl.inserts} == {"split_top", "card"}
        assert all(tl.word_span(w) is not None for w in ("w0008", "w0014"))
        assert tl.word_span("w0012") is None and tl.word_span("w0001") is None

        # ---------------------------------------------------------------- `studio render` (full quality)
        if not _overlay_ready():
            monkeypatch.setattr(overlays_mod, "render_overlays", lambda *a, **k: None)
        res = _cli(capsys, work, "render", str(job.root))
        rd = Path(res["render_dir"])
        assert Path(res["finals"]["tiktok"]) == rd / "final_tiktok.mp4"
        names = ["timeline.json", "aroll.mov", "mix.wav", "mix_nomusic.wav", "final_tiktok.mp4",
                 "final_nomusic.mp4", "cover.jpg", "captions.srt", "render.json", "audio_report.json"]
        if _overlay_ready():
            names += ["overlays.mov", "overlay_props.json"]
        for name in names:
            assert (rd / name).exists(), name
        saved = Timeline.load(rd / "timeline.json")
        assert saved.frame_count == tl.frame_count
        # the render uses the compiler's caption placement verbatim (no second placement pass)
        assert [c.model_dump() for c in saved.captions] == [c.model_dump() for c in tl.captions]
        rep = json.loads((rd / "audio_report.json").read_text())
        assert not rep["warnings"], rep["warnings"]
        assert rep["music"]["source"] == "studio.audio.music"  # fitted + ducked bed, not the raw fallback
        assert rep["sfx"] and rep["sfx"][0]["sfx_id"] == "fx001"
        for seam in rep["seams"]:  # the removed side of every seam is room tone (no chopped breath/word)
            lv = seam["levels_db"]
            assert max(lv["a_post"], lv["b_pre"]) < lv["floor"] + 10, seam

        # ---------------------------------------------------------------- `studio qa` (keyless: no ASR)
        res = _cli(capsys, work, "qa", str(job.root))
        failed = [r for r in res["results"] if not r["passed"]]
        assert res["passed"] and len(res["results"]) == 10, "\n".join(
            f"#{r['number']} {r['name']}: {r['detail']}" for r in failed)

        # ---------------------------------------------------------------- `studio report`
        report = Path(_cli(capsys, work, "report", str(job.root)))
        text = report.read_text()
        assert "final_tiktok.mp4" in text and "w0008" in text
    finally:
        if job is not None and os.environ.get("STUDIO_SMOKE_KEEP") != "1":  # set to keep the job dir
            shutil.rmtree(job.renders_dir, ignore_errors=True)  # full-resolution intermediates are large
            (job.media_dir / "mezz.mov").unlink(missing_ok=True)
        if job is not None:
            print(f"smoke job dir: {job.root}")
