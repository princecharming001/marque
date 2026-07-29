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
