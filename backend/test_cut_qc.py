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
