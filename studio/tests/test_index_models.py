from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import ValidationError

from studio.jobs import Job
from studio.media.models import AudioInfo, ColorInfo, MediaInfo
from studio.perception import index as ixmod
from studio.perception.index import (
    AsrResult,
    Cluster,
    FaceTrack,
    FaceTrackPoint,
    Gap,
    TakeIndex,
    Word,
    build_index,
    load_index,
    save_index,
)


# ------------------------------------------------------------------ media info
def test_media_info_properties_and_roundtrip(media_info: MediaInfo):
    assert media_info.is_portrait and media_info.has_audio and not media_info.hdr
    assert media_info.aspect == Fraction(9, 16)
    assert media_info.frame_count == round(media_info.duration_us * 30 / 1e6)
    js = media_info.model_dump_json()
    assert '"fps":"30/1"' in js
    assert MediaInfo.model_validate_json(js) == media_info
    no_audio = media_info.model_copy(update={"audio": None, "display_aspect": None})
    assert not no_audio.has_audio
    assert no_audio.aspect == Fraction(1080, 1920)


def test_media_info_validation():
    with pytest.raises(ValidationError):
        MediaInfo(path="x", width=0, height=10, fps=30, duration_us=1)
    with pytest.raises(ValidationError):
        MediaInfo(path="x", width=10, height=10, fps=30, duration_us=1, bogus=1)  # type: ignore[call-arg]
    m = MediaInfo(path="x", width=10, height=10, fps="30000/1001", duration_us=1,
                  color=ColorInfo(transfer="arib-std-b67", hdr=True, hdr_format="hlg"),
                  audio=AudioInfo(codec="aac", sample_rate=48000, channels=2))
    assert m.fps == Fraction(30000, 1001) and m.hdr
    assert ColorInfo.hdr_from_transfer("arib-std-b67") == (True, "hlg")
    assert ColorInfo.hdr_from_transfer("smpte2084") == (True, "pq")
    assert ColorInfo.hdr_from_transfer("bt709") == (False, None)
    assert ColorInfo.hdr_from_transfer(None) == (False, None)


# ------------------------------------------------------------------ model validation
def test_word_validation():
    Word(id="w0001", text="hi", start_us=0, end_us=10)
    Word(id="w12345", text="hi", start_us=0, end_us=0)
    for bad in (dict(id="w1", text="x", start_us=0, end_us=1), dict(id="w0001", text="x", start_us=5, end_us=1),
                dict(id="w0001", text="x", start_us=0, end_us=1, kind="noise"),
                dict(id="w0001", text="x", start_us=0, end_us=1, emphasis=1.5),
                dict(id="w0001", text="x", start_us=-1, end_us=1)):
        with pytest.raises(ValidationError):
            Word(**bad)


def test_word_display():
    assert Word(id="w0001", text="um", start_us=0, end_us=1, kind="filler").display() == "{um}"
    assert Word(id="w0001", text="Um,", start_us=0, end_us=1, kind="filler").display() == "{Um}"
    assert Word(id="w0001", text="laughter", start_us=0, end_us=1, kind="event").display() == "(laughter)"
    assert Word(id="w0001", text="(sigh)", start_us=0, end_us=1, kind="event").display() == "(sigh)"
    assert Word(id="w0001", text="restr", start_us=0, end_us=1, kind="cutoff").display() == "restr-"
    assert Word(id="w0001", text="restr-", start_us=0, end_us=1, kind="cutoff").display() == "restr-"
    assert Word(id="w0001", text="hello", start_us=0, end_us=1).display() == "hello"


def test_gap_validation():
    Gap(id="g0001", after_word_id="w0001", before_word_id="w0002", start_us=10, end_us=20, snap_us=15)
    with pytest.raises(ValidationError):
        Gap(id="g0001", after_word_id="w0001", before_word_id="w0002", start_us=10, end_us=20, snap_us=25)
    with pytest.raises(ValidationError):
        Gap(id="g0001", after_word_id=None, before_word_id=None, start_us=10, end_us=20, snap_us=15)
    with pytest.raises(ValidationError):
        Gap(id="g1", after_word_id="w0001", before_word_id=None, start_us=10, end_us=20, snap_us=15)


