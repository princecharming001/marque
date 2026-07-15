"""A5b/A5c (superintelligence epic) — pure argv/parse helpers in app/audio.py.
No subprocess execution here (that's main.py's job, fail-soft); these tests only
exercise the string-building and JSON-parsing logic, which is what's actually
testable keylessly per the plan's own "arg-builder unit tests" strategy.
"""
from app import audio


# ---------------------------------------------------------------------------
# A5b — loudnorm 2-pass
# ---------------------------------------------------------------------------

def test_loudnorm_pass1_args_shape():
    args = audio.loudnorm_pass1_args("https://cdn/a.mp4")
    assert args[0] == "ffmpeg"
    assert "https://cdn/a.mp4" in args
    af = args[args.index("-af") + 1]
    assert "loudnorm=I=-14.0" in af
    assert "print_format=json" in af
    assert args[-2:] == ["-f", "null"] or args[-1] == "-"


def test_loudnorm_pass1_args_custom_target():
    args = audio.loudnorm_pass1_args("u", target_lufs=-16.0)
    af = args[args.index("-af") + 1]
    assert "I=-16.0" in af


def test_parse_loudnorm_json_extracts_block():
    stderr = 'some ffmpeg noise\n{\n "input_i": "-23.5",\n "input_tp": "-2.1",\n' \
             ' "input_lra": "7.0",\n "input_thresh": "-33.5",\n "target_offset": "0.5"\n}\ntrailing'
    parsed = audio.parse_loudnorm_json(stderr)
    assert parsed is not None
    assert parsed["input_i"] == "-23.5"
    assert parsed["target_offset"] == "0.5"


def test_parse_loudnorm_json_returns_none_on_garbage():
    assert audio.parse_loudnorm_json("no json here at all") is None
    assert audio.parse_loudnorm_json("") is None
    assert audio.parse_loudnorm_json("{not valid json}") is None


def test_loudnorm_pass2_args_builds_measured_filter():
    measured = {"input_i": "-23.5", "input_tp": "-2.1", "input_lra": "7.0",
               "input_thresh": "-33.5", "target_offset": "0.7"}
    args = audio.loudnorm_pass2_args("https://cdn/a.mp4", measured, "/tmp/out.mp4")
    assert args is not None
    assert "-c:v" in args and args[args.index("-c:v") + 1] == "copy"   # video untouched
    af = args[args.index("-af") + 1]
    assert "measured_I=-23.5" in af
    assert "measured_TP=-2.1" in af
    assert "measured_LRA=7.0" in af
    assert "measured_thresh=-33.5" in af
    assert "offset=0.7" in af
    assert args[-1] == "/tmp/out.mp4"


def test_loudnorm_pass2_args_none_when_measured_incomplete():
    assert audio.loudnorm_pass2_args("u", {"input_i": "-23.5"}, "/tmp/out.mp4") is None
    assert audio.loudnorm_pass2_args("u", {}, "/tmp/out.mp4") is None


def test_loudnorm_pass2_args_defaults_offset_when_absent():
    measured = {"input_i": "-23.5", "input_tp": "-2.1", "input_lra": "7.0", "input_thresh": "-33.5"}
    args = audio.loudnorm_pass2_args("u", measured, "/tmp/out.mp4")
    af = args[args.index("-af") + 1]
    assert "offset=0.0" in af


# ---------------------------------------------------------------------------
# A5c — voice polish chain
# ---------------------------------------------------------------------------

def test_voice_polish_args_shape():
    args = audio.voice_polish_args("https://cdn/a.mp4", "/tmp/polished.mp4")
    assert args[0] == "ffmpeg"
    assert "-c:v" in args and args[args.index("-c:v") + 1] == "copy"   # video untouched, duration identical
    af = args[args.index("-af") + 1]
    for stage in ("highpass=f=90", "equalizer=f=450", "equalizer=f=3200",
                  "deesser", "acompressor", "alimiter"):
        assert stage in af
    assert args[-1] == "/tmp/polished.mp4"


def test_voice_polish_args_deterministic():
    a = audio.voice_polish_args("u", "/tmp/o.mp4")
    b = audio.voice_polish_args("u", "/tmp/o.mp4")
    assert a == b
