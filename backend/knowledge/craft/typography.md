# Craft — On-Screen Text (reading-rate law)

The formal standards (use them — nothing here is taste):
- Netflix Timed Text: 42 chars/line, max 2 lines, adult 20 chars/second cap,
  event duration 5/6s minimum to 7s maximum, 2-frame minimum gap, don't cross
  shot changes when avoidable.
- BBC: 160-180 wpm ceiling = 0.3s per word floor; line <= 68% frame width.
- SMPTE ST 2046-1: title-safe = central 90% x 90%; nothing essential outside 93%.
- 9:16 platform zones: Meta top 14% / bottom 35% / sides 6%; TikTok ~130px top,
  ~480px bottom, ~140px right rail.

THE derived law for every burned-in text element (caption page, text card,
hook sticker, end card): display_seconds >= max(word_count x 0.3,
char_count / 20). Full-screen titles want ~3x a single read (GoE #36 — "read
it through three times"); lower-thirds ~2x ("read aloud twice", 3-7s band).

Line-break semantics (Netflix): break after punctuation, before conjunctions/
prepositions; never split article+noun or names; bottom-heavy pyramid.

One typographic voice per video: one caption font family, one accent color,
one transition grammar (our bundle_coherence lint) — mixed grammars are the
single fastest "amateur" read.

```yaml
rules:
  - id: typ.reading_rate
    principle: "Every text element: duration_s >= max(words*0.3, chars/20)"
    source: "BBC 160-180wpm; Netflix 20 CPS"
    enforce: lint
    params: {sec_per_word: 0.3, chars_per_sec: 20}
  - id: typ.end_card_read
    principle: "Full-screen CTA holds ~2-3x one read; scale hold to text length"
    source: "GoE #36; lower-third convention"
    enforce: knob
    params: {reads: 2.0, min_frames: 75, max_frames: 150}
  - id: typ.safe_areas
    principle: "Text inside central 90%; clear of platform UI zones"
    source: "SMPTE ST 2046-1; Meta/TikTok published zones"
    enforce: knob
```
