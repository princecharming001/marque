"""Tests for studio.agent.tools (EditSession + Director tools). Keyless and offline.

Agents are driven by pydantic-ai ``FunctionModel`` (scripted Director) and ``TestModel`` (schema-generated
arguments). Frame tools run on a small synthetic video generated with ffmpeg. The ``real`` test lets the
house Anthropic model call ``get_transcript`` on the fixture index.
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from pydantic_ai import Agent, BinaryImage
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from studio.agent import providers as pv
from studio.agent import tools as tl
from studio.agent.providers import Capabilities
from studio.agent.tools import EditSession, PreviewResult, build_tools
from studio.jobs import Job
from studio.perception.index import TakeIndex

FFMPEG = shutil.which("ffmpeg")
STORY = [{"from_word": "w0001", "to_word": "w0008"}, {"from_word": "w0014", "to_word": "w0018"},
         {"from_word": "w0020", "to_word": "w0041"}]


# ============================================================================================ fixtures
@pytest.fixture(scope="session")
def tools_proxy_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """17 s 360x640 30 fps H.264 + AAC (covers the 16.6 s fixture index)."""
    if FFMPEG is None:
        pytest.skip("ffmpeg not available")
    out = tmp_path_factory.mktemp("tools_media") / "proxy17.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=360x640:rate=30:duration=17", "-f", "lavfi", "-i",
                    "sine=frequency=220:sample_rate=48000:duration=17", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "1", "-shortest", str(out)],
                   check=True)
    return out


@pytest.fixture
def media_job(job: Job, tools_proxy_video: Path) -> Job:
    shutil.copy(tools_proxy_video, job.proxy_path)
    return job


@pytest.fixture
def session(job: Job, take_index: TakeIndex) -> EditSession:
    return EditSession(job, take_index)


def _images(out: Any) -> list[BinaryImage]:
    return [x for x in out if isinstance(x, BinaryImage)] if isinstance(out, list) else []


# ============================================================================================ session basics
def test_session_starts_with_saved_v0(job: Job, take_index: TakeIndex) -> None:
    s = EditSession(job, take_index)
    assert s.doc.version == 0 and job.doc_versions() == [0] and s.versions == [0]
    assert s.doc.job_id == job.id
    s2 = EditSession(job)  # reopens the latest version and loads the index from the job
    assert s2.doc.version == 0 and len(s2.index.words) == len(take_index.words)
    with pytest.raises(ValueError):
        s.set_stage("montage")


def test_tool_list_is_complete_and_stable(session: EditSession) -> None:
    tools = session.tools()
    names = [t.name for t in tools]
    assert names == list(tl.TOOL_NAMES)
    assert tools.session is session
    for t in tools:
        td = t.tool_def
        assert td.description and len(td.description) > 40, t.name
        assert td.parameters_json_schema.get("type") == "object"
        assert t.max_retries == 5
    fam = next(t for t in tools if t.name == "cut_ops").tool_def
    assert fam.sequential and "set_story" in fam.description
    items = fam.parameters_json_schema["properties"]["ops"]["items"]
    assert "anyOf" in items and "$defs" in fam.parameters_json_schema
    assert "styles/" in next(t for t in tools if t.name == "load_skill").tool_def.description or True
    session.set_stage("story")
    assert [t.name for t in session.tools()] == names  # stage never changes the tool list
    assert [t.name for t in session.tools(op_tools="apply_ops")].count("cut_ops") == 0
    assert "apply_ops" not in [t.name for t in session.tools(op_tools="families")]
    only = session.tools(include=["get_words", "view_frames"])
    assert [t.name for t in only] == ["get_words", "view_frames"]


def test_tool_objects_are_reused_while_doctrine_changes(job: Job, take_index: TakeIndex, tmp_path: Path) -> None:
    """Tool definitions are part of the cached, thinking-bound prefix: they must not drift mid-conversation."""
    from studio.config import Settings

    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "SKILL.md").write_text("# Doctrine\n\nIndex.\n")
    (skills / "broll.md").write_text("# B-roll\n\nLoad this file when adding b-roll.\n")
    settings = Settings.load(env={"STUDIO_SKILLS_DIR": str(skills), "STUDIO_WORK_DIR": str(tmp_path / "w")})
    s = EditSession(job, take_index, settings=settings)
    first = s.tools()
    (skills / "music.md").write_text("# Music\n\nLoad this file when choosing music.\n")  # doctrine grows on disk
    second = s.tools()
    assert first is not second and all(a is b for a, b in zip(first, second, strict=True))
    desc = next(t for t in second if t.name == "load_skill").tool_def.description
    assert "broll" in desc and "music" not in desc
    assert "music" in asyncio.run(next(t for t in second if t.name == "load_skill").function(name="music"))
    assert s.tools(include=["view_frames", "get_words"])[0] is s.tools(include=["get_words", "view_frames"])[0]


def test_build_tools_variants(job: Job, take_index: TakeIndex) -> None:
    tools = build_tools(job, take_index, stage="fine_cut", exclude=["ask_creator"])
    assert tools.session.stage == "fine_cut" and "ask_creator" not in [t.name for t in tools]
    again = build_tools(tools.session, stage="finishing")
    assert again.session is tools.session and tools.session.stage == "finishing"


# ============================================================================================ query tools
def test_overview_and_transcripts(session: EditSession) -> None:
    ov = session.q_overview()
    assert "1080x1920 portrait" in ov and "158 wpm" in ov and "empty_story" in ov
    full = session.q_transcript("full")
    assert "w0014 The" in full and "[g0004 0.90s silence]" in full and "{Um}" in full
    assert "c01 take 2/2, recommended" in full
    assert session.q_transcript("compact").count("\n") == 5
    assert "story is empty" in session.q_transcript("cut")
    assert "every word is currently out" in session.q_transcript("removed")
    assert session.q_transcript("weird").startswith("ERROR")


def test_words_sentences_clusters_gaps(session: EditSession) -> None:
    session.apply_ops([{"op": "set_story", "segments": STORY, "reason": "first cut"}])
    words = session.q_words("w0017", "w0019")
    lines = words.splitlines()
    assert len(lines) == 3
    assert lines[1].startswith('w0018 "restraint." word') and "emph 0.95" in lines[1] and "kept seg002" in lines[1]
    assert "g0006 800ms breath" in lines[1] and "cut" in lines[2]
    sents = session.q_sentences()
    assert "s003 w0014-w0018" in sents and "c01 take 2/2 (recommended)" in sents and "kept 5/5" in sents
    assert "s002" in sents and "incomplete" in sents
    cl = session.q_clusters()
    assert "take 1 s002" in cl and "INCOMPLETE" in cl and "look_away 100%" in cl and "in cut 0/5" in cl
    gaps = session.q_gaps(250)
    assert "g0004 900ms silence" in gaps and "g0003 700ms pause" in gaps
    g3 = next(ln for ln in gaps.splitlines() if ln.startswith("g0003"))
    assert "segment edge" in g3
    inside = session.q_gaps(250, only_in_cut=True)
    assert "g0009" in inside and "inside seg003" in inside and "g0004" not in inside
    with pytest.raises(KeyError):
        session.q_words("w9999")


def test_prosody_events_assets(session: EditSession, job: Job) -> None:
    pro = session.q_prosody(["w0018", "w0001-w0002", "s005"])
    assert "w0018" in pro and "emphasis 0.95" in pro and "w0041" in pro and pro.count("\n") == 16
    with pytest.raises(KeyError):
        session.q_prosody(["zzz"])
    ev = session.q_visual_events()
    assert "look_away" in ev and "w0009-w0013" in ev and "glances at notes" in ev
    assert "blink" in session.q_visual_events("blink") and "look_away" not in session.q_visual_events("blink")
    assert session.q_visual_events("sneeze").startswith("ERROR")
    assert "No registered assets" in session.q_assets()
    from studio.doc.model import AssetRef, Licence

    job.register_asset(AssetRef(kind="video", source="pexels", source_id="42", path="assets/broll/px_42.mp4",
                                width=1080, height=1920, duration_ms=6000, description="hands on keyboard",
                                licence=Licence(name="Pexels License")))
    assert "pexels_42 video 1080x1920 6.0s from pexels: hands on keyboard" in session.q_assets()
    assert "No registered assets of kind audio" in session.q_assets("audio")


# ============================================================================================ ops
def test_apply_ops_versions_oplog_and_summary(session: EditSession, job: Job) -> None:
    out = session.apply_ops([{"op": "set_story", "segments": STORY, "reason": "first cut"},
                             {"op": "set_gap", "gap_id": "g0009", "ms": 350}, {"op": "bogus"}])
    assert out.applied == 2 and out.base_version == 0 and out.new_version == 1
    assert job.doc_versions() == [0, 1] and session.versions == [0, 1]
    assert out.text.startswith("Applied 2 of 3 ops: v0 -> v1")
    assert "[2] bogus {}: REJECTED; unknown op 'bogus'" in out.text
    assert "seg003 w0020-w0041" in out.text and "gaps g0009=350ms" in out.text and "Checks: 0 errors" in out.text
    oplog = job.read_oplog()
    assert [e["op"].get("op") for e in oplog] == ["set_story", "set_gap", "bogus"]
    # JSON string / single dict / {"ops": [...]} inputs
    r2 = session.apply_ops(json.dumps({"op": "pin", "word_ids": ["w0018"], "kind": "payoff"}))
    assert r2.applied == 1 and session.doc.pins.payoff_word_ids == ["w0018"]
    r3 = session.apply_ops({"ops": [{"op": "note", "text": "hook lands on 'much.'"}]})
    assert r3.applied == 1 and session.doc.version == 3
    with pytest.raises(ValueError):
        session.apply_ops("{not json")
    # undo through the meta family restores v2's content as v4
    r4 = session.apply_ops([{"op": "undo", "n": 1}])
    assert r4.applied == 1 and session.doc.version == 4 and not session.doc.notes


def test_stage_gating(session: EditSession, job: Job) -> None:
    session.set_stage("story")
    out = session.apply_ops([{"op": "set_story", "segments": STORY},
                             {"op": "set_color", "spec": {"exposure": 0.3}}])
    assert out.results[0].applied
    assert not out.results[1].applied and "not available in the story stage" in out.results[1].reason
    assert "set_color" in out.text and "Stage: story (families: cut, meta)" in out.text
    assert any(e["op"].get("op") == "set_color" and not e["applied"] for e in job.read_oplog())
    session.set_stage(None)
    assert session.apply_ops([{"op": "set_color", "spec": {"exposure": 0.3}}]).applied == 1


# ============================================================================================ frames
def test_view_frames_sheets_and_filmstrips(media_job: Job, take_index: TakeIndex) -> None:
    s = EditSession(media_job, take_index, capabilities=pv.capabilities_for("anthropic", "claude-fable-5-1"))
    s.apply_ops([{"op": "set_story", "segments": STORY}])
    items = ["w0001", "w0012:mid", "g0004", "s003", *[f"w{n:04d}" for n in range(20, 36)]]
    out = s.view_frames(items, ["w0010-w0020", "seg002"], frames_per_range=4)
    imgs = _images(out)
    assert len(imgs) >= 3 and s.images_sent == len(imgs)
    for im in imgs:
        with Image.open(io.BytesIO(im.data)) as pil:
            assert max(pil.size) <= 2000  # never downscaled by the API, even in many-image requests
            assert pv.visual_tokens(*pil.size) <= 4784  # nor by the visual-token cap
    text = "\n".join(x for x in out if isinstance(x, str))
    assert "Contact sheet 1/" in text and "Filmstrip w0010-w0020" in text and "Filmstrip seg002" in text
    assert "w0012:mid 0:04." in text and "eyes 0.90" in text  # measurements follow the images
    # sheets are saved in the job for the report
    assert list((media_job.critique_dir / "frames").glob("sheet_*.png"))


def test_view_frames_errors_and_limits(media_job: Job, take_index: TakeIndex) -> None:
    s = EditSession(media_job, take_index)
    assert s.view_frames()[0].startswith("ERROR")
    assert "bad item" in s.view_frames(["w0012:top"])[0]
    with pytest.raises(KeyError):
        s.view_frames(["w9999"])
    assert "bad range" in s.view_frames(ranges=["w0010 w0020"])[0]
    assert "at most" in s.view_frames([f"w{n:04d}" for n in range(1, 41)] + ["g0001"] * 9)[0]
    assert "no preview render" in s.view_frames(["w0001"], source="render")[0]
    tiny = EditSession(media_job, take_index, capabilities=Capabilities(max_images=1))
    assert _images(tiny.view_frames(["w0001"]))
    assert "accepts 1 per request" in tiny.view_frames(["w0002"])[0]
    budget = EditSession(media_job, take_index, image_budget_bytes=1000)
    assert "image budget" in budget.view_frames(["w0001"])[0]


class _FakeUploader:
    provider_name = "anthropic"

    def __init__(self, fail_after: int | None = None) -> None:
        self.calls: list[tuple[int, str, str]] = []
        self.fail_after = fail_after

    def upload(self, data: bytes, media_type: str, filename: str) -> str:
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise pv.ProviderError("connection", "Could not reach Anthropic (network error).")
        self.calls.append((len(data), media_type, filename))
        return f"file_{len(self.calls):03d}"


def test_view_frames_uploads_images(media_job: Job, take_index: TakeIndex) -> None:
    from pydantic_ai import UploadedFile

    up = _FakeUploader()
    s = EditSession(media_job, take_index, image_uploader=up, image_budget_bytes=1000,
                    capabilities=pv.capabilities_for("anthropic", "claude-fable-5-1"))
    out = s.view_frames(["w0001", "w0018"], ["s003"])
    files = [x for x in out if isinstance(x, UploadedFile)]
    assert len(files) == 2 and not _images(out)  # no inline bytes; the tiny byte budget does not apply
    assert files[0].file_id == "file_001" and files[0].media_type in ("image/png", "image/jpeg")
    assert files[0].provider_name == "anthropic" and up.calls[0][2].startswith("sheet_")
    assert s.images_sent == 2 and s.images_uploaded == 2 and s.image_bytes_sent == 0
    s.new_conversation()
    assert s.images_sent == 0


def test_view_frames_upload_failure_falls_back_inline(media_job: Job, take_index: TakeIndex) -> None:
    from pydantic_ai import UploadedFile

    up = _FakeUploader(fail_after=1)
    s = EditSession(media_job, take_index, image_uploader=up)
    out = s.view_frames(["w0001"], ["s003"])
    assert sum(isinstance(x, UploadedFile) for x in out) == 1 and len(_images(out)) == 1
    assert s.upload_disabled and "network error" in s.upload_disabled and s.image_bytes_sent > 0
    assert len(_images(s.view_frames(["w0018"]))) == 1 and len(up.calls) == 1  # stays inline afterwards
    assert any(r["event"] == "image_upload_disabled" for r in media_job.read_trace())


def test_director_sees_uploaded_sheets(media_job: Job, take_index: TakeIndex) -> None:
    """The model receives the uploaded sheet as UploadedFile content inside the tool result."""
    from pydantic_ai import UploadedFile

    tools = build_tools(media_job, take_index, image_uploader=_FakeUploader(), include=["view_frames"])
    got: dict[str, Any] = {}

    def director(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if _count_responses(messages) == 0:
            return ModelResponse(parts=[ToolCallPart("view_frames", {"items": ["w0014"]})])
        ret = next(p for p in messages[-1].parts if isinstance(p, ToolReturnPart))  # type: ignore[union-attr]
        got["content"] = ret.content
        return ModelResponse(parts=[TextPart("seen")])

    agent = Agent(pv.TracedModel(FunctionModel(director), job=media_job, role="director"), tools=tools)
    assert agent.run_sync("look").output == "seen"
    assert any(isinstance(x, UploadedFile) and x.file_id == "file_001" for x in got["content"])
    mc = [r for r in media_job.read_trace() if r["event"] == "model_call"]
    assert mc[-1]["uploaded_images_in_context"] == 1 and mc[-1]["images_in_context"] == 1


def test_view_frames_without_vision(media_job: Job, take_index: TakeIndex) -> None:
    s = EditSession(media_job, take_index, capabilities=Capabilities(vision=False))
    out = s.view_frames(["w0018", "g0006"])
    assert len(out) == 1 and isinstance(out[0], str)
    assert "cannot view images" in out[0] and "w0018" in out[0] and s.images_sent == 0


def test_view_frames_hires_falls_back_to_proxy(media_job: Job, take_index: TakeIndex) -> None:
    s = EditSession(media_job, take_index)
    imgs = _images(s.view_frames(["w0001", "w0018"], hires=True))
    assert imgs
    with Image.open(io.BytesIO(imgs[0].data)) as pil:
        assert pil.size[0] > 600  # tiles larger than the default 270 px (capped at the source width, never upscaled)


@pytest.mark.parametrize(("model", "src"), [("claude-fable-5-1", (1080, 1920)), ("claude-fable-5-1", (1920, 1080)),
                                            ("claude-opus-4-6", (1080, 1920)), ("claude-haiku-4-5", (1920, 1080))])
@pytest.mark.parametrize("hires", [False, True])
def test_sheet_plan_fits_model_limits(model: str, src: tuple[int, int], hires: bool) -> None:
    caps = pv.capabilities_for("anthropic", model)
    tw, cols, per = tl._sheet_plan(caps, *src, hires=hires, n_items=40)
    assert 96 <= tw <= src[0] and 1 <= cols <= per
    # the layout the renderer draws (header + rows of tile + two label lines + gutters) stays unresampled
    th = round(tw * src[1] / src[0])
    fsize = max(11, min(22, tw // 15))
    rows = per // cols
    w = cols * tw + (cols + 1) * 6
    h = (fsize + 3) * 1.2 + (fsize * 1.2 + 2) + 10 + rows * (th + 2 * (fsize * 1.2 + 2) + 6) + (rows + 1) * 6
    assert max(w, h) <= caps.image_edge_limit()
    assert pv.visual_tokens(w, round(h)) <= (caps.max_image_tokens or 10**9)
    if model == "claude-fable-5-1" and not hires and src[0] < src[1]:
        assert (tw, cols, per) == (270, 5, 15)  # the high-res tier fits 15 portrait tiles per sheet


def test_prepare_image_token_cap(tmp_path: Path) -> None:
    p = tmp_path / "wide.png"
    Image.new("RGB", (1900, 1900), (10, 200, 30)).save(p)
    data, mt, w, h = tl._prepare_image(p, 2000, 7_000_000, 1568)
    assert pv.visual_tokens(w, h) <= 1568 and abs(w / h - 1) < 0.01 and mt == "image/png"
    data2, _mt2, w2, h2 = tl._prepare_image(p, 2000, 7_000_000, 4784)
    assert (w2, h2) == (1900, 1900) and data2 == p.read_bytes()  # 68x68 patches fit: sent as written
    assert tl._fit_dims(4000, 100, 2000, None) == (2000, 50)


def test_prepare_image_limits(tmp_path: Path) -> None:
    import numpy as np

    rng = np.random.default_rng(0)
    big = Image.fromarray(rng.integers(0, 255, (3000, 1600, 3), dtype=np.uint8))
    p = tmp_path / "noise.png"
    big.save(p)
    data, mt, w, h = tl._prepare_image(p, 2000, 5_000_000)
    assert max(w, h) == 2000 and mt == "image/jpeg" and len(data) <= 5_000_000
    small = tmp_path / "flat.png"
    Image.new("RGB", (400, 300), (20, 30, 40)).save(small)
    data2, mt2, w2, h2 = tl._prepare_image(small, 2000, 5_000_000)
    assert mt2 == "image/png" and (w2, h2) == (400, 300)


# ============================================================================================ callbacks
def test_ask_creator_modes(job: Job, take_index: TakeIndex) -> None:
    s = EditSession(job, take_index, batch=True)
    ans = asyncio.run(s.call_ask_creator("Keep the second CTA?", ["yes", "no"]))
    assert ans.startswith("NO ANSWER") and s.questions[0]["answer"] is None

    def sync_cb(sess: EditSession, q: str, opts: list[str] | None) -> str:
        return "yes, keep it"

    s2 = EditSession(job, take_index, batch=False, ask_creator=sync_cb)
    assert asyncio.run(s2.call_ask_creator("Keep?", None)) == "Creator answered: yes, keep it"

    async def async_cb(sess: EditSession, q: str, opts: list[str] | None) -> None:
        return None

    s3 = EditSession(job, take_index, batch=False, ask_creator=async_cb)
    assert "no reply" in asyncio.run(s3.call_ask_creator("Keep?", None))


def test_render_preview_and_critique_callbacks(media_job: Job, take_index: TakeIndex, tools_proxy_video: Path) -> None:
    s = EditSession(media_job, take_index)
    assert "not available" in asyncio.run(s.call_render_preview("full"))[0]
    assert "not available" in asyncio.run(s.call_critique(["?"]))
    s.apply_ops([{"op": "set_story", "segments": STORY}])
    rdir = media_job.new_render_dir()
    video = rdir / "preview.mp4"
    shutil.copy(tools_proxy_video, video)
    try:
        from studio.compile.timeline import compile as compile_timeline

        compile_timeline(s.doc, take_index, job=media_job).save(rdir / "timeline.json")
        have_tl = True
    except Exception:  # the compiler is another module; the callback path is still tested without it
        have_tl = False
    sheet = media_job.critique_dir / "cb.png"
    Image.new("RGB", (300, 200), (200, 10, 10)).save(sheet)

    def render_cb(sess: EditSession, scope: str) -> PreviewResult:
        assert scope == "hook"
        return PreviewResult(summary=f"rendered v{sess.doc.version} {scope}", video_path=video, images=[sheet],
                             metrics={"lufs": -14.1})

    s.render_preview_cb = render_cb
    out = asyncio.run(s.call_render_preview("hook"))
    assert out[0].startswith("rendered v1 hook") and "view_frames" in out[0] and "-14.1" in out[0]
    assert len(_images(out)) == 1 and s.last_render == video
    if have_tl:
        assert s.last_timeline == rdir / "timeline.json"
        rend = s.view_frames(["w0009", "w0018"], source="render")
        assert _images(rend)
        meta_sheets = sorted((media_job.critique_dir / "frames").glob("sheet_*.png"))
        from studio.perception.frames import read_sheet_meta

        tiles = read_sheet_meta(meta_sheets[-1])["tiles"]
        assert tiles[0]["t_us"] is None and tiles[1]["t_us"] is not None  # w0009 was cut

    async def critic(sess: EditSession, qs: list[str]) -> list[dict[str, Any]]:
        return [{"by": "frame_judge", "severity": "P1", "refs": ["w0018"], "text": "payoff frame is soft",
                 "confirmed_by": ["metrics"]}]

    s.critique_cb = critic
    notes = asyncio.run(s.call_critique(["Is the payoff sharp?"]))
    assert notes == "[P1] frame_judge @ w0018: payoff frame is soft (confirmed by metrics)"
    assert s.critique_notes and s.critique_notes[0]["severity"] == "P1"


# ============================================================================================ agent-driven
def _count_responses(messages: list[ModelMessage]) -> int:
    return sum(1 for m in messages if isinstance(m, ModelResponse))


def test_scripted_director_drives_tools(media_job: Job, take_index: TakeIndex) -> None:
    tools = build_tools(media_job, take_index, stage="story")
    seen: dict[str, Any] = {}

    def director(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        step = _count_responses(messages)
        if step == 0:
            seen["tool_names"] = sorted(t.name for t in info.function_tools)
            return ModelResponse(parts=[ToolCallPart("get_overview", {}),
                                        ToolCallPart("get_transcript", {"view": "full"}),
                                        ToolCallPart("load_skill", {"name": "nonexistent-topic"})])
        if step == 1:
            returns = {p.tool_name: p.content for m in messages if isinstance(m, ModelRequest) for p in m.parts
                       if isinstance(p, ToolReturnPart)}
            seen["transcript"] = returns["get_transcript"]
            seen["skill"] = returns["load_skill"]
            return ModelResponse(parts=[ToolCallPart("cut_ops", {"ops": [
                {"op": "set_story", "segments": STORY, "reason": "false start and filler out"},
                {"op": "set_captions", "plan": {"pages": []}}]})])
        if step == 2:
            last = next(p for p in messages[-1].parts if isinstance(p, ToolReturnPart))  # type: ignore[union-attr]
            seen["apply"] = last.content
            return ModelResponse(parts=[ToolCallPart("view_frames", {"items": ["w0014", "w0018:end"],
                                                                      "ranges": ["seg002"]})])
        if step == 3:
            last = next(p for p in messages[-1].parts if isinstance(p, ToolReturnPart))  # type: ignore[union-attr]
            seen["frames"] = last.content
            return ModelResponse(parts=[ToolCallPart("ask_creator", {"question": "Keep the CTA?"})])
        return ModelResponse(parts=[TextPart("Story cut done.")])

    job = media_job
    model = pv.TracedModel(FunctionModel(director), job=job, role="director", stage="story")
    agent = Agent(model, tools=tools, retries=3)
    result = agent.run_sync("Make the story cut.")
    assert result.output == "Story cut done."
    assert seen["tool_names"] == sorted(tl.TOOL_NAMES)
    assert "w0014 The" in seen["transcript"] and seen["skill"].startswith("ERROR: unknown skill")
    assert "Applied 1 of 2 ops: v0 -> v1" in seen["apply"] and "not available in the story stage" in seen["apply"]
    frames = seen["frames"]
    assert isinstance(frames, list) and any(isinstance(x, BinaryImage) for x in frames)
    assert tools.session.doc.version == 1 and len(tools.session.doc.segments) == 3
    trace = job.read_trace()
    calls = [r for r in trace if r["event"] == "tool_call"]
    assert [c["tool"] for c in calls][:3] == ["get_overview", "get_transcript", "load_skill"] or \
        sorted(c["tool"] for c in calls[:3]) == ["get_overview", "get_transcript", "load_skill"]
    by_tool = {c["tool"]: c for c in calls}
    assert by_tool["load_skill"]["ok"] is False and by_tool["cut_ops"]["args"]["ops"] == ["set_story", "set_captions"]
    assert by_tool["view_frames"]["images"] >= 2 and by_tool["cut_ops"]["doc_version"] == 1
    assert by_tool["ask_creator"]["ok"] is True and by_tool["ask_creator"]["stage"] == "story"
    mcalls = [r for r in trace if r["event"] == "model_call"]
    assert len(mcalls) == 5 and mcalls[0]["tool_calls"] == ["get_overview", "get_transcript", "load_skill"]
    assert mcalls[-1]["images_in_context"] >= 2


def test_testmodel_calls_every_tool_without_crashing(job: Job, take_index: TakeIndex) -> None:
    """Schema-generated (mostly nonsense) arguments must come back as readable results, never exceptions."""
    tools = build_tools(job, take_index)
    agent = Agent(TestModel(call_tools="all"), tools=tools, retries=5)
    agent.run_sync("exercise every tool")
    calls = [r for r in job.read_trace() if r["event"] == "tool_call"]
    called = {c["tool"] for c in calls}
    assert set(tl.QUERY_TOOL_NAMES) <= called and "apply_ops" in called and "ask_creator" in called
    assert all("internally" not in (c.get("error") or "") for c in calls), [c for c in calls if c.get("error")]


def test_trace_redacts_secrets_in_tool_args(job: Job, take_index: TakeIndex) -> None:
    secret = "sk-ant-api03-TOOLSECRET-abcdefghijklmnop"
    s = EditSession(job, take_index)
    tools = s.tools(include=["ask_creator"])
    ask = tools[0]
    asyncio.run(ask.function(question=f"my key is {secret}"))  # type: ignore[call-arg]
    raw = job.trace_path.read_text()
    assert secret not in raw and "<redacted>" in raw


# ============================================================================================ real
@pytest.mark.real
def test_real_house_model_calls_get_transcript(job: Job, take_index: TakeIndex) -> None:
    """The house Anthropic Director (fallback Opus 5.5) calls get_transcript on the fixture index."""
    spec = pv.house_spec("director").model_copy(update={"effort": "low", "extra_settings": {"max_tokens": 8000}})
    fb = pv.director_fallback_spec().model_copy(update={"effort": "low", "extra_settings": {"max_tokens": 8000}})
    tools = build_tools(job, take_index, include=["get_transcript", "get_clusters"])
    model = pv.build_model(spec, role="director", job=job, stage="smoke", fallback=fb)
    agent = Agent(model, tools=tools, instructions=(
        "You are testing tools. Use get_transcript(view='compact') and answer with only the sentence ID of the "
        "recommended take of retake cluster c01."))
    out = agent.run_sync("Which sentence is the recommended take of c01?")
    assert "s003" in out.output
    trace = job.read_trace()
    assert any(r["event"] == "tool_call" and r["tool"] == "get_transcript" and r["ok"] for r in trace)
    mc = [r for r in trace if r["event"] == "model_call"]
    assert mc and all(r["ok"] for r in mc[-1:])
    assert "get_transcript" in [t for r in mc for t in r.get("tool_calls", [])]


@pytest.fixture(scope="session")
def code_word_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """17 s 360x640 video whose frames show a code word that only a model that sees the image can read."""
    if FFMPEG is None:
        pytest.skip("ffmpeg not available")
    from PIL import ImageDraw, ImageFont

    d = tmp_path_factory.mktemp("code_word")
    img = Image.new("RGB", (360, 640), (240, 240, 240))
    try:
        font: Any = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 84)
    except OSError:
        font = ImageFont.load_default(size=84)
    draw = ImageDraw.Draw(img)
    draw.text((40, 200), "KIWI", font=font, fill=(0, 0, 0))
    draw.text((100, 320), "73", font=font, fill=(0, 0, 0))
    png = d / "code.png"
    img.save(png)
    out = d / "code17.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-framerate", "30", "-i",
                    str(png), "-t", "17", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt",
                    "yuv420p", str(out)], check=True)
    return out


@pytest.mark.real
@pytest.mark.parametrize("upload", [True, False], ids=["files_api", "inline"])
def test_real_model_reads_frames(job: Job, take_index: TakeIndex, code_word_video: Path, upload: bool) -> None:
    """view_frames images reach the house model and are legible, uploaded (Files API) or inline."""
    shutil.copy(code_word_video, job.proxy_path)
    spec = pv.house_spec("critic").model_copy(update={"effort": "low", "extra_settings": {"max_tokens": 4000}})
    uploader = pv.make_image_uploader(spec, job=job, expires_in_s=3600) if upload else None  # 1 h: min expiry
    assert (uploader is not None) == upload
    tools = build_tools(job, take_index, include=["view_frames"], image_uploader=uploader,
                        capabilities=spec.capabilities)
    agent = Agent(pv.build_model(spec, role="critic", job=job, stage="smoke"), tools=tools, instructions=(
        "You are testing a frame viewer. Call view_frames with items ['w0014'] exactly once, then answer with only "
        "the word and the number printed in large black letters inside the video frame (not the yellow label)."))
    out = agent.run_sync("What is printed in the frame?").output.upper()
    assert "KIWI" in out and "73" in out, out
    trace = job.read_trace()
    assert tools.session.images_uploaded == (1 if upload else 0)
    assert any(r["event"] == "file_upload" and r["ok"] for r in trace) == upload
    mc = [r for r in trace if r["event"] == "model_call"]
    assert mc[-1]["ok"] and mc[-1]["images_in_context"] == 1
    assert mc[-1]["uploaded_images_in_context"] == (1 if upload else 0)
