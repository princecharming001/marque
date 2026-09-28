"""Editing doctrine loader (ARCHITECTURE §9).

``SKILL.md`` goes into every Director system prompt (front matter stripped); topic files load on demand
through the ``load_skill`` tool; ``constants.yaml`` feeds critics and metrics. Source directory:
``Settings.skills_dir`` (default ``studio/skills/editing``).

The doctrine is written by people (and in parallel with the engine), so everything here is tolerant:
missing files yield an explicit placeholder / empty mapping instead of an exception, reads are cached
by ``(mtime, size)`` so edits show up without a restart, and a malformed ``constants.yaml`` is reported
(warning, or :class:`SkillError` with ``strict=True``) instead of crashing a run.

Topic names are paths relative to the skills directory without ``.md`` (``"broll"``,
``"styles/story-comedy-hottake"``). :func:`resolve_skill_name` also accepts ``"broll.md"``, a unique
basename, a unique word such as ``"comedy"``, ``"SKILL"`` (the index) and ``"constants"`` (the raw YAML).
Anything resolving outside the skills directory (``..``, absolute paths, escaping symlinks) is refused.
Each topic's one-line description is the first sentence of the first prose paragraph after its title
(the files open with "Load this file when …", which is exactly what the Director needs to pick one).
"""

from __future__ import annotations

import re
import threading
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from studio.config import get_settings

if TYPE_CHECKING:  # pragma: no cover
    from studio.config import Settings

__all__ = [
    "SKILL_FILE", "CONSTANTS_FILE", "SkillError", "SkillNotFound", "SkillTopic", "skills_dir", "skill_index",
    "skill_meta", "list_topics", "list_skills", "topics_table", "resolve_skill_name", "load_skill",
    "list_sections", "load_constants", "constant", "system_prompt_block",
]

SKILL_FILE = "SKILL.md"
CONSTANTS_FILE = "constants.yaml"
_INDEX_ALIASES = {"skill", "skill.md", "index", "doctrine", "talking-head-editing"}
_CONSTANTS_ALIASES = {"constants", "constants.yaml", "constants.yml", "priors"}
_MAX_DESC = 240

_PLACEHOLDER = (
    "# Talking-head editing (doctrine not available)\n\n"
    "The editing doctrine (skills/editing/SKILL.md) is not available in this environment yet. "
    "Work from first principles: story before polish, every addition needs a job, cut where a thought "
    "ends, voice before visuals, match the speaker's energy, protect what only this creator could say, "
    "and restraint (\"none\" is a valid answer for any finishing layer)."
)


class SkillError(RuntimeError):
    """A doctrine file is unreadable or malformed."""


class SkillNotFound(KeyError):
    """Unknown topic name (the message lists close matches and the available topics)."""

    def __str__(self) -> str:  # KeyError repr-quotes its message; keep it readable
        return str(self.args[0]) if self.args else "skill not found"


@dataclass(frozen=True)
class SkillTopic:
    name: str  # "broll", "styles/story-comedy-hottake"
    path: Path
    title: str
    description: str
    size_bytes: int


# ---------------------------------------------------------------------------------------------- io
_cache_lock = threading.Lock()
_text_cache: dict[str, tuple[tuple[int, int], str]] = {}
_yaml_cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}


def skills_dir(settings: Settings | None = None) -> Path:
    s = settings if settings is not None else get_settings()
    return Path(s.skills_dir)


def _stamp(p: Path) -> tuple[int, int]:
    st = p.stat()
    return (st.st_mtime_ns, st.st_size)


def _read(p: Path) -> str:
    key = str(p)
    stamp = _stamp(p)
    with _cache_lock:
        hit = _text_cache.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise SkillError(f"{p.name} is not UTF-8 text: {e}") from None
    with _cache_lock:
        _text_cache[key] = (stamp, text)
    return text


