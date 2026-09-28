from __future__ import annotations

import json
import re
import threading
from fractions import Fraction
from pathlib import Path

import pytest

from studio.config import reset_settings
from studio.doc.model import CutDocument, new_document
from studio.jobs import (
    SUBDIRS,
    Job,
    JobError,
    new_job_id,
    read_json,
    to_jsonable,
    write_json_atomic,
)
from studio.media.models import MediaInfo
from studio.perception.index import TakeIndex


def test_new_job_id_format_and_uniqueness():
    ids = {new_job_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(re.match(r"^\d{8}-\d{6}-[0-9a-f]{6}$", i) for i in ids)


def test_create_layout(work_dir: Path):
    job = Job.create(work_dir=work_dir, meta={"source": "clip.mov"})
    assert job.root.parent == work_dir.resolve()
    for d in SUBDIRS:
        assert (job.root / d).is_dir()
    for d in ("broll", "music", "sfx"):
        assert (job.assets_dir / d).is_dir()
    meta = job.meta
    assert meta["id"] == job.id and meta["meta"] == {"source": "clip.mov"}
    assert meta["created_at"].endswith("Z")
    assert job.mezz_path == job.root / "media" / "mezz.mov"
    assert job.audio_path == job.root / "media" / "audio.wav"
    assert job.proxy_path == job.root / "media" / "proxy.mp4"
    assert job.index_path == job.root / "index" / "take_index.json"
    assert job.trace_path == job.root / "trace.jsonl"
    assert job.report_path == job.root / "report.md"
    assert job.oplog_path == job.root / "doc" / "oplog.jsonl"
    assert job.original_path is None
    (job.media_dir / "original.mov").write_bytes(b"x")
    assert job.original_path == job.media_dir / "original.mov"


def test_create_uses_settings_work_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STUDIO_WORK_DIR", str(tmp_path / "wd"))
    reset_settings()
    job = Job.create("abc")
    assert job.root == (tmp_path / "wd" / "abc").resolve()


def test_create_rejects_bad_ids_and_duplicates(work_dir: Path):
    for bad in ("", "../x", "a/b", ".hidden", "x" * 200, "has space"):
        with pytest.raises(JobError):
            Job.create(bad, work_dir=work_dir)
    Job.create("dup", work_dir=work_dir)
    with pytest.raises(JobError):
        Job.create("dup", work_dir=work_dir)
    again = Job.create("dup", work_dir=work_dir, exist_ok=True)
    assert again.id == "dup"


def test_open_by_path_and_id(work_dir: Path):
    job = Job.create("openme", work_dir=work_dir)
    assert Job.open(job.root) == job
    assert Job.open("openme", work_dir=work_dir) == job
    with pytest.raises(JobError):
        Job.open(work_dir / "nope")
    (work_dir / "notajob").mkdir()
    with pytest.raises(JobError):
        Job.open(work_dir / "notajob")


def test_path_escape_rejected(job: Job):
    assert job.path("media", "x.json") == job.root / "media" / "x.json"
    with pytest.raises(JobError):
        job.path("..", "other")
    with pytest.raises(JobError):
        job.path("/etc/passwd")


def test_json_helpers_roundtrip_models_and_fractions(job: Job, take_index: TakeIndex):
    p = job.save_json("index/extra.json", {"fps": Fraction(30000, 1001), "path": Path("/a/b"), "s": {3, 1},
                                             "media": take_index.media})
    data = read_json(p)
    assert data["fps"] == "30000/1001" and data["path"] == "/a/b" and data["s"] == [1, 3]
    assert data["media"]["fps"] == "30/1"
    assert job.load_json("index/extra.json") == data
    assert job.load_json("index/missing.json", default=None) is None
    with pytest.raises(JobError):
        job.load_json("index/missing.json")
    # atomic write leaves no temp files behind
    assert not [x for x in p.parent.iterdir() if x.name.startswith(".")]


def test_to_jsonable_numpy():
    np = pytest.importorskip("numpy")
    assert to_jsonable({"a": np.float32(0.5), "b": [np.int64(3)]}) == {"a": 0.5, "b": [3]}


def test_write_json_atomic_overwrites(tmp_path: Path):
    p = tmp_path / "d" / "f.json"
    write_json_atomic(p, {"a": 1})
    write_json_atomic(p, {"a": 2})
    assert json.loads(p.read_text()) == {"a": 2}


def test_trace_appends_and_redacts(job: Job, monkeypatch: pytest.MonkeyPatch):
    secret = "sk-ant-trace-0123456789"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    reset_settings()
    job.trace("model_call", role="director", provider="anthropic", model="claude-opus-5-5", input_tokens=10,
              output_tokens=5, latency_ms=12.5, tool_calls=["cut_ops"], note=f"key was {secret}",
              headers={"x-api-key": secret})
    job.trace("stage", stage="index")
    lines = job.trace_path.read_text().splitlines()
    assert len(lines) == 2
    assert secret not in job.trace_path.read_text()
    rec = job.read_trace()[0]
    assert rec["event"] == "model_call" and rec["role"] == "director" and rec["input_tokens"] == 10
    assert rec["note"] == "key was <redacted>" and rec["headers"]["x-api-key"] == "<redacted>"
    assert "ts" in rec and "t" in rec


def test_trace_concurrent_appends_are_whole_lines(job: Job):
    def worker(k: int) -> None:
        for i in range(50):
            job.trace("tick", worker=k, i=i, pad="x" * 200)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    recs = job.read_trace()
    assert len(recs) == 400
    assert {(r["worker"], r["i"]) for r in recs} == {(k, i) for k in range(8) for i in range(50)}


def test_media_info_and_index_persistence(work_dir: Path, take_index: TakeIndex):
    job = Job.create("persist", work_dir=work_dir)
    with pytest.raises(JobError):
        job.load_media_info()
    with pytest.raises(JobError):
        job.load_index()
    job.save_media_info(take_index.media)
    job.save_index(take_index)
    assert job.load_media_info() == take_index.media
    assert isinstance(job.load_media_info(), MediaInfo)
    back = job.load_index()
    assert back.model_dump() == take_index.model_dump()


def test_versioned_docs(job: Job, cut_doc: CutDocument):
    assert job.doc_versions() == [] and job.latest_doc_version() is None
    with pytest.raises(JobError):
        job.load_doc()
    v0 = new_document(job.id)
    job.save_doc(v0)
    job.save_doc(cut_doc)  # version 1
    assert job.doc_versions() == [0, 1]
    assert job.latest_doc_version() == 1
    assert job.load_doc() == cut_doc
    assert job.load_doc(0) == v0
    with pytest.raises(JobError):
        job.load_doc(7)
    # identical re-save is a no-op, a different one is refused unless overwrite
    job.save_doc(cut_doc)
    changed = cut_doc.model_copy(update={"created_by": "someone else"})
    with pytest.raises(JobError):
        job.save_doc(changed)
    job.save_doc(changed, overwrite=True)
    assert job.load_doc(1).created_by == "someone else"
    with pytest.raises(JobError):
        job.doc_path(-1)
    # v10 sorts numerically, not lexically
    job.save_doc(cut_doc.model_copy(update={"version": 10}))
    assert job.doc_versions() == [0, 1, 10]


def test_oplog_append_and_read(job: Job):
    job.append_oplog([{"op": {"op": "cut_words"}, "applied": True}])
    job.append_oplog([{"op": {"op": "set_gap"}, "applied": False}, {"op": {"op": "note"}, "applied": True}])
    job.append_oplog([])
    log = job.read_oplog()
    assert [e["op"]["op"] for e in log] == ["cut_words", "set_gap", "note"]


def test_render_dirs(job: Job):
    assert job.latest_render_dir() is None and job.render_numbers() == []
    r1 = job.new_render_dir()
    r2 = job.new_render_dir()
    assert r1.name == "r1" and r2.name == "r2" and r1.is_dir()
    (job.renders_dir / "r10").mkdir()
    (job.renders_dir / "junk").mkdir()
    assert job.render_numbers() == [1, 2, 10]
    assert job.latest_render_dir() == job.render_dir(10)
    assert job.new_render_dir().name == "r11"


def test_update_meta_and_log_path(job: Job):
    job.update_meta(platform="tiktok", fps=Fraction(30))
    assert job.meta["meta"]["platform"] == "tiktok"
    assert job.meta["meta"]["fps"] == "30/1"
    assert job.log_path("ffmpeg") == job.logs_dir / "ffmpeg.log"
    assert job.log_path("x.txt") == job.logs_dir / "x.txt"


def test_asset_registry(job: Job):
    from studio.doc.model import AssetRef, Licence

    lic = Licence(name="Pexels License", source="pexels")
    a = job.register_asset(AssetRef(source="pexels", source_id="2499611", path="assets/broll/px.mp4", licence=lic))
    assert a.id == "pexels_2499611"
    assert (job.assets_dir / "registry" / "pexels_2499611.json").exists()
    assert job.load_asset("pexels_2499611") == a
    assert job.load_asset("missing") is None
    assert job.load_asset("../../etc/passwd") is None
    b = job.register_asset(AssetRef(source="creator upload!", kind="image", licence=Licence(name="creator-owned")),
                           asset_id="logo_1")
    assert b.id == "logo_1"
    c = job.register_asset(AssetRef(source="higgs field"))
    assert c.id.startswith("higgs-field_") and job.load_asset(c.id) == c
    assert [x.id for x in job.list_assets()] == sorted([a.id, b.id, c.id])
    job.register_asset(a)  # identical re-register is fine
    with pytest.raises(JobError):
        job.register_asset(a.model_copy(update={"path": "assets/broll/other.mp4"}))
    job.register_asset(a.model_copy(update={"path": "assets/broll/other.mp4"}), overwrite=True)
    assert job.load_asset(a.id).path == "assets/broll/other.mp4"
    with pytest.raises(JobError):
        job.register_asset(a, asset_id="bad id")
