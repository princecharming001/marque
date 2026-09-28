"""Tests for studio.media.probe and studio.media.ingest (ARCHITECTURE §3).

Pure-function tests use hand-built ffprobe dicts. Media tests run the full ingest on small synthetic
sources (conftest ``synth_*`` fixtures plus a few built here: HLG round-trip, stereo, A/V offset,
landscape, 4K60 HEVC). Each module-scoped job directory is deleted at teardown (mezzanines are large).
Slow tests ingest the real QA takes in /Users/home/studio-testdata.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from collections import namedtuple
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from studio.config import Settings
from studio.jobs import Job
from studio.media import ingest as ingest_mod
from studio.media.ingest import (
    IngestError,
    IngestOptions,
    ToneMapConfig,
    dialogue_downmix,
    estimate_mezz_bytes,
    ingest,
    make_proxy,
    mezz_codec_args,
    mezz_video_filter,
    proxy_size,
    stereo_audio_path,
    verify_ingest,
)
from studio.media.models import AudioInfo, ColorInfo, MediaInfo
from studio.media.probe import (
    FLAG_DV_UNSUPPORTED,
    FLAG_HDR_TONEMAPPED,
    FLAG_LANDSCAPE,
    FLAG_MEZZ_FALLBACK,
    FLAG_NEEDS_REFRAME,
    FLAG_NO_AUDIO,
    FLAG_VFR,
    ProbeError,
    decide_fps,
    ffprobe_json,
    has_flag,
    hdr_peak_nits,
    media_info_from_probe,
    parse_mp4_edit_lists,
    probe,
    rotation_from_stream,
    select_audio_stream,
    select_video_stream,
    timing_summary,
)
from studio.timebase import frame_to_sample

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")
TESTDATA = Path("/Users/home/studio-testdata")


# ============================================================================================ helpers
def _ff(args: list[str]) -> None:
    subprocess.run([FFMPEG or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _vstream(path: Path) -> dict[str, Any]:
    st = ffprobe_json(path)["streams"]
    return next(s for s in st if s["codec_type"] == "video")


def _gray_frame(path: Path, n: int, w: int, h: int, *, noautorotate: bool = False,
                vf_extra: str = "") -> np.ndarray:
    args = [FFMPEG or "ffmpeg", "-hide_banner", "-loglevel", "error"]
    if noautorotate:
        args.append("-noautorotate")
    # raw luma codes (yuv444p keeps the signal range; a direct "gray" conversion would expand tv range)
    vf = f"select=eq(n\\,{n}){vf_extra},format=yuv444p,extractplanes=y"
    args += ["-i", str(path), "-vf", vf, "-frames:v", "1", "-f", "rawvideo", "-"]
    raw = subprocess.run(args, check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, w).astype(np.float64)


def _rgb_frame(path: Path, n: int, w: int, h: int) -> np.ndarray:
    """Frame ``n`` as float RGB 0..255 via zimg (BT.709 limited → RGB, exact transfer)."""
    vf = (f"select=eq(n\\,{n}),zscale=min=bt709:tin=bt709:pin=bt709:rin=tv:m=bt709:t=bt709:p=bt709:r=tv"
          ":agamma=0,format=rgb48le")
    raw = subprocess.run([FFMPEG or "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vf", vf,
                          "-frames:v", "1", "-f", "rawvideo", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype="<u2").reshape(h, w, 3).astype(np.float64) / 65535.0 * 255.0


def _stream(**kw: Any) -> dict[str, Any]:
    base = {"index": 0, "codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080,
            "pix_fmt": "yuv420p", "r_frame_rate": "30/1", "avg_frame_rate": "30/1", "time_base": "1/600",
            "start_time": "0.000000", "duration": "10.000000", "disposition": {"default": 1},
            "color_range": "tv", "color_space": "bt709", "color_transfer": "bt709", "color_primaries": "bt709"}
    base.update(kw)
    return base


def _astream(**kw: Any) -> dict[str, Any]:
    base = {"index": 1, "codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 1,
            "channel_layout": "mono", "time_base": "1/48000", "start_time": "0.000000", "duration": "10.000000",
            "disposition": {"default": 1}, "bit_rate": "128000"}
    base.update(kw)
    return base


def _probe(streams: list[dict[str, Any]], studio: dict[str, Any] | None = None,
           fmt: str = "mov,mp4,m4a,3gp,3g2,mj2") -> dict[str, Any]:
    d: dict[str, Any] = {"streams": streams, "format": {"format_name": fmt, "duration": "10.000000",
                                                        "start_time": "0.000000"}}
    if studio is not None:
        d["studio"] = studio
    return d


def _cfr_timing(n: int, delta: int, tb: str, jitter: list[int] | None = None) -> dict[str, Any]:
    pts, t = [], 0
    for i in range(n):
        pts.append(t)
        t += delta + (jitter[i % len(jitter)] if jitter else 0)
    return timing_summary(pts, tb, last_duration=delta)


# ============================================================================================ pure: probe
class TestStreamSelection:
    def test_video_skips_cover_art(self):
        cover = _stream(index=0, codec_name="mjpeg", disposition={"attached_pic": 1})
        main = _stream(index=1, disposition={"default": 0})
        assert select_video_stream([cover, main])["index"] == 1

    def test_audio_never_apac_prefers_default_aac(self):
        apac = _astream(index=1, codec_name="apac", channels=4, disposition={"default": 1}, bit_rate="900000")
        aac = _astream(index=2, codec_name="aac", channels=2, disposition={"default": 0})
        data = _astream(index=3, codec_type="data", codec_name="none")
        assert select_audio_stream([_stream(), apac, aac, data])["index"] == 2
        aac_def = _astream(index=4, disposition={"default": 1})
        assert select_audio_stream([aac, aac_def])["index"] == 4

    def test_no_audio(self):
        assert select_audio_stream([_stream()]) is None


class TestRotation:
    @pytest.mark.parametrize(("ccw", "cw"), [(-90, 90), (90, 270), (180, 180), (-180, 180), (0, 0), (270, 90)])
    def test_display_matrix(self, ccw, cw):
        s = _stream(side_data_list=[{"side_data_type": "Display Matrix", "rotation": ccw}])
        assert rotation_from_stream(s) == (cw, False)

    def test_legacy_rotate_tag_is_clockwise(self):
        assert rotation_from_stream(_stream(tags={"rotate": "90"})) == (90, False)

    def test_mirror_detected(self):
        # a pure horizontal flip: ffprobe reports "rotation -180", but nothing is rotated
        mat = ("\n00000000:       -65536           0           0\n00000001:            0       65536           0"
               "\n00000002:            0           0  1073741824\n")
        s = _stream(side_data_list=[{"side_data_type": "Display Matrix", "displaymatrix": mat, "rotation": -180}])
        assert rotation_from_stream(s) == (0, True)

    def test_iphone_matrix_not_mirrored(self):
        mat = ("\n00000000:            0       65536           0\n00000001:       -65536           0           0"
               "\n00000002:     70778880           0  1073741824\n")
        s = _stream(side_data_list=[{"side_data_type": "Display Matrix", "displaymatrix": mat, "rotation": -90}])
        assert rotation_from_stream(s) == (90, False)


class TestFrameRate:
    def test_cfr_30(self):
        fps, vfr, _ = decide_fps(_cfr_timing(300, 512, "1/15360"), Fraction(30), Fraction(30))
        assert (fps, vfr) == (Fraction(30), False)

    def test_iphone_jitter_is_cfr_30(self):
        t = _cfr_timing(1216, 20, "1/600", jitter=[0] * 50 + [1, -1])
        fps, vfr, _ = decide_fps(t, Fraction(30), Fraction(243200, 8107))
        assert (fps, vfr) == (Fraction(30), False)

    def test_ntsc_is_exact(self):
        fps, vfr, _ = decide_fps(_cfr_timing(600, 1001, "1/30000"), Fraction(30000, 1001), None)
        assert (fps, vfr) == (Fraction(30000, 1001), False)

    def test_ntsc_in_coarse_timebase(self):
        # 29.97 in a 1/600 timebase: mostly 20 ticks, one 21 every ~50 frames
        pts, t, acc = [], Fraction(0), Fraction(0)
        for _ in range(1500):
            pts.append(int(t))
            acc += Fraction(600 * 1001, 30000)
            t = acc.__floor__()
        fps, vfr, _ = decide_fps(timing_summary(pts, "1/600"), Fraction(30000, 1001), None)
        assert (fps, vfr) == (Fraction(30000, 1001), False)

    def test_single_dropped_frame_stays_cfr(self):
        pts = [i * 512 for i in range(900) if i != 450]
        fps, vfr, _ = decide_fps(timing_summary(pts, "1/15360"), Fraction(30), None)
        assert (fps, vfr) == (Fraction(30), False)

    def test_phone_low_light_vfr_conforms_to_nominal(self):
        # the real var-vfr take: 1/30, 1/24 and 1/15 s frames in a 1/19200 timebase
        deltas = [640] * 360 + [800] * 288 + [1280] * 179
        rng = np.random.default_rng(1)
        rng.shuffle(deltas)
        pts = [403 + int(x) for x in np.concatenate([[0], np.cumsum(deltas)])]
        fps, vfr, reason = decide_fps(timing_summary(pts, "1/19200"), Fraction(30), Fraction(23))
        assert (fps, vfr) == (Fraction(30), True) and "vfr" in reason

    def test_mixed_30_60_conforms_to_60(self):
        pat = [2, 1, 1, 2, 2, 2, 2, 2]  # in 1/60 s units (conftest synth_vfr)
        pts, t = [], 0
        for i in range(200):
            pts.append(t)
            t += pat[i % len(pat)] * 256
        fps, vfr, _ = decide_fps(timing_summary(pts, "1/15360"), Fraction(60), None)
        assert (fps, vfr) == (Fraction(60), True)

    def test_fallback_without_timing(self):
        assert decide_fps(None, Fraction(25), None) == (Fraction(25), False, "stream frame rate (no packet timing)")
        with pytest.raises(ProbeError):
            decide_fps(None, None, None)


class TestMediaInfoFromProbe:
    def test_iphone_dv84_hlg_portrait(self):
        v = _stream(width=1920, height=1080, pix_fmt="yuv420p10le", color_transfer="arib-std-b67",
                    color_primaries="bt2020", color_space="bt2020nc", codec_tag_string="hvc1",
                    side_data_list=[{"side_data_type": "Display Matrix", "rotation": -90},
                                    {"side_data_type": "DOVI configuration record", "dv_profile": 8,
                                     "dv_level": 5, "dv_bl_signal_compatibility_id": 4}])
        apac = _astream(index=2, codec_name="apac", channels=4)
        studio = {"video_timing": _cfr_timing(300, 20, "1/600"), "video_stream_index": 0,
                  "audio_stream_index": 1, "first_video": {"pts": 0, "time_base": "1/600", "side_data": []},
                  "first_audio": {"pts": 0, "time_base": "1/48000"},
                  "edit_lists": [{"track_id": 2, "handler": "soun", "media_timescale": 48000,
                                  "movie_timescale": 600,
                                  "entries": [{"segment_duration": 6000, "media_time": 2112, "rate": 1.0}]}]}
        mi = media_info_from_probe(_probe([v, _astream(), apac], studio), "/x/take.mov")
        assert (mi.width, mi.height, mi.rotation) == (1080, 1920, 90)
        assert mi.is_portrait and not has_flag(mi, FLAG_NEEDS_REFRAME) and not has_flag(mi, FLAG_LANDSCAPE)
        assert mi.color.hdr and mi.color.hdr_format == "hlg" and mi.color.dolby_vision
        assert mi.color.bit_depth == 10
        assert mi.fps == 30 and not mi.vfr and mi.frame_count == 300 and mi.duration_us == 10_000_000
        assert mi.audio is not None and mi.audio.stream_index == 1 and mi.audio.priming_samples == 2112
        assert mi.edit_list
        assert any("APAC" in n for n in mi.notes)
        assert any("Dolby Vision profile 8" in n for n in mi.notes)

    def test_landscape_flags(self):
        mi = media_info_from_probe(_probe([_stream(), _astream()]), "/x/l.mp4")
        assert (mi.width, mi.height) == (1920, 1080)
        assert has_flag(mi, FLAG_LANDSCAPE) and has_flag(mi, FLAG_NEEDS_REFRAME)

    def test_four_three_portrait_needs_reframe(self):
        mi = media_info_from_probe(_probe([_stream(width=1440, height=1920)]), "/x/p.mov")
        assert has_flag(mi, FLAG_NEEDS_REFRAME) and not has_flag(mi, FLAG_LANDSCAPE)

    def test_no_audio_flag(self):
        mi = media_info_from_probe(_probe([_stream(width=1080, height=1920)]), "/x/n.mp4")
        assert mi.audio is None and not mi.has_audio and has_flag(mi, FLAG_NO_AUDIO)

    def test_untagged_10bit_bt2020_assumed_hlg(self):
        v = _stream(pix_fmt="yuv420p10le", color_transfer="unknown", color_primaries="bt2020",
                    color_space="bt2020nc")
        mi = media_info_from_probe(_probe([v]), "/x/u.mov")
        assert mi.color.hdr and mi.color.hdr_format == "hlg" and mi.color.transfer == "arib-std-b67"

    def test_untagged_sdr_assumes_bt709(self):
        v = _stream(color_transfer=None, color_primaries=None, color_space=None, color_range=None)
        mi = media_info_from_probe(_probe([v]), "/x/u.mov")
        assert not mi.color.hdr
        assert (mi.color.primaries, mi.color.transfer, mi.color.matrix, mi.color.range) == \
            ("bt709", "bt709", "bt709", "tv")
        assert any("untagged colour" in n for n in mi.notes)

    def test_mislabelled_2020_matrix_stays_sdr(self):
        v = _stream(pix_fmt="yuv420p10le", color_space="bt2020nc")
        mi = media_info_from_probe(_probe([v]), "/x/m.mov")
        assert not mi.color.hdr and mi.color.matrix == "bt2020nc"
        assert any("inconsistent colour tags" in n for n in mi.notes)

    def test_dv_profile5_flagged(self):
        v = _stream(pix_fmt="yuv420p10le", color_transfer=None, color_primaries=None, color_space=None,
                    side_data_list=[{"side_data_type": "DOVI configuration record", "dv_profile": 5,
                                     "dv_bl_signal_compatibility_id": 0}])
        mi = media_info_from_probe(_probe([v]), "/x/dv5.mp4")
        assert mi.color.hdr and mi.color.hdr_format == "dolby_vision" and has_flag(mi, FLAG_DV_UNSUPPORTED)

    def test_pq_and_yuvj_range(self):
        pq = media_info_from_probe(_probe([_stream(color_transfer="smpte2084", color_primaries="bt2020",
                                                   color_space="bt2020nc", pix_fmt="yuv420p10le")]), "/x/pq.mov")
        assert pq.color.hdr and pq.color.hdr_format == "pq"
        full = media_info_from_probe(_probe([_stream(pix_fmt="yuvj420p", color_range=None)]), "/x/j.mov")
        assert full.color.range == "pc"

    def test_non_square_pixels_upsampled(self):
        v = _stream(width=1440, height=1080, sample_aspect_ratio="4:3")
        mi = media_info_from_probe(_probe([v]), "/x/ana.mov")
        assert (mi.width, mi.height) == (1920, 1080) and mi.sar == Fraction(4, 3)
        rot = _stream(width=1440, height=1080, sample_aspect_ratio="4:3",
                      side_data_list=[{"side_data_type": "Display Matrix", "rotation": -90}])
        mr = media_info_from_probe(_probe([rot]), "/x/ana_rot.mov")
        assert (mr.width, mr.height) == (1080, 1920)

    def test_vfr_and_start_offsets(self):
        deltas = [640, 800, 1280, 640, 640, 800] * 60
        pts = [403 + int(x) for x in np.concatenate([[0], np.cumsum(deltas)])]
        studio = {"video_timing": timing_summary(pts, "1/19200", last_duration=640), "video_stream_index": 0,
                  "audio_stream_index": 1, "first_video": {"pts": 403, "time_base": "1/19200"},
                  "first_audio": {"pts": 0, "time_base": "1/48000"}}
        mi = media_info_from_probe(_probe([_stream(width=1080, height=1920, time_base="1/19200"), _astream()],
                                          studio), "/x/v.mov")
        assert mi.vfr and mi.fps == 30 and has_flag(mi, FLAG_VFR)
        assert mi.start_us == 20990 and mi.audio is not None and mi.audio.start_us == 0
        src = Fraction(pts[-1] + 640 - 403, 19200)
        assert abs(mi.duration_s - src) <= Fraction(1, 60)  # within half a frame
        assert any("audio starts" in n for n in mi.notes)

    def test_no_video_raises(self):
        with pytest.raises(ProbeError):
            media_info_from_probe(_probe([_astream()]), "/x/a.m4a")

    def test_fallback_without_measurements(self):
        mi = media_info_from_probe(_probe([_stream(width=1080, height=1920, duration="6.000000"), _astream()]),
                                   "/x/f.mov")
        assert mi.fps == 30 and mi.frame_count == 180 and mi.audio is not None


class TestHdrPeak:
    def test_hlg_is_1000(self):
        d = _probe([_stream(color_transfer="arib-std-b67")])
        assert hdr_peak_nits(d) == 1000.0

    def test_pq_prefers_maxcll_then_mastering(self):
        cll = {"side_data_type": "Content light level metadata", "max_content": 1600, "max_average": 400}
        md = {"side_data_type": "Mastering display metadata", "max_luminance": "40000000/10000",
              "min_luminance": "50/10000"}
        assert hdr_peak_nits(_probe([_stream(color_transfer="smpte2084", side_data_list=[md, cll])])) == 1600.0
        assert hdr_peak_nits(_probe([_stream(color_transfer="smpte2084", side_data_list=[md])])) == 4000.0
        assert hdr_peak_nits(_probe([_stream(color_transfer="smpte2084")])) == 1000.0

    def test_sdr_none(self):
        assert hdr_peak_nits(_probe([_stream()])) is None


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def test_parse_mp4_edit_lists_handcrafted(tmp_path: Path):
    mvhd = _box(b"mvhd", bytes(4) + bytes(8) + struct.pack(">I", 600) + bytes(80))
    tkhd = _box(b"tkhd", bytes(4) + bytes(8) + struct.pack(">I", 2) + bytes(68))
    elst = _box(b"elst", bytes(4) + struct.pack(">I", 2) + struct.pack(">Iihh", 21, -1, 1, 0)
                + struct.pack(">Iihh", 36000, 0, 1, 0))
    mdhd = _box(b"mdhd", bytes(4) + bytes(8) + struct.pack(">I", 19200) + bytes(8))
    hdlr = _box(b"hdlr", bytes(4) + bytes(4) + b"vide" + bytes(13))
    trak = _box(b"trak", tkhd + _box(b"edts", elst) + _box(b"mdia", mdhd + hdlr))
    elst64 = _box(b"elst", bytes([1, 0, 0, 0]) + struct.pack(">I", 1) + struct.pack(">Qqhh", 6000, 2112, 1, 0))
    trak2 = _box(b"trak", _box(b"tkhd", bytes(4) + bytes(8) + struct.pack(">I", 3) + bytes(68))
                 + _box(b"edts", elst64) + _box(b"mdia", _box(b"hdlr", bytes(8) + b"soun" + bytes(13))))
    f = tmp_path / "t.mov"
    f.write_bytes(_box(b"ftyp", b"qt  " + bytes(4)) + _box(b"mdat", bytes(64)) + _box(b"moov", mvhd + trak + trak2))
    tracks = parse_mp4_edit_lists(f)
    assert len(tracks) == 2
    v, a = tracks
    assert (v["track_id"], v["handler"], v["media_timescale"], v["movie_timescale"]) == (2, "vide", 19200, 600)
    assert v["entries"] == [{"segment_duration": 21, "media_time": -1, "rate": 1.0},
                            {"segment_duration": 36000, "media_time": 0, "rate": 1.0}]
    assert (a["handler"], a["entries"][0]["media_time"]) == ("soun", 2112)
    (tmp_path / "junk.mov").write_bytes(b"not an mp4 at all")
    assert parse_mp4_edit_lists(tmp_path / "junk.mov") == []


# ============================================================================================ pure: ingest
def _mi(**kw: Any) -> MediaInfo:
    base: dict[str, Any] = {"path": "/x/o.mov", "width": 1080, "height": 1920, "fps": Fraction(30),
                            "duration_us": 10_000_000,
                            "color": ColorInfo(primaries="bt709", transfer="bt709", matrix="bt709", range="tv")}
    base.update(kw)
    return MediaInfo(**base)


class TestFilters:
    def test_sdr_chain(self):
        vf = mezz_video_filter(_mi(), chroma_location="left")
        assert vf.startswith("setpts=PTS-STARTPTS,fps=fps=30/1:round=near,tpad=stop_mode=clone:stop=3,")
        assert "tonemap" not in vf and "dither" not in vf and "gbrpf32le" not in vf
        assert "tin=bt709:min=bt709:pin=bt709:rin=tv:cin=left:t=bt709:p=bt709:m=bt709:r=tv" in vf
        assert "agamma=0" in vf and "format=yuv422p10le" in vf
        assert vf.endswith("setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv")

    def test_sdr_601_full_range_converted(self):
        c = ColorInfo(primaries="smpte170m", transfer="smpte170m", matrix="smpte170m", range="pc")
        vf = mezz_video_filter(_mi(color=c))
        assert "tin=smpte170m:min=smpte170m:pin=smpte170m:rin=pc" in vf and ":r=tv" in vf

    def test_hdr_chain_order_and_params(self):
        c = ColorInfo(primaries="bt2020", transfer="arib-std-b67", matrix="bt2020nc", range="tv", hdr=True,
                      hdr_format="hlg")
        vf = mezz_video_filter(_mi(color=c, fps=Fraction(30000, 1001)), peak_nits=1000.0)
        parts = vf.split(",")
        i_lin = next(i for i, p in enumerate(parts) if "t=linear" in p)
        i_fmt = parts.index("format=gbrpf32le")
        i_set = next(i for i, p in enumerate(parts) if p.startswith("setparams=") and "colorspace=bt709" in p)
        i_tm = next(i for i, p in enumerate(parts) if p.startswith("tonemap="))
        i_out = next(i for i, p in enumerate(parts) if "dither=error_diffusion" in p)
        assert i_lin < i_fmt < i_set < i_tm < i_out
        assert "npl=203" in parts[i_lin] and ":p=bt709" in parts[i_lin] and "tin=arib-std-b67" in parts[i_lin]
        assert parts[i_tm] == "tonemap=tonemap=mobius:param=0.6:desat=2:peak=4.926108"
        assert "fps=fps=30000/1001" in vf

    def test_tonemap_config(self):
        assert ToneMapConfig().effective_param == 0.6
        assert ToneMapConfig(operator="hable").effective_param is None
        tm = ToneMapConfig.from_env({"STUDIO_TONEMAP_OPERATOR": "Hable", "STUDIO_TONEMAP_DESAT": "0",
                                     "STUDIO_TONEMAP_NPL": "100", "STUDIO_TONEMAP_PEAK_NITS": "600"})
        assert (tm.operator, tm.desat, tm.npl, tm.peak_nits) == ("hable", 0.0, 100.0, 600.0)
        assert tm.signal_peak(1000.0) == pytest.approx(6.0)
        assert ToneMapConfig().signal_peak(None) == pytest.approx(1000 / 203)
        assert ToneMapConfig().signal_peak(100.0) == 1.0
        with pytest.raises(ValueError):
            ToneMapConfig(operator="bt2390")
        with pytest.raises(ValueError):
            ToneMapConfig(npl=0)
        c = ColorInfo(transfer="smpte2084", primaries="bt2020", matrix="bt2020nc", range="tv", hdr=True,
                      hdr_format="pq")
        vf = mezz_video_filter(_mi(color=c), tonemap=ToneMapConfig(operator="reinhard", param=0.5), peak_nits=4000)
        assert "tonemap=tonemap=reinhard:param=0.5:desat=2:peak=19.704433" in vf
        dim = mezz_video_filter(_mi(color=c), peak_nits=150)  # below reference white: nothing to compress
        assert "tonemap=tonemap=clip:desat=2:peak=1.000000" in dim

    def test_interlaced_deinterlaced_first(self):
        vf = mezz_video_filter(_mi(width=1920, height=1080), field_order="tt")
        assert vf.startswith("bwdif=mode=send_frame:parity=auto:deint=all,setpts=PTS-STARTPTS")
        assert not mezz_video_filter(_mi(), field_order="progressive").startswith("bwdif")
        assert not mezz_video_filter(_mi(rotation=90), field_order="tt").startswith("bwdif")

    def test_sar_resize(self):
        vf = mezz_video_filter(_mi(width=1920, height=1080, sar=Fraction(4, 3)))
        assert ":w=1920:h=1080" in vf and "setsar=1" in vf

    def test_codec_args(self):
        vt = mezz_codec_args("prores_hq", encoder="prores_videotoolbox")
        assert vt[:4] == ["-c:v", "prores_videotoolbox", "-profile:v", "3"]
        ks = mezz_codec_args("prores", encoder="prores_ks")
        assert ks[:6] == ["-c:v", "prores_ks", "-profile:v", "2", "-vendor", "apl0"]
        for args in (vt, ks, mezz_codec_args("ffv1")):
            assert "yuv422p10le" in args and args.count("bt709") == 3
        with pytest.raises(ValueError):
            mezz_codec_args("h264")

    def test_estimate_and_options(self):
        hq = estimate_mezz_bytes(_mi(), "prores_hq")
        assert estimate_mezz_bytes(_mi(), "prores") < hq < estimate_mezz_bytes(_mi(), "ffv1")
        assert 200e6 < hq < 350e6  # ~220 Mb/s for 10 s of 1080p30
        assert estimate_mezz_bytes(_mi(width=2160, height=3840, fps=Fraction(60)), "prores_hq") > 7 * hq
        with pytest.raises(ValueError):
            IngestOptions(mezz_codec="h264")
        o = IngestOptions.from_env({"STUDIO_MEZZ_CODEC": "ffv1", "STUDIO_PRORES_ENCODER": "prores_ks"})
        assert (o.mezz_codec, o.prores_encoder) == ("ffv1", "prores_ks")

    @pytest.mark.parametrize(("w", "h", "exp"), [(1080, 1920, (540, 960)), (1920, 1080, (960, 540)),
                                                 (2160, 3840, (540, 960)), (1440, 1920, (540, 720)),
                                                 (1080, 1080, (540, 540)), (720, 1280, (540, 960))])
    def test_proxy_size(self, w, h, exp):
        assert proxy_size(w, h) == exp


class TestDownmix:
    sr = 48_000

    def _speech(self, seconds: float = 3.0, seed: int = 0) -> np.ndarray:
        rng = np.random.default_rng(seed)
        t = np.arange(int(seconds * self.sr)) / self.sr
        env = (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)) ** 2
        return (0.3 * env * (np.sin(2 * np.pi * 180 * t) + 0.5 * np.sin(2 * np.pi * 900 * t)
                             + 0.2 * rng.standard_normal(len(t)))).astype(np.float32)

    def test_identical_channels_average_equals_input(self):
        s = self._speech()
        mono, d = dialogue_downmix(np.stack([s, s], axis=1))
        assert d["mode"] == "coherent_average" and d["lag_samples"] == 0
        assert np.allclose(mono, s, atol=1e-6)

    def test_silent_right_channel_dropped(self):
        s = self._speech()
        mono, d = dialogue_downmix(np.stack([s, np.zeros_like(s)], axis=1))
        assert d["mode"] == "left_only" and np.array_equal(mono, s)

    def test_near_silent_channel_dropped(self):
        s = self._speech()
        n = self._speech(seed=5) * 0.01
        mono, d = dialogue_downmix(np.stack([n, s], axis=1))
        assert d["mode"] == "right_only" and np.array_equal(mono, s)

    def test_polarity_inversion_corrected(self):
        s = self._speech()
        mono, d = dialogue_downmix(np.stack([s, -s], axis=1))
        assert d["polarity_inverted"] and d["mode"] == "coherent_average"
        assert np.allclose(mono, s, atol=1e-6)

    def test_delay_aligned_before_sum(self):
        s = self._speech()
        lag = 7
        r = np.concatenate([np.zeros(lag, np.float32), s[:-lag]])  # right lags left by 7 samples
        mono, d = dialogue_downmix(np.stack([s, r], axis=1))
        assert d["mode"] == "coherent_average" and abs(d["lag_samples"]) == lag
        # aligned average == left except the zero-filled edge
        assert np.max(np.abs(mono[lag:-lag] - s[lag:-lag])) < 1e-5
        naive = 0.5 * (s + r)
        spec = lambda x: np.abs(np.fft.rfft(x))  # noqa: E731
        assert spec(mono).sum() > spec(naive).sum()  # no comb-filter loss

    def test_weaker_coherent_channel_dropped(self):
        s = self._speech()
        mono, d = dialogue_downmix(np.stack([s, 0.3 * s], axis=1))  # -10.5 dB safety-track style
        assert d["mode"] == "left_only" and np.array_equal(mono, s)

    def test_incoherent_keeps_cleaner(self):
        rng = np.random.default_rng(3)
        clean = self._speech(seed=1)
        noisy = self._speech(seed=2)[::-1].copy() + (0.08 * rng.standard_normal(len(clean))).astype(np.float32)
        mono, d = dialogue_downmix(np.stack([noisy, clean], axis=1))
        assert d["mode"] == "right_only" and np.array_equal(mono, clean)

    def test_all_silent_and_bad_shape(self):
        z = np.zeros((4800, 2), np.float32)
        mono, d = dialogue_downmix(z)
        assert d["mode"] == "silent" and mono.shape == (4800,)
        with pytest.raises(ValueError):
            dialogue_downmix(np.zeros((10, 3), np.float32))


# ============================================================================================ media fixtures
@pytest.fixture(scope="module")
def mod_dir(tmp_path_factory: pytest.TempPathFactory):
    d = tmp_path_factory.mktemp("ingest_mod")
    yield d
    shutil.rmtree(d, ignore_errors=True)  # mezzanines are large; never leave them behind


@pytest.fixture(scope="module")
def mod_settings(mod_dir: Path) -> Settings:
    return Settings.load(env={"STUDIO_WORK_DIR": str(mod_dir / "work")})


@pytest.fixture(scope="module")
def ingested(mod_settings: Settings):
    """``get(name, src, **opts) -> (job, info)``, cached per name for the module."""
    cache: dict[str, tuple[Job, MediaInfo]] = {}

    def get(name: str, src: Path, **opts: Any) -> tuple[Job, MediaInfo]:
        if name not in cache:
            job = Job.create(name, work_dir=mod_settings.work_dir)
            opts.setdefault("min_free_bytes", 64 << 20)  # the shared test machine runs close to a full disk
            info = ingest(src, job, settings=mod_settings, options=IngestOptions(**opts))
            cache[name] = (job, info)
        return cache[name]

    return get


@pytest.fixture(scope="module")
def src_dir(mod_dir: Path) -> Path:
    d = mod_dir / "src"
    d.mkdir()
    return d


@pytest.fixture(scope="module")
def sdr_midtones(src_dir: Path) -> Path:
    """1 s 1080x1920 SDR BT.709: testsrc2 dimmed to mid-tones (skin-tone range) plus a white patch."""
    out = src_dir / "sdr_midtones.mov"
    _ff(["-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=1", "-f", "lavfi", "-i",
         "sine=frequency=330:sample_rate=48000:duration=1", "-vf",
         "format=gbrp,colorlevels=romax=0.62:gomax=0.62:bomax=0.62,drawbox=x=40:y=40:w=240:h=240:color=white:t=fill,"
         "format=yuv444p10le,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
         "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p10le",
         "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
         "-c:a", "aac", "-shortest", str(out)])
    return out


@pytest.fixture(scope="module")
def hlg_roundtrip(src_dir: Path, sdr_midtones: Path) -> Path:
    """``sdr_midtones`` converted SDR → HLG with the BT.2408 203-nit mapping (10-bit HEVC, near lossless)."""
    out = src_dir / "hlg_roundtrip.mov"
    vf = ("zscale=tin=bt709:min=bt709:pin=bt709:rin=tv:t=linear:p=bt709:npl=203:agamma=0,format=gbrpf32le,"
          "zscale=tin=linear:pin=bt709:p=bt2020:t=linear:agamma=0,"
          "zscale=tin=linear:pin=bt2020:t=arib-std-b67:p=bt2020:m=bt2020nc:r=tv:npl=203:dither=error_diffusion"
          ":agamma=0,format=yuv420p10le,setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc"
          ":range=tv")
    _ff(["-i", str(sdr_midtones), "-map", "0:v", "-map", "0:a", "-vf", vf, "-c:v", "libx265",
         "-preset", "ultrafast", "-x265-params",
         "log-level=error:crf=2:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
         "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
         "-color_range", "tv", "-tag:v", "hvc1", "-c:a", "copy", str(out)])
    return out


@pytest.fixture(scope="module")
def stereo_src(src_dir: Path) -> Path:
    """360x640 2 s with stereo PCM where R = L delayed by 0.1 ms (≈5 samples)."""
    out = src_dir / "stereo.mov"
    sig = "0.3*sin(2*PI*220*{t})*(0.6+0.4*sin(2*PI*3*{t}))+0.1*sin(2*PI*1330*{t})"
    expr = sig.format(t="t") + "|" + sig.format(t="(t-0.0001)")
    _ff(["-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=2", "-f", "lavfi", "-i",
         f"aevalsrc=exprs='{expr}':s=48000:d=2", "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "pcm_s24le", str(out)])
    return out


def _click_wav(path: Path, sr: int, at: float, dur: float) -> Path:
    _ff(["-f", "lavfi", "-i", f"aevalsrc='if(between(t\\,{at}\\,{at + 0.0005})\\,0.9\\,0)':s={sr}:d={dur}",
         "-c:a", "pcm_s16le", str(path)])
    return path


@pytest.fixture(scope="module")
def video_late_src(src_dir: Path) -> Path:
    """Video starts 0.5 s after audio; 44.1 kHz PCM with a click at 1.0 s (→ 0.5 s on the timeline)."""
    click = _click_wav(src_dir / "click441.wav", 44100, 1.0, 3.0)
    out = src_dir / "video_late.mov"
    _ff(["-itsoffset", "0.5", "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=2.5", "-i", str(click),
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "pcm_s16le", str(out)])
    return out


@pytest.fixture(scope="module")
def audio_late_src(src_dir: Path) -> Path:
    """Audio starts 0.25 s after video; click at 0.5 s of audio (→ 0.75 s on the timeline)."""
    click = _click_wav(src_dir / "click48.wav", 48000, 0.5, 2.0)
    out = src_dir / "audio_late.mov"
    _ff(["-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=2.5", "-itsoffset", "0.25", "-i", str(click),
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "pcm_s16le", str(out)])
    return out


@pytest.fixture(scope="module")
def mirrored_src(src_dir: Path) -> Path:
    """Coded 1280x720 with a display matrix = rotate 90° CCW + horizontal flip (front-camera style)."""
    land = src_dir / "mirror_land.mp4"
    _ff(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=1", "-c:v", "libx264", "-preset",
         "ultrafast", "-pix_fmt", "yuv420p", str(land)])
    out = src_dir / "mirrored.mov"
    _ff(["-display_rotation", "90", "-display_hflip", "-i", str(land), "-c", "copy", str(out)])
    return out


@pytest.fixture(scope="module")
def interlaced_src(src_dir: Path) -> Path:
    out = src_dir / "interlaced.mov"
    _ff(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=1", "-c:v", "libx264", "-preset",
         "ultrafast", "-flags", "+ildct+ilme", "-top", "1", "-pix_fmt", "yuv420p", str(out)])
    return out


@pytest.fixture(scope="module")
def landscape_src(src_dir: Path) -> Path:
    out = src_dir / "landscape.mp4"
    _ff(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=2", "-f", "lavfi", "-i",
         "sine=frequency=300:sample_rate=48000:duration=2", "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)])
    return out


# ============================================================================================ media tests
@needs_ffmpeg
class TestIngestSynthetic:
    def test_portrait_sdr(self, ingested, synth_video: Path):
        job, info = ingested("video", synth_video)
        assert (info.width, info.height, info.fps, info.rotation) == (1080, 1920, Fraction(30), 0)
        assert info.frame_count == 180 and info.duration_us == 6_000_000 and not info.color.hdr
        assert info.audio is not None and info.audio.sample_rate == 48000
        # files
        orig = job.original_path
        assert orig is not None and orig.name == "original.mp4" and orig.samefile(synth_video)  # hard link
        for p in (job.probe_path, job.media_info_path, job.mezz_path, job.audio_path, job.proxy_path):
            assert p.exists() and p.stat().st_size > 0, p
        assert not stereo_audio_path(job).exists()
        assert not list(job.media_dir.glob(".*.tmp*"))
        pj = json.loads(job.probe_path.read_text())
        assert "streams" in pj and pj["studio"]["video_timing"]["n_frames"] == 180
        assert job.load_media_info() == info
        # mezzanine
        v = _vstream(job.mezz_path)
        assert (v["codec_name"], v["profile"], v["pix_fmt"]) == ("prores", "HQ", "yuv422p10le")
        assert (v["color_primaries"], v["color_transfer"], v["color_space"]) == ("bt709", "bt709", "bt709")
        assert int(v["nb_frames"]) == 180 and v["r_frame_rate"] == "30/1"
        assert not any(sd.get("rotation") is not None for sd in v.get("side_data_list", []))
        # audio: exact length, float32 mono 48k, content survives
        a, sr = sf.read(job.audio_path, dtype="float32")
        assert sr == 48000 and a.ndim == 1 and len(a) == 288_000
        assert sf.info(str(job.audio_path)).subtype == "FLOAT" and np.sqrt(np.mean(a[4800:-4800] ** 2)) > 0.05
        # proxy
        pv = _vstream(job.proxy_path)
        assert (pv["width"], pv["height"], pv["codec_name"], pv["pix_fmt"]) == (540, 960, "h264", "yuv420p")
        assert int(pv["nb_frames"]) == 180 and pv["start_time"] == "0.000000"  # frame-for-frame with the mezz
        streams = ffprobe_json(job.proxy_path)["streams"]
        assert any(s["codec_type"] == "audio" for s in streams)
        meta = job.meta["meta"]["ingest"]
        assert meta["verify"]["ok"] is True and meta["mezz"]["codec"] == "prores_hq" and meta["proxy"]["timecode"]
        assert any(e["event"] == "ingest" for e in job.read_trace())

    def test_mezz_matches_source_pixels(self, ingested, synth_video: Path):
        job, _ = ingested("video", synth_video)
        for n in (0, 97, 179):
            src = _gray_frame(synth_video, n, 1080, 1920)
            mz = _gray_frame(job.mezz_path, n, 1080, 1920)
            assert np.mean(np.abs(src - mz)) < 1.0, n  # same frame, visually lossless
        rgb_src, rgb_mz = _rgb_frame(synth_video, 50, 1080, 1920), _rgb_frame(job.mezz_path, 50, 1080, 1920)
        assert np.median(np.abs(rgb_src - rgb_mz)) < 1.0  # chroma neither shifted nor smeared

    def test_proxy_burns_timecode(self, ingested, synth_video: Path):
        job, _ = ingested("video", synth_video)
        px = _gray_frame(job.proxy_path, 30, 540, 960)
        ref = _gray_frame(job.mezz_path, 30, 540, 960, vf_extra=",scale=540:960:flags=lanczos")
        corner = np.abs(px[900:, :200] - ref[900:, :200]).mean()
        centre = np.abs(px[300:600, 150:400] - ref[300:600, 150:400]).mean()
        assert corner > 8.0 and centre < 4.0

    def test_rotated_upright_and_direction(self, ingested, synth_rotated: Path):
        job, info = ingested("rotated", synth_rotated)
        assert (info.width, info.height, info.coded_width, info.coded_height) == (1080, 1920, 1920, 1080)
        assert info.rotation == 270  # display_rotation 90 = 90° counter-clockwise
        v = _vstream(job.mezz_path)
        assert (v["width"], v["height"]) == (1080, 1920)
        assert not any("rotation" in sd for sd in v.get("side_data_list", []))
        coded = _gray_frame(synth_rotated, 10, 1920, 1080, noautorotate=True)
        mz = _gray_frame(job.mezz_path, 10, 1080, 1920)
        ccw = np.abs(np.rot90(coded, k=1) - mz).mean()
        cw = np.abs(np.rot90(coded, k=-1) - mz).mean()
        assert ccw < 1.5 < cw
        assert (_vstream(job.proxy_path)["width"], _vstream(job.proxy_path)["height"]) == (540, 960)

    def test_mirrored_display_matrix(self, ingested, mirrored_src: Path):
        job, info = ingested("mirrored", mirrored_src, proxy=False)
        assert (info.width, info.height, info.rotation) == (720, 1280, 270)
        assert any("mirrored" in n for n in info.notes)
        coded = _gray_frame(mirrored_src, 5, 1280, 720, noautorotate=True)
        mz = _gray_frame(job.mezz_path, 5, 720, 1280)
        ok = np.abs(np.fliplr(np.rot90(coded, k=1)) - mz).mean()
        unflipped = np.abs(np.rot90(coded, k=1) - mz).mean()
        assert ok < 1.5 and unflipped > ok + 2.0

    def test_hlg_tonemapped_once_and_faces_preserved(self, ingested, hlg_roundtrip: Path, sdr_midtones: Path):
        job, info = ingested("hlg", hlg_roundtrip)
        assert info.color.hdr and info.color.hdr_format == "hlg" and has_flag(info, FLAG_HDR_TONEMAPPED)
        v = _vstream(job.mezz_path)
        assert (v["color_primaries"], v["color_transfer"], v["color_space"], v["pix_fmt"]) == \
            ("bt709", "bt709", "bt709", "yuv422p10le")
        meta = job.meta["meta"]["ingest"]["mezz"]
        assert meta["tonemap"]["effective_param"] == 0.6 and meta["peak_nits"] == 1000.0
        assert "tonemap=tonemap=mobius" in meta["filter"] and "dither=error_diffusion" in meta["filter"]
        for n in (3, 20):
            ref = _rgb_frame(sdr_midtones, n, 1080, 1920)
            out = _rgb_frame(job.mezz_path, n, 1080, 1920)
            assert np.isfinite(out).all()
            # round trip: below the Möbius knee (all the dimmed mid-tones, the skin range) the SDR comes back
            mid = np.ones(ref.shape[:2], bool)
            mid[:320, :320] = False  # the white patch
            err = np.abs(out - ref)[mid]
            # (the mean is dominated by the HLG source's 4:2:0 chroma at saturated test-pattern edges)
            assert np.median(err) < 0.6 and err.mean() < 2.5, (n, float(np.median(err)), float(err.mean()))
            luma = np.array([0.2126, 0.7152, 0.0722])
            assert np.abs(out @ luma - ref @ luma)[mid].mean() < 1.2
            # diffuse white (1.0 linear) is above the knee: rolled off (~92 %), never clipped or brightened
            white = out[80:240, 80:240]
            assert 0.85 * 255 < white.mean() < 0.97 * 255 and white.std() < 2.0


    def test_vfr_conformed_to_cfr(self, ingested, synth_vfr: Path):
        job, info = ingested("vfr", synth_vfr)
        assert info.vfr and has_flag(info, FLAG_VFR) and info.fps == 60
        v = _vstream(job.mezz_path)
        assert v["r_frame_rate"] == "60/1" and int(v["nb_frames"]) == info.frame_count
        src_dur = float(ffprobe_json(synth_vfr)["format"]["duration"])
        assert abs(info.frame_count / 60 - src_dur) <= 1 / 60 + 0.03  # container rounding of the last frame
        assert sf.info(str(job.audio_path)).frames == frame_to_sample(info.frame_count, info.fps)

    def test_no_audio_generates_silence(self, ingested, synth_noaudio: Path):
        job, info = ingested("noaudio", synth_noaudio)
        assert info.audio is None and has_flag(info, FLAG_NO_AUDIO)
        a, sr = sf.read(job.audio_path, dtype="float32")
        assert sr == 48000 and len(a) == frame_to_sample(info.frame_count, info.fps) and not a.any()
        assert any(s["codec_type"] == "audio" for s in ffprobe_json(job.proxy_path)["streams"])
        assert job.meta["meta"]["ingest"]["audio"]["mode"] == "generated_silence"

    def test_stereo_dialogue_downmix(self, ingested, stereo_src: Path):
        job, info = ingested("stereo", stereo_src)
        st = stereo_audio_path(job)
        assert st.exists()
        s, sr = sf.read(st, dtype="float32")
        m, _ = sf.read(job.audio_path, dtype="float32")
        assert s.shape == (len(m), 2) and sr == 48000
        d = job.meta["meta"]["ingest"]["audio"]["downmix"]
        assert d["mode"] == "coherent_average" and abs(d["lag_samples"]) == 5
        assert np.sqrt(np.mean(m ** 2)) == pytest.approx(np.sqrt(np.mean(s[:, 0] ** 2)), rel=0.02)
        assert any("stereo dialogue downmix" in n for n in info.notes)

    def test_video_late_audio_trimmed_sample_exact(self, ingested, video_late_src: Path):
        job, info = ingested("video_late", video_late_src)
        assert info.start_us == 500_000 and info.audio is not None and info.audio.start_us == 0
        a, _ = sf.read(job.audio_path, dtype="float32")
        onset = int(np.flatnonzero(np.abs(a) > 0.45)[0])
        assert abs(onset - 24_000) <= 2, onset  # click at 1.0 s of audio = 0.5 s after the first frame
        assert len(a) == frame_to_sample(info.frame_count, info.fps)

    def test_audio_late_padded(self, ingested, audio_late_src: Path):
        job, info = ingested("audio_late", audio_late_src)
        assert info.audio is not None and info.audio.start_us == 250_000
        a, _ = sf.read(job.audio_path, dtype="float32")
        assert not a[:11_000].any()  # silence before the audio starts
        assert abs(int(np.flatnonzero(np.abs(a) > 0.45)[0]) - 36_000) <= 2

    def test_landscape_keeps_native_aspect(self, ingested, landscape_src: Path):
        job, info = ingested("landscape", landscape_src)
        assert (info.width, info.height, info.fps) == (640, 360, Fraction(25))
        assert has_flag(info, FLAG_LANDSCAPE) and has_flag(info, FLAG_NEEDS_REFRAME)
        assert (_vstream(job.mezz_path)["width"], _vstream(job.mezz_path)["height"]) == (640, 360)
        assert (_vstream(job.proxy_path)["width"], _vstream(job.proxy_path)["height"]) == (960, 540)

    def test_interlaced_source_deinterlaced(self, ingested, interlaced_src: Path):
        job, info = ingested("interlaced", interlaced_src, proxy=False)
        assert info.fps == 25 and info.frame_count == 25
        assert any("deinterlaced with bwdif" in n for n in info.notes)
        assert job.meta["meta"]["ingest"]["mezz"]["filter"].startswith("bwdif=")

    def test_ffv1_and_prores_ks_options(self, ingested, landscape_src: Path):
        job, _ = ingested("ffv1", landscape_src, mezz_codec="ffv1", proxy=False)
        v = _vstream(job.mezz_path)
        assert (v["codec_name"], v["pix_fmt"], v["color_space"]) == ("ffv1", "yuv422p10le", "bt709")
        assert not job.proxy_path.exists()
        job2, _ = ingested("ks", landscape_src, prores_encoder="prores_ks", mezz_codec="prores", proxy=False)
        v2 = _vstream(job2.mezz_path)
        assert (v2["codec_name"], v2["profile"]) == ("prores", "Standard")
        assert job2.meta["meta"]["ingest"]["mezz"]["encoder"] == "prores_ks"

    def test_disk_guard_fallback_and_refusal(self, ingested, landscape_src: Path, mod_settings: Settings,
                                             monkeypatch: pytest.MonkeyPatch):
        _, info = ingested("landscape", landscape_src)
        du = namedtuple("du", "total used free")
        opts = IngestOptions(min_free_bytes=0, proxy=False)
        audio = int(float(info.duration_s) * 48000 * 12) + (8 << 20)
        between = (estimate_mezz_bytes(info, "prores") + estimate_mezz_bytes(info, "prores_hq")) // 2 + audio
        monkeypatch.setattr(ingest_mod.shutil, "disk_usage", lambda p: du(10**12, 0, between))
        job = Job.create("guard", work_dir=mod_settings.work_dir)
        mi = ingest(landscape_src, job, settings=mod_settings, options=opts)
        assert has_flag(mi, FLAG_MEZZ_FALLBACK) and _vstream(job.mezz_path)["profile"] == "Standard"
        monkeypatch.setattr(ingest_mod.shutil, "disk_usage", lambda p: du(10**12, 0, 1000))
        job2 = Job.create("guard2", work_dir=mod_settings.work_dir)
        with pytest.raises(IngestError, match="insufficient disk space"):
            ingest(landscape_src, job2, settings=mod_settings, options=opts)
        assert not job2.mezz_path.exists()

    def test_reingest_same_job_and_verify_catches_damage(self, landscape_src: Path, mod_settings: Settings):
        job = Job.create("again", work_dir=mod_settings.work_dir)
        a = ingest(landscape_src, job, settings=mod_settings, options=IngestOptions(min_free_bytes=64 << 20))
        assert job.proxy_path.exists()
        b = ingest(landscape_src, job, settings=mod_settings,
                   options=IngestOptions(proxy=False, min_free_bytes=64 << 20))
        assert a == b and len(list(job.media_dir.glob("original.*"))) == 1
        assert not job.proxy_path.exists()  # a stale proxy never outlives its mezzanine
        sf.write(job.audio_path, np.zeros(1000, np.float32), 48000, subtype="FLOAT")
        with pytest.raises(IngestError, match="samples"):
            verify_ingest(job, b, settings=mod_settings, check_proxy=False)

    def test_make_proxy_requires_mezz(self, mod_settings: Settings):
        job = Job.create("empty", work_dir=mod_settings.work_dir)
        with pytest.raises(IngestError):
            make_proxy(job, _mi(), settings=mod_settings)

    def test_errors(self, mod_settings: Settings, src_dir: Path):
        job = Job.create("bad", work_dir=mod_settings.work_dir)
        with pytest.raises(IngestError, match="no such file"):
            ingest(src_dir / "missing.mov", job, settings=mod_settings)
        txt = src_dir / "notes.mov"
        txt.write_text("definitely not a movie")
        with pytest.raises(IngestError):
            ingest(txt, job, settings=mod_settings)
        wav = _click_wav(src_dir / "only_audio.wav", 48000, 0.1, 0.5)
        with pytest.raises(IngestError, match="no video"):
            ingest(wav, job, settings=mod_settings)

    def test_probe_matches_ingest(self, synth_video: Path):
        mi = probe(synth_video)
        assert (mi.width, mi.height, mi.fps, mi.frame_count) == (1080, 1920, Fraction(30), 180)
        assert mi.audio is not None and mi.audio.codec == "aac"
        el = parse_mp4_edit_lists(synth_video)
        assert {t["handler"] for t in el} <= {"vide", "soun"}


@needs_ffmpeg
@pytest.mark.slow
def test_hevc_4k60_portrait(ingested, src_dir: Path):
    if not _enough_disk(0.4):
        pytest.skip("disk nearly full")
    src = src_dir / "hevc4k60.mov"
    _ff(["-f", "lavfi", "-i", "testsrc2=size=2160x3840:rate=60:duration=1", "-f", "lavfi", "-i",
         "sine=frequency=500:sample_rate=48000:duration=1", "-c:v", "libx265", "-preset", "ultrafast",
         "-x265-params", "log-level=error", "-pix_fmt", "yuv420p", "-tag:v", "hvc1", "-c:a", "aac", "-shortest",
         str(src)])
    job, info = ingested("hevc4k60", src, proxy=True)
    assert (info.width, info.height, info.fps, info.frame_count) == (2160, 3840, Fraction(60), 60)
    v = _vstream(job.mezz_path)
    assert (v["width"], v["height"], v["r_frame_rate"], int(v["nb_frames"])) == (2160, 3840, "60/1", 60)
    assert (_vstream(job.proxy_path)["width"], _vstream(job.proxy_path)["height"]) == (540, 960)


# ============================================================================================ real takes
def _enough_disk(gb: float = 1.3) -> bool:
    import tempfile

    return shutil.disk_usage(tempfile.gettempdir()).free > gb * 1e9


def _skip_unless_room(src: Path, where: Path) -> None:
    """Skip (environment, not a failure) when even a ProRes 422 mezzanine of ``src`` cannot be written."""
    info = probe(src)
    need = estimate_mezz_bytes(info, "prores") + int(float(info.duration_s) * 48000 * 12) + (96 << 20)
    free = shutil.disk_usage(where).free
    if free < need:
        pytest.skip(f"disk nearly full: {free / 1e9:.2f} GB free, ~{need / 1e9:.2f} GB needed for {src.name}")


def _real_check(job: Job, info: MediaInfo, src: Path) -> None:
    v = _vstream(job.mezz_path)
    assert (int(v["width"]), int(v["height"])) == (1080, 1920) == (info.width, info.height)
    assert (v["color_primaries"], v["color_transfer"], v["color_space"], v["pix_fmt"]) == \
        ("bt709", "bt709", "bt709", "yuv422p10le")
    assert not any("rotation" in sd for sd in v.get("side_data_list", []))
    assert Fraction(v["r_frame_rate"]) == info.fps == 30
    fd = Fraction(1, 30)
    src_v = _vstream(src)
    src_dur = Fraction(src_v["duration"])
    pre = int((job.meta["meta"]["ingest"].get("preroll") or {}).get("frames") or 0)  # held first frames
    mezz_dur = Fraction(int(v["nb_frames"])) / info.fps
    assert abs(mezz_dur - pre / info.fps - src_dur) <= fd, (float(mezz_dur), float(src_dur), pre)
    a = sf.info(str(job.audio_path))
    assert a.samplerate == 48000 and a.channels == 1
    assert abs(Fraction(a.frames, 48000) - mezz_dur) <= Fraction(1, 48000)
    assert (_vstream(job.proxy_path)["width"], _vstream(job.proxy_path)["height"]) == (540, 960)


@pytest.mark.slow
@pytest.mark.skipif(not (TESTDATA / "qa-editor-real-take40.mov").exists(), reason="QA take not available")
@pytest.mark.skipif(not _enough_disk(), reason="needs ~1.3 GB free for the 40 s ProRes mezzanine")
def test_real_take40_rotated(ingested, mod_dir: Path):
    src = TESTDATA / "qa-editor-real-take40.mov"
    _skip_unless_room(src, mod_dir)
    job, info = ingested("real_take40", src)
    assert info.rotation == 90 and (info.coded_width, info.coded_height) == (1920, 1080)
    assert not info.vfr and info.frame_count == 1216
    assert info.audio is not None and info.audio.priming_samples == 2112
    _real_check(job, info, src)
    # the picture is upright: the mezzanine equals the autorotated decode of the original
    ref = _gray_frame(src, 300, 1080, 1920)
    mz = _gray_frame(job.mezz_path, 300, 1080, 1920)
    assert np.abs(ref - mz).mean() < 1.0
    shutil.rmtree(job.root, ignore_errors=True)


@pytest.mark.slow
@pytest.mark.skipif(not (TESTDATA / "qa-editor-var-vfr.mov").exists(), reason="QA take not available")
@pytest.mark.skipif(not _enough_disk(), reason="needs ~1.3 GB free for the 36 s ProRes mezzanine")
def test_real_vfr(ingested, mod_dir: Path):
    src = TESTDATA / "qa-editor-var-vfr.mov"
    _skip_unless_room(src, mod_dir)
    job, info = ingested("real_vfr", src)
    pre = int((job.meta["meta"]["ingest"].get("preroll") or {}).get("frames") or 0)  # the take opens mid-word
    assert info.vfr and has_flag(info, FLAG_VFR) and info.frame_count == 1080 + pre
    assert info.start_us == 20990  # empty edit; audio.wav trimmed to match
    assert job.meta["meta"]["ingest"]["audio"]["first_pts_samples"] == 1008
    _real_check(job, info, src)
    shutil.rmtree(job.root, ignore_errors=True)


def test_audio_info_model_roundtrip():
    """AudioInfo/MediaInfo produced by probe survive JSON (media_info.json)."""
    mi = _mi(audio=AudioInfo(stream_index=1, codec="aac", sample_rate=44100, channels=1, start_us=-21333,
                             priming_samples=1024), notes=[FLAG_NO_AUDIO + " x"])
    back = MediaInfo.model_validate_json(mi.model_dump_json())
    assert back == mi and has_flag(back, FLAG_NO_AUDIO)


# ============================================================================================ pre-roll
def test_detect_speech_at_start() -> None:
    from studio.media.ingest import detect_speech_at_start

    sr = 48000
    rng = np.random.default_rng(1)
    floor = rng.standard_normal(sr * 4).astype(np.float32) * 10 ** (-70 / 20)
    speech = floor.copy()
    t = np.arange(sr) / sr
    burst = (0.2 * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))).astype(np.float32)
    speech[:sr] += burst
    speech[2 * sr:3 * sr] += burst
    assert detect_speech_at_start(speech, sr)["speech_at_start"]
    late = floor.copy()
    late[sr // 2:sr // 2 + sr] += burst  # first word at 0.5 s
    assert not detect_speech_at_start(late, sr)["speech_at_start"]
    tone = np.tile(burst, 4)  # constant music-like level: undetermined, no pre-roll
    assert not detect_speech_at_start(tone, sr)["speech_at_start"]


@needs_ffmpeg
def test_preroll_when_speech_starts_on_the_first_frame(tmp_path: Path, mod_settings: Settings) -> None:
    """Speech on frame 0 → the first frame is held for ceil(0.15 s) of frames over the take's room tone, so the
    first word survives AAC priming in every deliverable and lands after 0.1 s."""
    from studio.media.ingest import FLAG_PREROLL

    src = tmp_path / "speech_at_start.mov"
    expr = "(lt(t\\,1)+between(t\\,2\\,3))*0.2*sin(2*PI*180*t)+0.0003*sin(2*PI*50*t)"
    _ff(["-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=4", "-f", "lavfi", "-i",
         f"aevalsrc=exprs='{expr}':s=48000:d=4", "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset",
         "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(src)])
    job = Job.create("preroll", work_dir=tmp_path / "work")
    info = ingest(src, job, settings=mod_settings, options=IngestOptions(mezz_codec="prores", proxy=False,
                                                                        min_free_bytes=64 << 20))
    pre = job.meta["meta"]["ingest"]["preroll"]
    assert pre["applied"] and pre["frames"] == 5 and has_flag(info, FLAG_PREROLL)
    assert info.frame_count == 125 and int(_vstream(job.mezz_path)["nb_frames"]) == 125
    a, sr = sf.read(job.audio_path, dtype="float32")
    assert len(a) == 125 * 1600
    head = np.sqrt(np.mean(a[:7000] ** 2))
    assert 0 < head < 10 ** (-50 / 20)  # room tone, not digital silence and not speech
    assert np.sqrt(np.mean(a[8000:12000] ** 2)) > 0.05  # the first word now starts at 5 frames
    off = ingest(src, Job.create("no-preroll", work_dir=tmp_path / "work"), settings=mod_settings,
                 options=IngestOptions(mezz_codec="prores", proxy=False, preroll_s=0.0, min_free_bytes=64 << 20))
    assert off.frame_count == 120 and not has_flag(off, FLAG_PREROLL)
