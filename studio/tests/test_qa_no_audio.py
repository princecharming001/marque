"""QA 2026-10-13: a source with no audio stream gets a silent media/audio.wav at ingest (flag:no_audio); the
transcriber paid ASR on that silence. A job whose media has no audio now yields an empty transcript without a call."""

from __future__ import annotations

import pytest

from studio.jobs import Job
from studio.perception import transcribe as tr
from studio.perception.index import TakeIndex


def test_no_audio_stream_means_no_asr_call_even_with_a_silent_track(work_dir, take_index: TakeIndex,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    job = Job.create("qa-editor-noaudio", work_dir=work_dir)
    job.save_media_info(take_index.media.model_copy(update={"audio": None}))
    job.audio_path.parent.mkdir(parents=True, exist_ok=True)
    job.audio_path.write_bytes(b"RIFF....WAVE")  # the silent track ingest writes

    def boom(*a, **k):
        raise AssertionError("ASR must not be called for a source with no audio stream")

    monkeypatch.setattr(tr, "transcribe_file", boom)
    res = tr.transcribe(job, provider="elevenlabs")
    assert res.words == [] and res.asr.params.get("reason") == "no audio"
