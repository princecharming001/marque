"""Unit tests for the cuts-only graders — fixtures built from the real prod
incident (ef4823cd: swallowed hook) and the owner's two complaint classes."""
from eval.cut_qc import grade_cuts, kept_word_set, consistency

def mk_words(script, ms_per_word=400):
    return [{"word": w, "start_ms": i * ms_per_word, "end_ms": i * ms_per_word + 300}
            for i, w in enumerate(script.split())]

def classes(f):
    return [x["class"] for x in f]


def test_overcut_unique_sentence_flags():
    # "Everyone tries to pair fusion by taste." is cut and never reappears.
    words = mk_words("Everyone tries to pair fusion by taste. That's why it collapses today.")
    # word 7 = "That's" -> frame 84 (400ms*7*30/1000)
    edl = {"segments": [{"src_in": 84, "src_out": 400}], "drops": []}
    f = grade_cuts(edl, words)
    assert "overcut_content" in classes(f)


def test_true_retake_cut_is_clean():
    # The cut sentence's content RECURS in kept text (dedupe was right) — no flag.
    words = mk_words("Everyone tries to pair fusion by taste badly. "
                     "Everyone tries to pair fusion by taste. And more.")
    edl = {"segments": [{"src_in": 96, "src_out": 500}], "drops": []}   # cut take 1
    f = grade_cuts(edl, words)
    assert "overcut_content" not in classes(f)


def test_overcut_partial_mid_sentence():
    # Middle of one sentence sliced out ("bits and pieces cut off").
    words = mk_words("The whole point of structure is fat acid and heat working together always.")
    # cut words 4..8 ("is fat acid and heat"): frames 48..107
    edl = {"segments": [{"src_in": 0, "src_out": 48}, {"src_in": 108, "src_out": 500}],
           "drops": []}
    f = grade_cuts(edl, words)
    assert "overcut_partial" in classes(f)


def test_undercut_stumble_flags():
    # A kept dangling fragment restarting as the next sentence = kept stumble.
    words = mk_words("Most fusion fails the same— Most fusion fails for the same reason. More talk here.")
    edl = {"segments": [{"src_in": 0, "src_out": 700}], "drops": []}   # everything kept
    f = grade_cuts(edl, words)
    assert "undercut_stumble" in classes(f)


def test_undercut_dupe_flags():
    words = mk_words("You need fat acid and heat to build it. "
                     "You need fat acid and heat to build this. Final point stands.")
    edl = {"segments": [{"src_in": 0, "src_out": 900}], "drops": []}
    f = grade_cuts(edl, words)
    assert "undercut_dupe" in classes(f)


def test_clean_edit_no_findings():
    words = mk_words("Here is a clean sentence. Here is another different thought entirely. Done now.")
    edl = {"segments": [{"src_in": 0, "src_out": 900}], "drops": []}
    assert grade_cuts(edl, words) == []


def test_consistency_metric():
    words = mk_words("One two three four five six seven eight nine ten.")
    e1 = {"segments": [{"src_in": 0, "src_out": 200}], "drops": []}
    e2 = {"segments": [{"src_in": 0, "src_out": 200}], "drops": [{"src_in": 0, "src_out": 24}]}
    a, b = kept_word_set(e1, words), kept_word_set(e2, words)
    assert consistency(a, a) == 1.0
    assert consistency(a, b) < 1.0


def test_snap_cut_end_restores_clean_sentence_head():
    """Round-1 real case (owner-fusion): the cut removed 'most fusion fails for
    the same' keeping the orphan 'reason.' — no restart inside the sentence, so
    the cut end snaps to the SENTENCE start (whole clean take restored)."""
    from app.edl import snap_cut_ends_to_takes, ms_to_frame
    words = mk_words("Bad stumble words here now— most fusion fails for the same reason. More talk.")
    # "reason." is word 11 -> frame 132 with 400ms words. Cut 0..132.
    edl = {"segments": [{"src_in": 132, "src_out": 500}],
           "drops": [{"src_in": 0, "src_out": 132, "reason": "false_start"}]}
    out = snap_cut_ends_to_takes(edl, words)
    # 'most' is word 5 -> frame 60: the cut must now end there.
    assert out["drops"][0]["src_out"] == 60
    assert out["segments"][0]["src_in"] == 60
    f = grade_cuts(out, words)
    assert "overcut_partial" not in classes(f)
    assert "orphan_fragment" not in classes(f)


