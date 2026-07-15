"""Table-driven tests for the v2 speakability lint (prompts.flag_stage_direction +
speakability_report). Both polarities: bodies that MUST flag, and near-miss bodies that
MUST NOT (the false-positive guard)."""
import prompts

BAD_CASES = [
    ("meta_narration_1", "Here I'd break down why most diets fail. It's not willpower, it's math.",
     "meta-narration"),
    ("meta_narration_2", "This is where I get into the three mistakes everyone makes with cardio timing.",
     "meta-narration"),
    ("meta_narration_3", "I'll cover the three biggest myths and why they persist.",
     "meta-narration"),
    ("coaching", "You want to open with a bold claim, then hit them with the data.",
     "coaching"),
    ("outline_label", "Step 1 — the hook.\n\nStep 2 — the reveal.",
     "outline"),
    ("sequencing_scaffold", "First, the claim. Then, the proof. Finally, the takeaway.",
     "sequencing scaffold"),
    ("intent", "The idea is to contrast what people expect with what actually works.",
     "editorial intent"),
    ("bulleted_summary", "- the myth\n- the evidence\n- the fix",
     "bulleted content summary"),
    ("imperative_directive", "Demonstrate the move on camera. Highlight the key number.",
     "imperative directive"),
    ("visual_artifact", "Picture the graph going up and to the right.",
     "visual artifact"),
    # v1 families still caught
    ("v1_talk_about", "Talk about how protein timing is a myth.", "stage direction"),
    ("v1_beat_sheet", "Beat 1: the claim.\n\nBeat 2: the proof.", "stage direction"),
]

GOOD_CASES = [
    ("teaser", "I'll show you what happened next. Three weeks in, my knees stopped hurting for the first time in years."),
    ("picture_this", "Picture this: you're three weeks in and the scale hasn't moved, but your jeans fit different."),
    ("two_beat_sequence", "First, you're going to hate this. Then you'll thank me for it in a month."),
    ("spoken_ordinal", "One: stop skipping breakfast. It's the difference between a 10am crash and a steady afternoon."),
    ("plain_body", "Everyone says budget first. That's exactly why you're broke. Here's the flip."),
    ("broll_cue_whitelisted", "The market moves in cycles. [broll: cut to a chart] But the fundamentals never change."),
]


def test_bad_bodies_are_flagged():
    for name, body, expect_substr in BAD_CASES:
        reason = prompts.flag_stage_direction(body)
        assert reason is not None, f"{name}: expected a flag, got None for body={body!r}"
        assert expect_substr in reason, f"{name}: expected {expect_substr!r} in reason, got {reason!r}"


def test_good_bodies_pass_clean():
    for name, body in GOOD_CASES:
        reason = prompts.flag_stage_direction(body)
        assert reason is None, f"{name}: expected no flag, got {reason!r} for body={body!r}"


def test_speakability_report_shape():
    dirty = {"hook": "fine hook", "body": "Demonstrate the move on camera.", "style": "talking_head"}
    report = prompts.speakability_report(dirty)
    assert report["violations"], "dirty body should produce at least one violation"
    assert report["families"] == report["violations"]

    clean = {"hook": "fine hook", "body": "This is a totally normal thing to say out loud.", "style": "talking_head"}
    report = prompts.speakability_report(clean)
    assert report["violations"] == []


def test_hook_dict_shape_supported():
    # Some callers pass hook as {"text": "..."} rather than a bare string.
    sc = {"hook": {"text": "Demonstrate the move on camera."}, "body": "fine body text here", "style": "talking_head"}
    report = prompts.speakability_report(sc)
    assert report["violations"], "hook dict shape should still be linted"


def test_empty_body_is_none():
    assert prompts.flag_stage_direction("") is None
    assert prompts.flag_stage_direction(None) is None