def _inside(root: Path, p: Path) -> bool:
    try:
        rr = root.resolve()
        pr = p.resolve()
    except OSError:
        return False
    return pr == rr or rr in pr.parents


_FRONT_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    m = _FRONT_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, Any] = {}
    try:
        loaded = yaml.safe_load(m.group(1))
        if isinstance(loaded, dict):
            meta = loaded
    except yaml.YAMLError:
        meta = {}
    return meta, text[m.end():].lstrip("\n")


# ---------------------------------------------------------------------------------------------- index
def skill_index(*, settings: Settings | None = None) -> str:
    """Contents of ``SKILL.md`` without its YAML front matter (a clear placeholder if it is missing)."""
    p = skills_dir(settings) / SKILL_FILE
    if not p.is_file():
        return _PLACEHOLDER
    _meta, body = _split_front_matter(_read(p))
    return body.strip() or _PLACEHOLDER


def skill_meta(*, settings: Settings | None = None) -> dict[str, Any]:
    """``SKILL.md`` front matter (``name``, ``description``) or ``{}``."""
    p = skills_dir(settings) / SKILL_FILE
    if not p.is_file():
        return {}
    meta, _ = _split_front_matter(_read(p))
    return meta


def _title_and_description(text: str, fallback: str) -> tuple[str, str]:
    _meta, body = _split_front_matter(text)
    title = ""
    para: list[str] = []
    in_code = False
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            if para:
                break
            continue
        if in_code:
            continue
        if line.startswith("#"):
            if not title and line.lstrip("#").strip():
                title = line.lstrip("#").strip()
                continue
            if para:
                break
            continue
        if not line:
            if para:
                break
            continue
        if line.startswith(("|", ">", "-", "*", "<")) and not para:
            continue  # tables, quotes, lists and html before the first prose paragraph
        para.append(line)
    text_para = " ".join(para)
    text_para = re.sub(r"\s+", " ", text_para).strip()
    m = re.match(r"(.+?[.!?])(\s|$)", text_para)
    desc = m.group(1) if m else text_para
    if len(desc) > _MAX_DESC:
        desc = desc[: _MAX_DESC - 1].rstrip() + "…"
    return title or fallback, desc


def list_topics(*, settings: Settings | None = None) -> list[SkillTopic]:
    """Topic files (``*.md`` except ``SKILL.md``, recursively), sorted by name."""
    root = skills_dir(settings)
    if not root.is_dir():
        return []
    out: list[SkillTopic] = []
    for p in sorted(root.rglob("*.md")):
        if not p.is_file() or p.name == SKILL_FILE or any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        if not _inside(root, p):
            continue  # a symlink pointing outside the doctrine
        name = p.relative_to(root).with_suffix("").as_posix()
        try:
            title, desc = _title_and_description(_read(p), name)
        except (OSError, SkillError):
            continue
        out.append(SkillTopic(name=name, path=p, title=title, description=desc, size_bytes=p.stat().st_size))
    out.sort(key=lambda t: t.name)
    return out


def list_skills(*, settings: Settings | None = None) -> list[str]:
    """Topic names loadable with :func:`load_skill` (e.g. ``"broll"``, ``"styles/story-comedy-hottake"``)."""
    return [t.name for t in list_topics(settings=settings)]


def topics_table(*, settings: Settings | None = None) -> str:
    """One line per topic: ``- name (≈N k tokens): description``."""
    lines = []
    for t in list_topics(settings=settings):
        ktok = max(1, round(t.size_bytes / 4 / 1000))
        lines.append(f"- {t.name} (~{ktok}k tokens): {t.description or t.title}")
    return "\n".join(lines)