def test_snap_cut_end_lands_on_restart_point():
    """Round-1 real case (take-47s): stumble + restart INSIDE one sentence —
    the cut end snaps to the final restart ('every recipe behind...'), keeping
    the clean take and still cutting the stumbles."""
    from app.edl import snap_cut_ends_to_takes
    words = mk_words("So so every recipe so every recipe behind me says heat the pan then.")
    # Cut through word 10 ("says") -> the clean take's head is lost.
    # words: 0 So 1 so 2 every 3 recipe 4 so 5 every 6 recipe 7 behind 8 me 9 says
    edl = {"segments": [{"src_in": 120, "src_out": 500}],   # kept from word 10 "heat"
           "drops": [{"src_in": 0, "src_out": 120, "reason": "false_start"}]}
    out = snap_cut_ends_to_takes(edl, words)
    # last restart = word 5 "every" (bigram 'every recipe' repeats) -> frame 60
    assert out["drops"][0]["src_out"] == 60
    f = grade_cuts(out, words)
    assert "overcut_partial" not in classes(f)


def test_snap_leaves_clean_boundaries_alone():
    from app.edl import snap_cut_ends_to_takes
    import copy
    words = mk_words("First full sentence here now. Second different sentence entirely done.")
    edl = {"segments": [{"src_in": 60, "src_out": 300}],
           "drops": [{"src_in": 0, "src_out": 60, "reason": "false_start"}]}  # ends AT sentence 2 start
    before = copy.deepcopy(edl)
    out = snap_cut_ends_to_takes(edl, words)
    assert out["drops"] == before["drops"]


def test_snap_dupe_extends_cut_over_first_take():
    """Round-2 real case (owner-fusion): restoring the split first take kept
    BOTH takes. When the sentence duplicates a later kept sentence, the whole
    earlier take is cut instead (keep-last-take)."""
    from app.edl import snap_cut_ends_to_takes
    words = mk_words("Most fusion fails for the same reason. "
                     "Most fusion fails for the same reason. And here is why now.")
    # take 1 = words 0..6, take 2 = words 7..13; cut 0..words[6] mid-take-1
    edl = {"segments": [{"src_in": 72, "src_out": 500}],
           "drops": [{"src_in": 0, "src_out": 72, "reason": "false_start"}]}
    out = snap_cut_ends_to_takes(edl, words)
    f = grade_cuts(out, words)
    assert "undercut_dupe" not in classes(f)
    assert "orphan_fragment" not in classes(f)


def test_aborted_partial_keep_cut_whole():
    """Round-2 real case (take-40s): '1, do the 2 cuisines—' left as '1,' —
    a partially-kept aborted sentence must be cut entirely."""
    from app.edl import enforce_sentence_integrity
    words = mk_words("1, do the 2 cuisines share a base? "
                     "1, do the 2 cuisines— 2, check the acid balance today ok.")
    # sentence 2 = words 8..12 ("1, do the 2 cuisines—"); cut words 9..12 keep "1,"
    edl = {"segments": [{"src_in": 0, "src_out": 108}, {"src_in": 156, "src_out": 600}],
           "drops": []}
    out = enforce_sentence_integrity(edl, words)
    from app.edl import _take_kept_fn
    kept = _take_kept_fn(out)
    assert not kept(96)     # "1," (word 8, frame 96) now cut too


