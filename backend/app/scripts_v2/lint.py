"""Deterministic "AI-tell" lint for short-form talking-head scripts.

No LLM, no network: regexes plus a little ``statistics`` over a script dict
``{"hook": str, "body": str, "cta": str}``. Every finding carries a stable
snake_case ``code`` so a later rewrite step can be triggered on hard tells and
an eval can count tells per script. Same input always yields the same output.

Lexicon provenance: Wikipedia "Signs of AI writing" (vocabulary, tricolons,
"not X, it's Y" framing, moral-of-the-story endings), the WSC / slop-guard
MIT word lists (the delve / crucial / tapestry family), and broadcast-news
writing guides (contractions, varied sentence length, no stage directions in
read copy). Severity: ``hard`` = the creator would cringe reading it aloud;
``soft`` = stylistic.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

HARD = "hard"
SOFT = "soft"


@dataclass(frozen=True)
class Finding:
    code: str        # stable snake_case id, e.g. "opener_cliche"
    severity: str    # "hard" | "soft"
    detail: str      # short human text, may quote the offending text
    span: str = ""   # the matched text when applicable


TELL_CODES: tuple[str, ...] = (
    # hard
    "opener_cliche", "question_hook_generic", "not_x_its_y", "wrapup_ending",
    "template_cta", "list_markers", "stage_direction", "invented_stat", "ai_word",
    "exemplar_overlap", "duplicate_opener", "fill_in_question", "long_close", "too_long",
    # soft
    "tricolon", "aphorism_tic", "low_contractions", "uniform_rhythm",
    "address_mismatch", "length_out_of_range", "em_dash", "hedge_stack",
)

# ---------------------------------------------------------------- text helpers
_WS = re.compile(r"\s+")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"\b[\w']+\b")
_QUOTES = "\"'“”‘’ "


def _phrase_re(phrases: list[str]) -> re.Pattern:
    """Whole-phrase, case-insensitive alternation (longest phrase wins)."""
    alts = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<![\w'])(?:{alts})(?![\w'])", re.I)


def _field(script: dict, key: str) -> str:
    return str(script.get(key) or "").replace("’", "'")


def spoken_text(script: dict) -> str:
    """hook + body + cta joined with spaces; paragraph breaks become spaces."""
    joined = " ".join(str(script.get(k) or "") for k in ("hook", "body", "cta"))
    return _WS.sub(" ", joined.replace("\n\n", " ")).strip()


def _sentences(text: str) -> list[str]:
    text = _WS.sub(" ", text).strip()
    out = [s.strip(_QUOTES) for s in _SENT_SPLIT.split(text)] if text else []
    return [s for s in out if s]


def _words(text: str) -> list[str]:
    return _WORD.findall(text)


def _norm_words(text: str) -> list[str]:
    return [w.lower() for w in _words(text.replace("’", "'"))]


def _ngrams(words: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


# ------------------------------------------------------------------- patterns
_OPENER_CLICHES = _phrase_re([
    "here's the thing", "let's dive in", "let's be honest", "real talk", "stop scrolling",
    "unpopular opinion", "nobody talks about", "nobody tells you", "pov:", "ever wondered",
    "did you know", "what if i told you", "have you ever", "okay so",
    "today i want to talk about", "in this video", "let me tell you about", "are you tired of",
])
_QUESTION_START = re.compile(r"^(?:why|what|how|did|have|do|is|are)\b", re.I)
_NOT_X_ITS_Y = re.compile(r"\b(?:isn't|is not|not)\b .{3,60}?[.,;] ?(?:it's|it is)\b", re.I)
_WRAP_START = re.compile(
    r"^(?:so,? to recap|remember|in conclusion|to sum up|at the end of the day|"
    r"the bottom line|that's why|moral of the story)\b", re.I)
_WRAP_ANY = _phrase_re(["you've got this", "you got this", "your journey", "starts today",
                        "thank me later", "will thank you"])
_TEMPLATE_CTA = _phrase_re(["follow for more", "like and subscribe", "save this", "share this with",
                            "comment below", "let me know in the comments", "drop a",
                            "follow for the next", "follow if"])
_LIST_START = re.compile(
    r"^(?:(?:first|second|third|finally)\s*[,:]|(?:firstly|secondly|thirdly)\b|"
    r"number (?:one|two|three)\b)", re.I)
_STAGE = re.compile(r"\[\s*(?:b-?roll|cut to|show)|\(beat\)|\bcut to:|\bb-roll:", re.I)
_BEAT_LINE = re.compile(r"^\s*beat \d", re.I | re.M)
_PCT = re.compile(r"\b(\d+(?:\.\d+)?)\s?(?:%|percent\b)")
_STAT_PHRASE = _phrase_re(["studies show", "research shows", "experts say", "science says",
                           "according to a study"])
_AI_WORDS = [
    r"delv(?:e|es|ed|ing)", r"crucial", r"pivotal", r"tapestry", r"testament", r"underscores?",
    r"showcas(?:e|es|ed|ing)", r"vibrant", r"intricate", r"meticulous(?:ly)?", r"enduring",
    r"foster(?:s|ing)?", r"garner(?:s|ed|ing)?", r"interplay", r"robust", r"multifaceted",
    r"leverag(?:e|es|ed|ing)", r"elevat(?:e|es|ed|ing)", r"unlock(?:s|ed|ing)?",
    r"harness(?:es|ed|ing)?", r"embark(?:s|ed|ing)?", r"navigat(?:e|es|ed|ing)", r"seamless(?:ly)?",
    r"empower(?:s|ing)?", r"actionable", r"transform(?:ative|s)?", r"game[- ]chang(?:er|ing)",
    r"secret sauce", r"level up", r"mindset shift", r"next level", r"simply put", r"the truth is",
    r"furthermore", r"moreover", r"additionally", r"therefore", r"consequently", r"in conclusion",
    r"utiliz(?:e|es|ed|ing)", r"individuals", r"commence", r"it(?:'s| is) important to note",
    r"groundbreaking", r"revolutionary", r"diverse array", r"ensuring you",
    r"highlighting the importance",
]
_AI_RE = re.compile(r"(?<![\w'])(?:" + "|".join(_AI_WORDS) + r")(?![\w'])", re.I)
_W13 = r"[\w']+(?: [\w']+){0,2}"
_TRI_AND = re.compile(r"(?<![\w'])([\w']+), ([\w']+), (?:and|or) ([\w']+)(?![\w'])", re.I)
_APHORISM = _phrase_re(["read that again", "let that sink in", "full stop", "every. single.",
                        "that's it. that's the", "that's the whole"])
_CONTRACTIONS = _phrase_re(["it's", "don't", "you're", "i'm", "that's", "can't", "won't", "isn't",
                            "doesn't", "didn't", "we're", "they're", "there's", "i've", "you've",
                            "wouldn't", "couldn't", "shouldn't"])
_EXPANDED = _phrase_re(["it is", "do not", "you are", "i am", "that is", "cannot", "will not",
                        "is not", "does not", "did not", "we are", "they are", "there is",
                        "i have", "you have", "would not", "could not", "should not"])
_ADDRESS_FORMS = ("you guys", "bro", "guys", "y'all", "friends", "fam", "besties", "everyone")
# Unambiguous address forms count anywhere; guys/friends/everyone only in vocative position.
_ADDR_ANY = re.compile(r"(?<![\w'])(you guys|y'all|besties|bro|fam)(?![\w'])", re.I)
_ADDR_VOCATIVE = re.compile(
    r"(?:\b(?:hey|hi|hello|morning|okay|ok|alright|listen|what's up|sup)\s+(guys|friends|everyone)\b"
    r"|,\s*(guys|friends|everyone)\s*(?=[,.!?:]|$))", re.I)
_HEDGES = _phrase_re(["could potentially", "might arguably", "it could be argued", "in some ways"])


def _canon_address(form: str) -> str:
    form = form.strip().lower()
    return "guys" if form == "you guys" else form


def _has_proper_noun(sentence: str) -> bool:
    words = _words(sentence)[1:]
    return any(w[0].isupper() and w != "I" and not w.startswith("I'") for w in words)


# --------------------------------------------------------------------- checks
def _check_tricolon(sentences: list[str]) -> Finding | None:
    for s in sentences:
        m = _TRI_AND.search(s)
        if m:
            return Finding("tricolon", SOFT, f"three-beat list: {m.group(0)!r}", m.group(0))
        parts = s.rstrip(".!?").split(", ")
        if len(parts) < 3:
            continue
        a, b, c = parts[-3], parts[-2], re.sub(r"^(?:and|or) ", "", parts[-1], flags=re.I)
        k = len(_words(b))
        if 1 <= k <= 3 and len(_words(c)) == k and len(_words(a)) >= k:
            tail = " ".join(_words(a)[-k:])
            span = f"{tail}, {b}, {parts[-1]}"
            return Finding("tricolon", SOFT, f"three-beat ending: {span!r}", span)
    return None


def lint_script(
    script: dict,
    *,
    profile: dict | None = None,
    recent_openers: list[str] | None = None,
    target_words: tuple[int, int] | None = None,
) -> list[Finding]:
    """Return every detected tell, hard findings first, in check order."""
    profile = profile or {}
    hook_raw, body_raw, cta_raw = (_field(script, k) for k in ("hook", "body", "cta"))
    text = spoken_text(script).replace("’", "'")
    sentences = _sentences(text)
    if not sentences:
        return []
    hook_sentence = sentences[0]
    hard: list[Finding] = []
    soft: list[Finding] = []

    # -- hard ---------------------------------------------------------------
    m = _OPENER_CLICHES.search(hook_sentence)
    if m:
        hard.append(Finding("opener_cliche", HARD, f"hook opens on a cliche: {m.group(0)!r}", m.group(0)))
    if (hook_sentence.endswith("?") and _QUESTION_START.match(hook_sentence)
            and not re.search(r"\d", hook_sentence) and not _has_proper_noun(hook_sentence)):
        hard.append(Finding("question_hook_generic", HARD,
                            "generic rhetorical question hook (no number, no specific name)", hook_sentence))
    m = _NOT_X_ITS_Y.search(text)
    if m:
        hard.append(Finding("not_x_its_y", HARD, f"'not X, it's Y' framing: {m.group(0)!r}", m.group(0)))

    # Landing window: last body sentence plus every CTA sentence.
    landing = (_sentences(body_raw)[-1:] + _sentences(cta_raw)) or sentences[-1:]
    for s in landing:
        m = _WRAP_START.match(s) or _WRAP_ANY.search(s)
        if m:
            hard.append(Finding("wrapup_ending", HARD, f"moral-of-the-story ending: {s!r}", m.group(0)))
            break
    m = _TEMPLATE_CTA.search(cta_raw or sentences[-1])
    if m:
        hard.append(Finding("template_cta", HARD, f"template CTA: {m.group(0)!r}", m.group(0)))

    markers = [mm.group(0) for s in _sentences(body_raw) if (mm := _LIST_START.match(s))]
    if len(markers) >= 2:
        if (script.get("formatId") or "") == "listicle":
            # A listicle IS a list; spoken "number one / number two" is the format, not a
            # tell. Keep it visible (soft) so an eval can still count it.
            soft.append(Finding("list_markers", SOFT,
                                f"spoken list markers in a listicle: {markers}", ", ".join(markers)))
        else:
            hard.append(Finding("list_markers", HARD, f"enumerated list markers: {markers}", ", ".join(markers)))
    # A [fill-in] is a short label the creator completes ("[your number]"). A bracketed
    # question or note to the creator is a stage direction they would read aloud.
    for mb in re.finditer(r"\[([^\]]*)\]?", hook_raw + " " + body_raw + " " + cta_raw):
        inner = mb.group(1)
        if "?" in inner or len(inner.split()) > 6 or not mb.group(0).endswith("]"):
            hard.append(Finding("fill_in_question", HARD,
                                f"bracket is a note or question, not a short fill-in: {mb.group(0)[:80]!r}",
                                mb.group(0)[:80]))
            break
    close = (_sentences(cta_raw) or sentences)[-1:] if (cta_raw or sentences) else []
    if close and len(_norm_words(close[0])) > 24:
        hard.append(Finding("long_close", HARD,
                            f"the closing line runs {len(_norm_words(close[0]))} words; say the payoff once, short",
                            close[0][:80]))
    m = _STAGE.search(body_raw) or _BEAT_LINE.search(body_raw)
    if m:
        hard.append(Finding("stage_direction", HARD, f"stage direction in read copy: {m.group(0)!r}", m.group(0)))

    allowed = [str(a) for a in (profile.get("allowed_claims") or [])]
    allowed_blob = " ".join(allowed)
    seen: set[str] = set()
    for mm in _PCT.finditer(text):
        num = mm.group(1)
        if re.search(rf"(?<![\d.]){re.escape(num)}(?![\d.])", allowed_blob) or mm.group(0) in seen:
            continue
        seen.add(mm.group(0))
        hard.append(Finding("invented_stat", HARD, f"unsourced statistic: {mm.group(0)!r}", mm.group(0)))
    if not allowed:
        for mm in _STAT_PHRASE.finditer(text):
            if mm.group(0).lower() not in seen:
                seen.add(mm.group(0).lower())
                hard.append(Finding("invented_stat", HARD, f"unsourced attribution: {mm.group(0)!r}", mm.group(0)))

    seen_words: set[str] = set()
    for mm in _AI_RE.finditer(text):
        key = mm.group(0).lower()
        if key not in seen_words:
            seen_words.add(key)
            hard.append(Finding("ai_word", HARD, f"AI-flavored word: {mm.group(0)!r}", mm.group(0)))

    script_words = _norm_words(text)
    script_7 = _ngrams(script_words, 7)
    for ex in profile.get("exemplar_texts") or []:
        shared = script_7 & _ngrams(_norm_words(str(ex)), 7)
        if shared:
            span = " ".join(min(shared, key=lambda g: script_words.index(g[0]) if g[0] in script_words else 0))
            hard.append(Finding("exemplar_overlap", HARD, f"7+ words copied from an exemplar: {span!r}", span))
            break

    hook_words = _norm_words(hook_raw) or _norm_words(hook_sentence)
    for op in recent_openers or []:
        op_words = _norm_words(str(op))
        same_start = len(hook_words) >= 6 and hook_words[:6] == op_words[:6]
        union = set(hook_words) | set(op_words)
        jacc = len(set(hook_words) & set(op_words)) / len(union) if union else 0.0
        if same_start or jacc >= 0.6:
            hard.append(Finding("duplicate_opener", HARD,
                                f"hook repeats a recent opener (jaccard {jacc:.2f}): {op!r}", str(op)))
            break

    # -- soft ---------------------------------------------------------------
    tri = _check_tricolon(sentences)
    if tri:
        soft.append(tri)
    m = _APHORISM.search(text)
    period = next((s for s in sentences if s.rstrip(".!").strip().lower() == "period"), None)
    if m or period:
        span = m.group(0) if m else period
        soft.append(Finding("aphorism_tic", SOFT, f"aphorism tic: {span!r}", span))

    n_contr = len(_CONTRACTIONS.findall(text))
    n_expanded = len(_EXPANDED.findall(text))
    if n_contr + n_expanded >= 4:
        rate = n_contr / (n_contr + n_expanded)
        if rate < 0.8:
            soft.append(Finding("low_contractions", SOFT,
                                f"contraction rate {rate:.2f} ({n_contr} contracted vs {n_expanded} spelled out)"))

    counts = [len(_words(s)) for s in sentences]
    if len(counts) >= 5 and statistics.mean(counts) > 0:
        ratio = statistics.pstdev(counts) / statistics.mean(counts)
        if ratio < 0.45:
            soft.append(Finding("uniform_rhythm", SOFT,
                                f"sentence lengths too even (std/mean {ratio:.2f}): {counts}"))

    want = profile.get("address_form")
    if want:
        want_c = _canon_address(str(want))
        found = [mm.group(1) for mm in _ADDR_ANY.finditer(text)]
        found += [g for mm in _ADDR_VOCATIVE.finditer(text) for g in mm.groups() if g]
        other = next((f for f in found if _canon_address(f) != want_c
                      and _canon_address(f) in {_canon_address(a) for a in _ADDRESS_FORMS}), None)
        if other:
            soft.append(Finding("address_mismatch", SOFT,
                                f"addresses the viewer as {other!r} but profile uses {want!r}", other))

    if target_words:
        lo, hi = target_words
        total = len(script_words)
        if total > hi * 1.4:
            hard.append(Finding("too_long", HARD, f"{total} words against a {lo}-{hi} target; cut the weakest beat"))
        elif not (lo * 0.8 <= total <= hi * 1.2):
            soft.append(Finding("length_out_of_range", SOFT,
                                f"{total} words, target {lo}-{hi} (tolerance {lo * 0.8:.0f}-{hi * 1.2:.0f})"))

    raw_all = hook_raw + body_raw + cta_raw
    dash = next((d for d in ("—", "–") if d in raw_all), None)
    if dash:
        soft.append(Finding("em_dash", SOFT, "em/en dash in spoken copy", dash))
    m = _HEDGES.search(text)
    if m:
        soft.append(Finding("hedge_stack", SOFT, f"stacked hedge: {m.group(0)!r}", m.group(0)))

    return hard + soft


def tells_count(findings) -> int:
    """Number of findings, hard and soft together."""
    return len(list(findings))


def summarize(findings) -> dict:
    items = list(findings)
    return {
        "hard": sum(1 for f in items if f.severity == HARD),
        "soft": sum(1 for f in items if f.severity == SOFT),
        "codes": sorted({f.code for f in items}),
    }