def system_prompt_block(*, settings: Settings | None = None) -> str:
    """``SKILL.md`` plus the list of loadable topics, for the Director's system prompt.

    Topics that ``SKILL.md`` already routes to (it names ``<topic>.md``) are listed by name and size only,
    so the prompt does not repeat the index's own "when to load" guidance; any other topic keeps its
    one-line description. The block is deterministic for a given doctrine (stable prompt-cache prefix)."""
    index = skill_index(settings=settings)
    lines = []
    for t in list_topics(settings=settings):
        ktok = max(1, round(t.size_bytes / 4 / 1000))
        if f"{t.name}.md" in index:
            lines.append(f"- {t.name} (~{ktok}k tokens)")
        else:
            lines.append(f"- {t.name} (~{ktok}k tokens): {t.description or t.title}")
    parts = [index]
    if lines:
        parts.append("## Topic files available through load_skill(name)\n\n"
                     "Pass the name without `.md` (the file names above also work); `section=` returns one "
                     "heading's part.\n\n" + "\n".join(lines))
    if (skills_dir(settings) / CONSTANTS_FILE).is_file():
        parts.append("`load_skill(\"constants\")` returns constants.yaml (default priors critics use).")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------------------------- names
def _norm(name: str) -> str:
    n = (name or "").strip().replace("\\", "/").strip()
    while n.startswith("./"):
        n = n[2:]
    n = re.sub(r"/{2,}", "/", n)
    return n


def resolve_skill_name(name: str, *, settings: Settings | None = None) -> str:
    """Canonical topic name for ``name`` (``"SKILL"`` / ``"constants"`` for the index / YAML).

    Raises :class:`SkillNotFound` (with suggestions) or ``ValueError`` for path traversal.
    """
    raw = _norm(name)
    if not raw:
        raise SkillNotFound("empty skill name")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or ".." in raw.split("/"):
        raise ValueError(f"refusing skill path outside the doctrine: {name!r}")
    low = raw.lower()
    if low in _INDEX_ALIASES:
        return "SKILL"
    if low in _CONSTANTS_ALIASES:
        return "constants"
    base = low[:-3] if low.endswith(".md") else low
    topics = list_skills(settings=settings)
    by_lower = {t.lower(): t for t in topics}
    if base in by_lower:
        return by_lower[base]
    # unique basename ("story-comedy-hottake" → "styles/story-comedy-hottake")
    by_base = [t for t in topics if t.lower().rsplit("/", 1)[-1] == base]
    if len(by_base) == 1:
        return by_base[0]
    # normalized forms ("b-roll" → "broll", "cutting and pacing" → "cutting-and-pacing")
    squash = re.sub(r"[^a-z0-9/]+", "", base)
    by_squash = [t for t in topics if re.sub(r"[^a-z0-9/]+", "", t.lower()) == squash]
    if len(by_squash) == 1:
        return by_squash[0]
    # a unique word/fragment match ("comedy" → "styles/story-comedy-hottake")
    tokens = [x for x in re.split(r"[^a-z0-9]+", base) if x]
    if tokens:
        cands = [t for t in topics if all(tok in re.split(r"[^a-z0-9]+", t.lower()) for tok in tokens)]
        if len(cands) == 1:
            return cands[0]
        if not cands:
            cands = [t for t in topics if squash and squash in re.sub(r"[^a-z0-9]+", "", t.lower())]
            if len(cands) == 1:
                return cands[0]
    suggestions: list[str] = []
    try:
        from rapidfuzz import process

        suggestions = [m[0] for m in process.extract(base, topics, limit=3, score_cutoff=40)]
    except Exception:  # pragma: no cover - rapidfuzz is a dependency
        suggestions = []
    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    avail = ", ".join(topics) if topics else "(no topic files available yet)"
    raise SkillNotFound(f"unknown skill {name!r}.{hint} Available: {avail}; also 'SKILL' and 'constants'.")


# ---------------------------------------------------------------------------------------------- load
_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def list_sections(text: str) -> list[tuple[int, str]]:
    """``[(level, heading)]`` of the Markdown headings in ``text`` (outside code fences)."""
    out = []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = _HEAD_RE.match(line)
        if m:
            out.append((len(m.group(1)), m.group(2)))
    return out