def test_kept_aborted_restart_gets_cut():
    """Round-2 real case (take-41s): kept stumble '...the ones that—' restarts
    as '...the ones there' — the aborted take must be dropped."""
    from app.edl import enforce_sentence_integrity
    words = mk_words("The first 3 ingredients on the label are the ones that— "
                     "The first 3 ingredients on the label are the ones there.")
    edl = {"segments": [{"src_in": 0, "src_out": 600}], "drops": []}
    out = enforce_sentence_integrity(edl, words)
    f = grade_cuts(out, words)
    assert "undercut_stumble" not in classes(f)
    from app.edl import _take_kept_fn
    kept = _take_kept_fn(out)
    assert not kept(0) and kept(ms_to_frame_local(11))  # restart sentence starts at word 11


def ms_to_frame_local(word_idx, ms_per_word=400):
    return round(word_idx * ms_per_word * 30 / 1000)


def test_interior_drop_restores_unique_word():
    """Round-2 real case (take-42s): 'I launched before I was [ready] and...'
    — an interior drop removed a unique word; it must come back."""
    from app.edl import enforce_sentence_integrity
    words = mk_words("I launched before I was ready and that's the only reason anyone showed up.")
    # drop word 5 "ready": frames 60..71
    edl = {"segments": [{"src_in": 0, "src_out": 500}],
           "drops": [{"src_in": 60, "src_out": 72, "reason": "dead_air"}]}
    out = enforce_sentence_integrity(edl, words)
    f = grade_cuts(out, words)
    from app.edl import _take_kept_fn
    assert _take_kept_fn(out)(60)


def test_interior_drop_keeps_stutter_cut():
    """Round-2 real case (take-41s): 'paying $74 extra for for the packaging'
    — the overshooting interior drop is restored but the 'for for' stutter
    stays cut (one 'for' removed)."""
    from app.edl import enforce_sentence_integrity, _take_kept_fn
    words = mk_words("You're paying $74 extra for for the packaging and the vibe, not the food.")
    # drop words 3..9 ("extra for for the packaging and the"): frames 36..119
    edl = {"segments": [{"src_in": 0, "src_out": 500}],
           "drops": [{"src_in": 36, "src_out": 120, "reason": "flub"}]}
    out = enforce_sentence_integrity(edl, words)
    kept = _take_kept_fn(out)
    assert kept(36)          # "extra" restored
    assert kept(84)          # "packaging" restored
    assert not kept(60)      # second "for" (word 5) stays cut as a stutter


def test_unique_sentence_restored_mid_video():
    """Round-1/2 real case (take-47s): 'Here's the one that doesn't.' — a
    unique content sentence fully dropped mid-video must be restored."""
    from app.edl import enforce_sentence_integrity, _take_kept_fn
    words = mk_words("Every recipe behind me says heat the pan first. "
                     "Here's the one that doesn't. Cold pan, cold oil, then heat slowly.")
    # sentence 2 = words 9..13, frames 108..165 fully dropped
    edl = {"segments": [{"src_in": 0, "src_out": 108}, {"src_in": 168, "src_out": 600}],
           "drops": []}
    out = enforce_sentence_integrity(edl, words)
    kept = _take_kept_fn(out)
    assert kept(108) and kept(156)
    f = grade_cuts(out, words)
    assert "overcut_content" not in classes(f)


def test_parallel_structure_not_flagged_as_dupe():
    """Round-2 grader false positive: 'Flip the fancy one over.' vs 'Now flip
    the drugstore one over.' are parallel beats, not retakes."""
    words = mk_words("Flip the fancy one over. Now flip the drugstore one over.")
    edl = {"segments": [{"src_in": 0, "src_out": 500}], "drops": []}
    f = grade_cuts(edl, words)
    assert "undercut_dupe" not in classes(f)


def test_guards_are_importable_from_main():
    """Round-3 regression: the enforce call was wired into main.py without its
    import — the fail-soft except swallowed the NameError for a whole round."""
    import importlib, os
    os.environ.setdefault("TESTING", "1")
    m = importlib.import_module("main")
    from app import edl as _edl
    assert getattr(m, "snap_cut_ends_to_takes") is _edl.snap_cut_ends_to_takes
    assert getattr(m, "enforce_sentence_integrity") is _edl.enforce_sentence_integrity


