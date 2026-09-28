"""Tests for studio.agent.skills (doctrine loader). Keyless and offline."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from studio.agent import skills as sk
from studio.config import STUDIO_ROOT, Settings

REAL_SKILLS = STUDIO_ROOT / "skills" / "editing"


def _settings(skills_dir: Path) -> Settings:
    return Settings.load(env={"STUDIO_SKILLS_DIR": str(skills_dir)})


@pytest.fixture
def tiny(tmp_path: Path) -> Settings:
    d = tmp_path / "editing"
    (d / "styles").mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: talking-head-editing\ndescription: Doctrine for tests.\n---\n\n# Talking-head editing\n\n"
        "You are the Director.\n\n## Stage order\n\n1. Brief.\n", encoding="utf-8")
    (d / "broll.md").write_text(
        "# B-roll: when and how\n\nLoad this file at finishing when you consider an insert. It covers jobs.\n\n"
        "## Principles\n\n1. Every insert needs a job.\n\n### Detail\n\nnested\n\n## Modes\n\nfull, split.\n",
        encoding="utf-8")
    (d / "broll-sourcing.md").write_text("# Sourcing\n\n| table | first |\n\nRank and license inserts. More.\n",
                                         encoding="utf-8")
    (d / "styles" / "story-comedy-hottake.md").write_text(
        "# Style treatments: storytime, comedy, hot take\n\nLoad this once the brief names the style.\n",
        encoding="utf-8")
    (d / "constants.yaml").write_text("meta:\n  version: '1'\nhook:\n  first_word_target_s: [0.1, 0.5]\n"
                                      "list_key:\n  - a\n  - b\n", encoding="utf-8")
    return _settings(d)


# ---------------------------------------------------------------------------------------------- tiny dir
def test_index_strips_front_matter_and_meta(tiny: Settings) -> None:
    text = sk.skill_index(settings=tiny)
    assert text.startswith("# Talking-head editing")
    assert "name: talking" not in text
    assert sk.skill_meta(settings=tiny)["name"] == "talking-head-editing"


def test_list_topics_and_descriptions(tiny: Settings) -> None:
    names = sk.list_skills(settings=tiny)
    assert names == ["broll", "broll-sourcing", "styles/story-comedy-hottake"]
    topics = {t.name: t for t in sk.list_topics(settings=tiny)}
    assert topics["broll"].title == "B-roll: when and how"
    assert topics["broll"].description == "Load this file at finishing when you consider an insert."
    # a table before the first prose paragraph is skipped
    assert topics["broll-sourcing"].description == "Rank and license inserts."
    table = sk.topics_table(settings=tiny)
    assert "- styles/story-comedy-hottake" in table and "Load this once the brief names the style." in table


@pytest.mark.parametrize(("name", "canon"), [
    ("broll", "broll"), ("broll.md", "broll"), ("BROLL", "broll"), ("./broll.md", "broll"), ("b-roll", "broll"),
    ("comedy", "styles/story-comedy-hottake"), ("story-comedy-hottake", "styles/story-comedy-hottake"),
    ("styles/story-comedy-hottake.md", "styles/story-comedy-hottake"), ("SKILL", "SKILL"), ("skill.md", "SKILL"),
    ("constants", "constants"), ("broll sourcing", "broll-sourcing"),
])
def test_resolve_names(tiny: Settings, name: str, canon: str) -> None:
    assert sk.resolve_skill_name(name, settings=tiny) == canon


@pytest.mark.parametrize("bad", ["../config", "/etc/passwd", "styles/../../x", "C:/x", "..\\secret"])
def test_path_traversal_rejected(tiny: Settings, bad: str) -> None:
    with pytest.raises(ValueError):
        sk.resolve_skill_name(bad, settings=tiny)


def test_unknown_skill_lists_suggestions(tiny: Settings) -> None:
    with pytest.raises(sk.SkillNotFound) as ei:
        sk.load_skill("brol", settings=tiny)
    msg = str(ei.value)
    assert "Did you mean" in msg and "broll" in msg and "Available:" in msg


def test_load_skill_full_and_sections(tiny: Settings) -> None:
    full = sk.load_skill("broll", settings=tiny)
    assert full.startswith("# B-roll") and "## Modes" in full
    part = sk.load_skill("broll", section="principles", settings=tiny)
    assert part.startswith("## Principles") and "### Detail" in part and "## Modes" not in part
    assert sk.load_skill("broll", section="Detail", settings=tiny).startswith("### Detail")
    assert sk.load_skill("SKILL", settings=tiny).startswith("# Talking-head editing")
    assert "first_word_target_s" in sk.load_skill("constants", settings=tiny)
    with pytest.raises(sk.SkillNotFound) as ei:
        sk.load_skill("broll", section="nope", settings=tiny)
    assert "Headings:" in str(ei.value) and "Principles" in str(ei.value)


def test_constants_and_dotted_lookup(tiny: Settings) -> None:
    c = sk.load_constants(settings=tiny)
    assert c["meta"]["version"] == "1"
    c["meta"]["version"] = "mutated"  # callers get a copy
    assert sk.load_constants(settings=tiny)["meta"]["version"] == "1"
    assert sk.constant("hook.first_word_target_s", settings=tiny) == [0.1, 0.5]
    assert sk.constant("list_key.1", settings=tiny) == "b"
    assert sk.constant("hook.missing", 42, settings=tiny) == 42


def test_cache_sees_edits(tiny: Settings) -> None:
    p = Path(tiny.skills_dir) / "broll.md"
    assert "Every insert" in sk.load_skill("broll", settings=tiny)
    time.sleep(0.01)
    p.write_text("# B-roll\n\nChanged file, longer than before to change the size.\n", encoding="utf-8")
    os.utime(p, None)
    assert "Changed file" in sk.load_skill("broll", settings=tiny)


def test_missing_doctrine_is_tolerated(tmp_path: Path) -> None:
    s = _settings(tmp_path / "nothing-here")
    assert "not available" in sk.skill_index(settings=s)
    assert sk.list_skills(settings=s) == []
    assert sk.load_constants(settings=s) == {}
    assert sk.constant("a.b", "dflt", settings=s) == "dflt"
    with pytest.raises(sk.SkillNotFound):
        sk.load_skill("broll", settings=s)
    assert "not available" in sk.system_prompt_block(settings=s)


def test_malformed_constants(tmp_path: Path) -> None:
    d = tmp_path / "ed"
    d.mkdir()
    (d / "constants.yaml").write_text("a: [1, 2\nb: :\n", encoding="utf-8")
    s = _settings(d)
    with pytest.warns(UserWarning, match="malformed"):
        assert sk.load_constants(settings=s) == {}
    with pytest.raises(sk.SkillError):
        sk.load_constants(settings=s, strict=True)
    (d / "constants.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(sk.SkillError):
        sk.load_constants(settings=s, strict=True)


def test_symlink_escape_ignored(tmp_path: Path) -> None:
    d = tmp_path / "ed"
    d.mkdir()
    outside = tmp_path / "secret.md"
    outside.write_text("# Secret\n\nDo not load.\n", encoding="utf-8")
    (d / "sneaky.md").symlink_to(outside)
    (d / "ok.md").write_text("# OK\n\nFine.\n", encoding="utf-8")
    s = _settings(d)
    assert sk.list_skills(settings=s) == ["ok"]
    with pytest.raises(sk.SkillNotFound):
        sk.load_skill("sneaky", settings=s)


def test_system_prompt_block(tiny: Settings) -> None:
    block = sk.system_prompt_block(settings=tiny)
    assert block.startswith("# Talking-head editing")
    assert "load_skill(name)" in block and "- broll" in block and "constants" in block
    assert "Load this file at finishing" in block  # the index does not route to broll: keep its description
    assert block == sk.system_prompt_block(settings=tiny)  # deterministic (cached prompt prefix)
    # topics the index already routes to are listed by name and size only
    p = tiny.skills_dir / "SKILL.md"
    p.write_text(p.read_text() + "\n| Inserts | `broll.md` |\n", encoding="utf-8")
    routed = sk.system_prompt_block(settings=tiny)
    line = next(ln for ln in routed.splitlines() if ln.startswith("- broll ("))
    assert line.endswith("tokens)") and "Load this file at finishing" not in routed
    assert "Rank and license inserts." in routed  # broll-sourcing is not routed by the index


# ---------------------------------------------------------------------------------------------- real doctrine
@pytest.mark.skipif(not (REAL_SKILLS / "SKILL.md").is_file(), reason="doctrine not written yet")
def test_real_doctrine_loads() -> None:
    s = _settings(REAL_SKILLS)
    assert "Director" in sk.skill_index(settings=s)
    names = sk.list_skills(settings=s)
    for expected in ("broll", "cutting-and-pacing", "story-and-hook"):
        if (REAL_SKILLS / f"{expected}.md").is_file():
            assert expected in names
    for t in sk.list_topics(settings=s):
        assert t.description, f"{t.name} has no one-line description"
        assert len(t.description) <= 240
    if any(n.startswith("styles/") and "comedy" in n for n in names):
        assert "comedy" in sk.resolve_skill_name("comedy", settings=s)
    if (REAL_SKILLS / "constants.yaml").is_file():
        assert isinstance(sk.load_constants(settings=s, strict=True), dict)