def test_cluster_recommended_must_be_member():
    with pytest.raises(ValidationError):
        Cluster(id="c01", sentence_ids=["s001"], recommended_sentence_id="s002")


def test_take_index_consistency_checks(take_index: TakeIndex):
    data = take_index.model_dump()
    bad = dict(data)
    bad["words"] = [dict(w) for w in data["words"]]
    bad["words"][1]["id"] = bad["words"][0]["id"]
    with pytest.raises(ValidationError, match="duplicate word id"):
        TakeIndex.model_validate(bad)
    bad = dict(data)
    bad["words"] = list(reversed(data["words"]))
    with pytest.raises(ValidationError, match="time order"):
        TakeIndex.model_validate(bad)
    bad = dict(data)
    bad["gaps"] = [dict(g) for g in data["gaps"]]
    bad["gaps"][2]["after_word_id"] = "w9999"
    with pytest.raises(ValidationError, match="unknown word"):
        TakeIndex.model_validate(bad)
    bad = dict(data)
    bad["clusters"] = [dict(c) for c in data["clusters"]]
    bad["clusters"][0]["sentence_ids"] = ["s002", "s099"]
    bad["clusters"][0]["recommended_sentence_id"] = "s002"
    with pytest.raises(ValidationError, match="unknown sentence"):
        TakeIndex.model_validate(bad)


# ------------------------------------------------------------------ query API
def test_fixture_shape(take_index: TakeIndex):
    ix = take_index
    assert len(ix.words) == 41 and len(ix.sentences) == 5 and len(ix.clusters) == 1
    assert [w.kind for w in ix.words if w.kind != "word"] == ["cutoff", "filler"]
    assert not ix.sentence("s002").complete
    assert ix.cluster("c01").recommended_sentence_id == "s003"
    assert ix.gaps[0].after_word_id is None and ix.gaps[-1].before_word_id is None


def test_lookups(take_index: TakeIndex):
    ix = take_index
    assert ix.word("w0018").text == "restraint."
    assert ix.word_pos("w0001") == 0 and ix.word_pos("w0041") == 40
    assert ix.has_word("w0041") and not ix.has_word("w0042")
    assert ix.word_map["w0003"].text == "cut"
    assert ix.gap("g0004").kind == "silence"
    assert ix.gap_map["g0006"].has_breath
    assert ix.gap_after("w0013").id == "g0004"
    assert ix.gap_before("w0014").id == "g0004"
    assert ix.gap_after("w0001") is None
    assert ix.next_word("w0001").id == "w0002" and ix.next_word("w0041") is None
    assert ix.prev_word("w0001") is None and ix.prev_word("w0002").id == "w0001"
    assert ix.sentence("s003").word_ids[-1] == "w0018"
    assert ix.sentence_of("w0015").id == "s003"
    assert ix.sentence_map["s005"].text.startswith("If this")
    assert ix.cluster_map["c01"].sentence_ids == ["s002", "s003"]
    for fn, arg in ((ix.word, "w9999"), (ix.gap, "g9999"), (ix.sentence, "s999"), (ix.cluster, "c99"),
                    (ix.word_pos, "nope")):
        with pytest.raises(KeyError):
            fn(arg)


def test_get_words_and_ranges(take_index: TakeIndex):
    ix = take_index
    ws = ix.get_words("w0014", "w0018")
    assert [w.text for w in ws] == ["The", "real", "secret", "is", "restraint."]
    assert len(ix.get_words()) == 41
    assert ix.get_words("w0040")[-1].id == "w0041"
    assert ix.get_words(None, "w0002")[0].id == "w0001"
    assert ix.word_ids("w0001", "w0003") == ["w0001", "w0002", "w0003"]
    with pytest.raises(ValueError):
        ix.get_words("w0010", "w0005")
    with pytest.raises(KeyError):
        ix.get_words("w0001", "w9999")