def _extract_section(text: str, section: str) -> str | None:
    want = section.strip().lower().lstrip("#").strip()
    lines = text.splitlines()
    in_code = False
    heads: list[tuple[int, int, str]] = []  # (line index, level, text)
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = _HEAD_RE.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2)))
    pick = None
    for exact in (True, False):
        for i, lvl, h in heads:
            hl = h.lower()
            if (hl == want) if exact else (want in hl):
                pick = (i, lvl)
                break
        if pick:
            break
    if pick is None:
        return None
    start, lvl = pick
    end = len(lines)
    for i, lv, _h in heads:
        if i > start and lv <= lvl:
            end = i
            break
    return "\n".join(lines[start:end]).strip()


def load_skill(name: str, *, section: str | None = None, settings: Settings | None = None) -> str:
    """Contents of one topic file (path traversal rejected).

    ``section`` returns only the part under the first heading matching it (exact, then substring,
    case-insensitive), up to the next heading of the same or higher level; unknown sections raise
    :class:`SkillNotFound` listing the file's headings. ``"SKILL"`` returns the index and
    ``"constants"`` the raw ``constants.yaml``.
    """
    canon = resolve_skill_name(name, settings=settings)
    root = skills_dir(settings)
    if canon == "SKILL":
        text = skill_index(settings=settings)
    elif canon == "constants":
        p = root / CONSTANTS_FILE
        if not p.is_file():
            raise SkillNotFound("constants.yaml is not available yet")
        text = _read(p)
    else:
        p = root / f"{canon}.md"
        if not _inside(root, p) or not p.is_file():
            raise SkillNotFound(f"skill {canon!r} is not available")
        _meta, text = _split_front_matter(_read(p))
    if section:
        part = _extract_section(text, section)
        if part is None:
            heads = "; ".join(h for _lvl, h in list_sections(text)[:40])
            raise SkillNotFound(f"no section matching {section!r} in {canon}. Headings: {heads}")
        return part
    return text


# ---------------------------------------------------------------------------------------------- constants
def load_constants(*, settings: Settings | None = None, strict: bool = False) -> dict[str, Any]:
    """Parsed ``constants.yaml`` (craft priors, not gates); ``{}`` when the file does not exist yet.

    A malformed file warns and returns ``{}`` (or raises :class:`SkillError` with ``strict=True``).
    The returned dict is a fresh copy (callers may mutate it).
    """
    import copy

    p = skills_dir(settings) / CONSTANTS_FILE
    if not p.is_file():
        return {}
    key = str(p)
    stamp = _stamp(p)
    with _cache_lock:
        hit = _yaml_cache.get(key)
        if hit is not None and hit[0] == stamp:
            return copy.deepcopy(hit[1])
    try:
        data = yaml.safe_load(_read(p))
    except yaml.YAMLError as e:
        msg = f"constants.yaml is malformed: {e}"
        if strict:
            raise SkillError(msg) from None
        warnings.warn(msg, stacklevel=2)
        return {}
    if data is None:
        data = {}
    if not isinstance(data, dict):
        msg = f"constants.yaml must be a mapping at the top level, got {type(data).__name__}"
        if strict:
            raise SkillError(msg)
        warnings.warn(msg, stacklevel=2)
        return {}
    with _cache_lock:
        _yaml_cache[key] = (stamp, data)
    return copy.deepcopy(data)


_MISSING = object()


def constant(path: str, default: Any = None, *, settings: Settings | None = None) -> Any:
    """Dotted lookup into ``constants.yaml``: ``constant("hook.first_word_target_s")`` → ``[0.1, 0.5]``."""
    node: Any = load_constants(settings=settings)
    for part in [x for x in path.split(".") if x]:
        if isinstance(node, dict):
            node = node.get(part, _MISSING)
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            node = _MISSING
        if node is _MISSING:
            return default
    return node
