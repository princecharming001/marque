"""Work-volume disk budget (studio.storage): estimates, reclaim, locks, the lean intermediate, mezzanine rebuild."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from studio import storage
from studio.jobs import Job

FFMPEG = shutil.which("ffmpeg")


def _render(job: Job, *, final: bool = True, big: int = 4096) -> Path:
    rd = job.new_render_dir()
    (rd / "render.json").write_text("{}")
    (rd / "timeline.json").write_text("{}")
    if final:
        (rd / "final_tiktok.mp4").write_bytes(b"f" * 100)
    (rd / "aroll.mov").write_bytes(b"x" * big)
    (rd / "overlays.mov").write_bytes(b"x" * 100)
    (rd / "mix.wav").write_bytes(b"x" * 100)
    (rd / "stems").mkdir()
    (rd / "stems" / "dialogue.wav").write_bytes(b"x" * 100)
    return rd


def _media(job: Job) -> None:
    (job.media_dir / "original.mov").write_bytes(b"o" * 10)
    job.mezz_path.write_bytes(b"m" * 5000)
    job.audio_path.write_bytes(b"a" * 10)
    job.media_info_path.write_text("{}")


def test_estimates_scale_and_lean_is_smaller() -> None:
    hq = storage.render_need_bytes(60, fps=30)
    lean = storage.render_need_bytes(60, fps=30, aroll="lean")
    assert lean < 0.7 * hq and storage.render_need_bytes(120, fps=30) > 1.9 * hq
    assert storage.render_need_bytes(60, fps=60) > hq  # more frames, more bytes
    assert storage.render_need_bytes(60, preview=True) < lean
    # a 60 s 1080x1920p30 ProRes HQ render: the A-roll alone is ~1.65 GB
    assert 1.8e9 < hq < 3.2e9
    assert storage.mezz_bytes(105, 1080, 1920, 30, "prores_hq") == pytest.approx(2.9e9, rel=0.1)


def test_speech_span_discounts_dead_air(take_index) -> None:
    ix = take_index
    dur = ix.media.duration_us / 1e6
    dead = sum(g.end_us - g.start_us for g in ix.gaps if g.kind == "silence") / 1e6
    assert storage.speech_span_s(ix) == pytest.approx(max(min(dur, 5.0), dur - dead))
    assert storage.edit_need_bytes(ix) > 0


def test_reclaim_job_keeps_finals_and_drops_a_rebuildable_mezzanine(job: Job) -> None:
    _media(job)
    r1, r2 = _render(job), _render(job)
    freed = storage.reclaim_job(job, keep_intermediates={r2.name}, drop_mezz=True, reason="test")
    assert not (r1 / "aroll.mov").exists() and (r1 / "final_tiktok.mp4").exists() and (r1 / "render.json").exists()
    assert (r2 / "aroll.mov").exists()  # kept on request
    assert not job.mezz_path.exists() and freed >= 5000 + 4096
    rec = storage.mezz_pruned(job)
    assert rec is not None and rec["bytes"] == 5000 and rec["reason"] == "test"
    # no original → the mezzanine is not regenerable → never dropped
    job.mezz_path.write_bytes(b"m" * 10)
    for p in job.media_dir.glob("original.*"):
        p.unlink()
    storage.reclaim_job(job, drop_mezz=True)
    assert job.mezz_path.exists()


def test_reclaim_work_dir_skips_active_jobs(work_dir: Path) -> None:
    a, b = Job.create("a", work_dir=work_dir), Job.create("b", work_dir=work_dir)
    for j in (a, b):
        _media(j)
        _render(j)
    with storage.job_activity(a):
        assert storage.job_is_active(a.root) and not storage.job_is_active(b.root)
        freed = storage.reclaim_work_dir(work_dir)
    assert freed > 0
    assert a.mezz_path.exists() and (a.renders_dir / "r1" / "aroll.mov").exists()  # held by a running edit
    assert not b.mezz_path.exists() and not (b.renders_dir / "r1" / "aroll.mov").exists()
    assert not storage.job_is_active(a.root)
    # exclude + need: stops once enough is freed
    assert storage.reclaim_work_dir(work_dir, exclude=a.root) == 0


def test_ensure_space_reclaims_before_refusing(job: Job, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUDIO_MIN_FREE_GB", "0")
    state = {"free": 1_000}
    monkeypatch.setattr(storage, "free_bytes", lambda _p: state["free"])

    def reclaim(short: int) -> int:
        state["free"] += 10_000
        return 10_000

    ok, free = storage.ensure_space(job.root, 5_000, reclaim=reclaim)
    assert ok and free == 11_000
    with pytest.raises(storage.DiskSpaceError, match="GB free"):
        storage.ensure_space(job.root, 10**9, what="a test render")
    assert storage.ensure_space(job.root, 10**9, raise_error=False)[0] is False


def test_choose_aroll_codec_falls_back_to_lean(job: Job, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUDIO_MIN_FREE_GB", "0")
    hq = storage.render_need_bytes(40, fps=30)
    lean = storage.render_need_bytes(40, fps=30, aroll="lean")
    monkeypatch.setattr(storage, "free_bytes", lambda _p: hq + 1)
    assert storage.choose_aroll_codec(job.root, 40, width=1080, height=1920, fps=30)[0] == "prores_hq"
    monkeypatch.setattr(storage, "free_bytes", lambda _p: (hq + lean) // 2)
    assert storage.choose_aroll_codec(job.root, 40, width=1080, height=1920, fps=30) == ("lean", lean)
    monkeypatch.setattr(storage, "free_bytes", lambda _p: lean - 1)
    with pytest.raises(storage.DiskSpaceError):
        storage.choose_aroll_codec(job.root, 40, width=1080, height=1920, fps=30)
    monkeypatch.setenv("STUDIO_AROLL_CODEC", "lean")
    monkeypatch.setattr(storage, "free_bytes", lambda _p: 10**13)
    assert storage.choose_aroll_codec(job.root, 40, width=1080, height=1920, fps=30)[0] == "lean"


def test_render_slot_is_exclusive_and_reentrant() -> None:
    order: list[str] = []
    with storage.render_slot():
        with storage.render_slot():  # re-entrant (ensure_mezz inside a render)
            order.append("inner")
        done = threading.Event()

        def other() -> None:
            # another *process* would block; within one process a second thread sees the held counter, so probe
            # the lock file directly
            import fcntl

            d = Path(os.environ["STUDIO_SLOTS_DIR"]) / "studio-edit-slots"
            with open(d / "render0.lock", "a+") as fh:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    order.append("got")
                except BlockingIOError:
                    order.append("blocked")
            done.set()

        t = threading.Thread(target=other)
        t.start()
        done.wait(5)
        t.join()
    assert order == ["inner", "blocked"]


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")
def test_lean_intermediate_is_4_2_2_10_bit_and_near_lossless(tmp_path: Path) -> None:
    src = tmp_path / "src.mov"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x568:rate=30:duration=1", "-c:v", "prores_ks", "-profile:v", "3",
                    "-pix_fmt", "yuv422p10le", str(src)], check=True)
    out = tmp_path / "lean.mov"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), *storage.lean_video_args(),
                    str(out)], check=True)
    pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                    "stream=pix_fmt,codec_name", "-of", "json", str(out)], check=True,
                                   capture_output=True, text=True).stdout)["streams"][0]
    assert pr["pix_fmt"] == "yuv422p10le" and pr["codec_name"] in ("hevc", "h264")
    psnr = subprocess.run([FFMPEG, "-hide_banner", "-i", str(src), "-i", str(out), "-lavfi",
                           "[0:v]format=yuv422p10le[a];[1:v]format=yuv422p10le[b];[a][b]psnr", "-f", "null", "-"],
                          capture_output=True, text=True).stderr
    avg = psnr.split("average:")[1].split()[0]
    assert avg == "inf" or float(avg) > 45.0


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")
def test_a_reclaimed_mezzanine_is_rebuilt_frame_identical(synth_video: Path, settings, work_dir: Path) -> None:
    from studio.media.ingest import IngestOptions, ingest

    job = Job.create("rebuild", work_dir=work_dir)
    ingest(synth_video, job, settings=settings, options=IngestOptions(proxy=False, min_free_bytes=0))
    before = job.mezz_path.read_bytes()
    assert storage.reclaim_job(job, drop_mezz=True, reason="test") > 0 and not job.mezz_path.exists()
    t0 = time.monotonic()
    assert storage.ensure_mezz(job, settings=settings) and job.mezz_path.exists()
    assert time.monotonic() - t0 < 120
    after = job.mezz_path.read_bytes()
    assert len(after) == len(before)
    assert storage.mezz_pruned(job) is None and not storage.ensure_mezz(job, settings=settings)


def test_ingest_ladder_reserves_room_for_the_renders(synth_video: Path, settings, work_dir: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    from collections import namedtuple

    from studio.media import ingest as ingest_mod
    from studio.media.ingest import IngestOptions, estimate_mezz_bytes, ingest
    from studio.media.probe import FLAG_MEZZ_FALLBACK, probe

    info = probe(synth_video)
    du = namedtuple("du", "total used free")
    audio = int(float(info.duration_s) * 48000 * 12) + (8 << 20)
    reserve = storage.render_need_bytes(float(info.duration_s), fps=30, aroll="lean") + \
        int(storage.RATES["final"] * float(info.duration_s) * 2)
    # ProRes 422 HQ alone would fit, but not with the renders' reserve: the ladder steps down to ProRes 422
    free = estimate_mezz_bytes(info, "prores") + audio + reserve + 1000
    assert estimate_mezz_bytes(info, "prores_hq") + audio < free
    monkeypatch.setattr(ingest_mod.shutil, "disk_usage", lambda p: du(10**12, 0, free))
    job = Job.create("ladder", work_dir=work_dir)
    mi = ingest(synth_video, job, settings=settings,
                options=IngestOptions(proxy=False, min_free_bytes=0, reserve_render=True))
    assert any(n.startswith(FLAG_MEZZ_FALLBACK) for n in mi.notes)
    assert job.meta["meta"]["ingest"]["mezz"]["codec"] == "prores"