def test_gaps_queries(take_index: TakeIndex):
    ix = take_index
    assert [g.id for g in ix.get_gaps(min_ms=600)] == ["g0003", "g0004", "g0006", "g0009"]
    assert len(ix.get_gaps()) == len(ix.gaps)
    assert [g.id for g in ix.gaps_between("w0014", "w0018")] == ["g0005"]
    assert [g.id for g in ix.gaps_between("w0001", "w0014")] == ["g0002", "g0003", "g0004"]
    assert ix.gaps_between("w0001", "w0001") == []
    with pytest.raises(ValueError):
        ix.gaps_between("w0014", "w0001")


def test_get_prosody(take_index: TakeIndex):
    rows = take_index.get_prosody(["w0018", "w0019"])
    assert rows[0]["id"] == "w0018" and rows[0]["emphasis"] == 0.95 and rows[0]["f0_z"] == 1.4
    assert rows[1]["kind"] == "filler"
    assert set(rows[0]) == {"id", "text", "kind", "f0_z", "int_z", "dur_z", "emphasis"}
    with pytest.raises(KeyError):
        take_index.get_prosody(["w9999"])


def test_visual_events(take_index: TakeIndex):
    ix = take_index
    assert [e.kind for e in ix.get_visual_events()] == ["blink", "look_away"]
    assert [e.kind for e in ix.get_visual_events("look_away")] == ["look_away"]
    assert ix.get_visual_events("reading") == []
    assert [e.kind for e in ix.get_visual_events(from_word="w0009", to_word="w0013")] == ["look_away"]
    assert ix.get_visual_events(from_word="w0030", to_word="w0041") == []


def test_time_to_word_mapping(take_index: TakeIndex):
    ix = take_index
    w = ix.word("w0010")
    assert ix.word_at(w.start_us).id == "w0010"
    assert ix.word_at(w.end_us - 1).id == "w0010"
    g = ix.gap("g0004")
    assert ix.word_at(g.snap_us) is None
    assert [x.id for x in ix.words_between_us(g.start_us - 1, g.end_us + 1)] == ["w0013", "w0014"]
    assert ix.words_between_us(g.start_us, g.end_us) == []


def test_face_at_interpolates_track(take_index: TakeIndex):
    ix = take_index
    pts = ix.visual.face_track.points
    a, b = pts[3], pts[4]
    mid = (a.t_us + b.t_us) // 2
    box = ix.face_at(mid)
    assert box is not None
    assert box.cx == pytest.approx((a.cx + b.cx) / 2)
    assert box.w == pytest.approx(0.34)
    assert ix.face_at(0).cx == pytest.approx(pts[0].cx)
    assert ix.face_at(10**12).cx == pytest.approx(pts[-1].cx)
    exact = ix.face_at(a.t_us)
    assert exact.cx == pytest.approx(a.cx) and exact.cy == pytest.approx(a.cy)


def test_face_track_linear_and_fallbacks(take_index: TakeIndex):
    tr = FaceTrack(points=[FaceTrackPoint(t_us=0, cx=0.2, cy=0.4, w=0.1, h=0.2),
                           FaceTrackPoint(t_us=1000, cx=0.6, cy=0.2, w=0.3, h=0.2)])
    box = tr.at(250)
    assert box.cx == pytest.approx(0.3) and box.cy == pytest.approx(0.35) and box.w == pytest.approx(0.15)
    assert box.x == pytest.approx(0.3 - 0.075)
    assert FaceTrack().at(5) is None
    ix = take_index.model_copy(deep=True)
    ix.visual.face_track = None
    near = ix.face_at(1_234_567)  # nearest raw sample
    assert near is not None
    ix.visual.samples = []
    assert ix.face_at(0) is None