def test_tail_cut_of_normal_sentence_restored():
    """Round-3 real case (take-41s): lost 'the fancy one over.' from 'Flip the
    fancy one over.' — a tail-cut of a non-aborted, non-duplicate sentence
    must be restored."""
    from app.edl import enforce_sentence_integrity, _take_kept_fn
    words = mk_words("Flip the fancy one over. Now flip the drugstore one over.")
    # cut words 1..4 ("the fancy one over."): frames 12..59
    edl = {"segments": [{"src_in": 0, "src_out": 12}, {"src_in": 60, "src_out": 400}],
           "drops": []}
    out = enforce_sentence_integrity(edl, words)
    kept = _take_kept_fn(out)
    assert kept(24) and kept(48)     # "fancy" ... "over." back
    f = grade_cuts(out, words)
    assert "overcut_partial" not in classes(f)


def test_tail_cut_of_duplicate_take_cut_whole():
    """Tail-cut sentence that duplicates a later kept take: the fragment is
    the discarded take — cut all of it, don't resurrect it."""
    from app.edl import enforce_sentence_integrity, _take_kept_fn
    words = mk_words("Most fusion fails for the same reason. "
                     "Most fusion fails for the same reason. And here is why now.")
    # take 1 words 0..6; cut words 2..6 keeping "Most fusion"
    edl = {"segments": [{"src_in": 0, "src_out": 24}, {"src_in": 84, "src_out": 500}],
           "drops": []}
    out = enforce_sentence_integrity(edl, words)
    kept = _take_kept_fn(out)
    assert not kept(0)               # "Most" (take 1) now cut
    f = grade_cuts(out, words)
    assert "undercut_dupe" not in classes(f)
    assert "orphan_fragment" not in classes(f)


def test_fully_kept_duplicate_takes_first_cut():
    """Round-3 real case (owner-fusion): the author kept BOTH takes verbatim —
    the earlier one must be cut (keep-last-take)."""
    from app.edl import enforce_sentence_integrity, _take_kept_fn
    words = mk_words("Most fusion fails for the same reason. "
                     "Most fusion fails for the same reason. And here is why now.")
    edl = {"segments": [{"src_in": 0, "src_out": 500}], "drops": []}
    out = enforce_sentence_integrity(edl, words)
    kept = _take_kept_fn(out)
    assert not kept(0) and kept(84)  # take 1 cut, take 2 kept
    f = grade_cuts(out, words)
    assert "undercut_dupe" not in classes(f)


def test_intra_sentence_restart_stumble_trimmed():
    """Round-4 real case (take-47s): 'So, so every recipe, so every recipe
    behind me says heat the pan' — the restart lives inside ONE sentence; the
    stumble region before the last restart is cut."""
    from app.edl import enforce_sentence_integrity, _take_kept_fn
    words = mk_words("So, so every recipe, so every recipe behind me says heat the pan first.")
    edl = {"segments": [{"src_in": 0, "src_out": 600}], "drops": []}
    out = enforce_sentence_integrity(edl, words)
    kept = _take_kept_fn(out)
    assert not kept(0)                # "So," cut
    assert not kept(36)               # first "recipe," cut
    assert kept(60) and kept(84)      # final take "every recipe behind..." kept


def test_rhetorical_repeat_with_unique_content_survives():
    """Pass E must not cut when the pre-restart region carries unique content."""
    from app.edl import enforce_sentence_integrity, _take_kept_fn
    import copy
    words = mk_words("Great sauce needs great heat and great heat needs a steel pan always.")
    edl = {"segments": [{"src_in": 0, "src_out": 600}], "drops": []}
    before = copy.deepcopy(edl)
    out = enforce_sentence_integrity(edl, words)
    assert out["drops"] == before["drops"]