def test_render_transcript_full(take_index: TakeIndex):
    txt = take_index.render_transcript("full")
    lines = txt.splitlines()
    assert lines[0] == "[g0001 0.30s silence]"
    assert lines[1].startswith("s001 0:00.30-")
    assert "w0005 videos [g0002 0.18s] w0006 way" in txt
    assert "s002 " in txt and "[c01 take 1/2, incomplete]" in txt
    assert "[c01 take 2/2, recommended]" in txt
    assert "w0013 restr- [g0004 0.90s silence]" in txt
    assert "w0018 restraint. [g0006 0.80s breath]" in txt
    assert "w0019 {Um} [g0007 0.25s] w0020 keep" in txt
    assert txt.count("\n") == 10  # leading gap + 5 x (header + words)


def test_render_transcript_compact(take_index: TakeIndex):
    txt = take_index.render_transcript("compact")
    assert "s001 (w0001-w0008) Most people cut their videos way too much. [g0003 0.70s]" in txt
    assert "[g0002" not in txt  # 180 ms < 250 ms default threshold
    assert "s002 (w0009-w0013) [c01 1/2, incomplete] The real secret is restr- [g0004 0.90s silence]" in txt
    assert "s003 (w0014-w0018) [c01 2/2 rec]" in txt
    assert "{Um} [g0007 0.25s] keep" in txt
    all_gaps = take_index.render_transcript("compact", min_gap_ms=0)
    assert "[g0002 0.18s]" in all_gaps and "[g0005 0.12s]" in all_gaps
    none_gaps = take_index.render_transcript("full", min_gap_ms=5000)
    assert "[g" not in none_gaps


def test_render_transcript_filtered(take_index: TakeIndex):
    ix = take_index
    kept = ix.word_ids("w0001", "w0008") + ix.word_ids("w0020", "w0028")
    txt = ix.render_transcript("compact", word_ids=kept, min_gap_ms=0)
    assert "s002" not in txt and "s003" not in txt
    assert "g0001" not in txt  # leading silence is not part of a cut view
    assert "[g0003" not in txt and "[g0007" not in txt  # boundary gaps (neighbour cut)
    assert "s004 (w0020-w0028) keep the pauses [g0008 0.15s] that matter" in txt
    assert "{Um}" not in txt
    with pytest.raises(KeyError):
        ix.render_transcript(word_ids=["w9999"])
    with pytest.raises(ValueError):
        ix.render_transcript("tiny")  # type: ignore[arg-type]


def test_render_transcript_words_without_sentences(media_info: MediaInfo):
    ix = TakeIndex(media=media_info, words=[Word(id="w0001", text="a", start_us=0, end_us=10),
                                            Word(id="w0002", text="b", start_us=20, end_us=30)])
    assert ix.render_transcript("compact") == "s--- (w0001-w0002) a b"
    assert ix.plain_text() == "a b"


def test_equality_ignores_lookup_caches(make_take_index):
    a, b = make_take_index(), make_take_index()
    a.word("w0001")  # builds a's caches only
    assert a == b
    b.words[0].text = "Many"
    assert a != b
    assert a != "not an index"


def test_cache_refreshes_after_list_replacement(take_index: TakeIndex):
    ix = take_index
    assert ix.has_word("w0041")
    ix.words = ix.words[:10]
    ix.sentences = []
    ix.clusters = []
    ix.gaps = [g for g in ix.gaps if g.id in ("g0001", "g0002")]
    assert not ix.has_word("w0041") and ix.word_pos("w0010") == 9


def test_plain_text(take_index: TakeIndex):
    assert take_index.plain_text().startswith("Most people cut")
    assert "Um," not in take_index.plain_text(include_fillers=False)


# ------------------------------------------------------------------ persistence
def test_save_load_roundtrip(tmp_path: Path, take_index: TakeIndex, job: Job):
    p = save_index(take_index, tmp_path / "ix.json")
    back = load_index(p)
    assert back.model_dump() == take_index.model_dump()
    assert back.word("w0018").prosody.f0_z == 1.4
    assert back.media.fps == Fraction(30)
    save_index(take_index, job)
    assert load_index(job).words[0].id == "w0001"
    assert load_index(job.root).words[0].id == "w0001"


def test_room_tone_ranges_roundtrip_as_tuples(take_index: TakeIndex):
    js = take_index.model_dump_json()
    back = TakeIndex.model_validate_json(js)
    assert back.audio.room_tone_ranges_us == take_index.audio.room_tone_ranges_us
    assert all(isinstance(r, tuple) for r in back.audio.room_tone_ranges_us)


# ------------------------------------------------------------------ orchestrator wiring
def test_build_index_calls_stages_in_order(monkeypatch: pytest.MonkeyPatch, job: Job, take_index: TakeIndex):
    from studio.perception import (
        audio_metrics,
        gaps,
        prosody,
        takes,
        transcribe,
        visual,
    )

    calls: list[str] = []
    ix = take_index
    raw_words = [w.model_copy(update={"sentence_id": None, "cluster_id": None, "prosody": None}) for w in ix.words]

    def fake_transcribe(j, *, provider=None, keyterms=None, settings=None):
        calls.append(f"transcribe:{provider}:{list(keyterms or [])}")
        return AsrResult(words=raw_words, asr=ix.asr)

    def fake_refine(j, words):
        calls.append("refine")
        return words

    def fake_detect(j, words):
        calls.append("gaps")
        return ix.gaps

    def fake_sentences(words, **_kw):
        calls.append("sentences")
        sid = {w.id: w.sentence_id for w in ix.words}
        return [w.model_copy(update={"sentence_id": sid[w.id]}) for w in words], ix.sentences

    def fake_cluster(words, sentences, *, settings=None):
        calls.append("clusters")
        cid = {w.id: w.cluster_id for w in ix.words}
        return [w.model_copy(update={"cluster_id": cid[w.id]}) for w in words], sentences, ix.clusters

    def fake_prosody(j, words, sentences):
        calls.append("prosody")
        return words, ix.energy

    def fake_audio(j, *, gaps=None, words=None):
        assert words, "build_index passes the words (speech level, per-word SNR)"
        calls.append(f"audio:{len(gaps or [])}")
        return ix.audio

    def fake_visual(j, *, sample_fps=10.0):
        calls.append("visual")
        return ix.visual

    monkeypatch.setattr(transcribe, "transcribe", fake_transcribe)
    monkeypatch.setattr(gaps, "refine_word_boundaries", fake_refine)
    monkeypatch.setattr(gaps, "detect_gaps", fake_detect)
    monkeypatch.setattr(takes, "segment_sentences", fake_sentences)
    monkeypatch.setattr(takes, "cluster_takes", fake_cluster)
    monkeypatch.setattr(prosody, "analyze_prosody", fake_prosody)
    monkeypatch.setattr(audio_metrics, "measure_audio", fake_audio)
    monkeypatch.setattr(visual, "analyze_visual", fake_visual)
    job.index_path.unlink()
    out = build_index(job, asr_provider="elevenlabs", keyterms=["Yunicorn"])
    assert calls == ["transcribe:elevenlabs:['Yunicorn']", "refine", "gaps", "sentences", "clusters", "prosody",
                     f"audio:{len(ix.gaps)}", "visual"]
    assert out.words[13].sentence_id == "s003" and out.words[13].cluster_id == "c01"
    assert out.transcript_text.startswith("Most people cut")
    assert job.index_path.exists()
    assert load_index(job).model_dump() == out.model_dump()
    assert [r["event"] for r in job.read_trace()] == ["stage", "stage"]


def test_id_helpers():
    assert ixmod.word_id(1) == "w0001" and ixmod.word_id(12345) == "w12345"
    assert ixmod.gap_id(7) == "g0007" and ixmod.sentence_id(3) == "s003" and ixmod.cluster_id(2) == "c02"
    assert ixmod.WORD_ID_RE.match("w0001") and not ixmod.WORD_ID_RE.match("w01")
