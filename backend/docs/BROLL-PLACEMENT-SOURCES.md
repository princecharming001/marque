
---

## Part 6 — Viral/aesthetic v2 (2026-07-17): verified cadence + KLIPY + title system

**Adversarial verification round** (13 research agents over 2 rounds; every load-bearing number
re-checked against independent sources — OpusClip 13.5M-clip research, Aibrify/Captions.ai 2026
pacing models, Joyspace Hormozi-2026 analyses, docs.klipy.com):

- **Tenor API is DEAD** (Google shutdown 2026-06-30). Deleted from the codebase.
- **KLIPY is the new cultural source** (api.klipy.com — ex-Tenor team; Discord/WhatsApp/Bluesky
  migrated 2026-07). Ladder: KLIPY **clips** (≤10s movie/TV/viral moments, `file.mp4` FLAT url,
  dims in `file_meta`) → KLIPY **gifs** (`file.{hd,md,sm}.mp4.url` nested) → GIPHY fallback.
  ⚠️ the `static-memes` vertical is png/webp ONLY — never a video source. per_page min 8;
  `content_filter=high` (clips not fully MPA-rated); attribution "Powered by KLIPY" + logo
  (ToS §4) renders on inserts; NO asset caching/rehosting (we link only); keyless → GIPHY-only.
  Signup: partner.klipy.com (test 100 req/hr; production free via form). Env: `KLIPY_KEY`.
- **Holds (verified bands)**: contextual 1.2–3.0s (<1.2s doesn't register; 3s soft ceiling);
  meme pop-ins 0.5–1.5s. `_BROLL_HOLD_POLICY`: entity/data (36,54,72) · evidence (45,75,90) ·
  action/concept (36,75,90) · meme (15,30,45).
- **Density is a GENRE DIAL** (founder/B2B tolerates 8–10s cadence; lo-fi credibility evidence):
  entertainment → tight spacing 45f, floor ~1/5s (divisor 150), visual budget 0.50, meme cap 5;
  educational → spacing 90f, floor ~1/8s (divisor 240), budget 0.40, meme cap 2.
- **Jitter**: deterministic per-job (sha1-seeded) spacing −15..+30f / hold ±6f — fixed intervals
  are an AI tell.
- **SFX restraint**: budget 3/30s (was 5); memes get ONE pop (couple_broll_sfx, post-resolve);
  "hit" reserved for the top emphasis span; over-coupling = "marketing-guru content" tell.
- **Hook title v2**: hold = first-sentence end in OUTPUT frames, clamp [60,150]f, fallback 90f;
  sentence case (Title Case refuted — "PowerPoint"), CAPS iff captions uppercase; 5–8 words,
  ≤60 chars word-boundary; pos_y 0.24 (clears the eye line + Reels top 14%); theme hook
  font/bg wired (fixes the mixed-fonts lint error); frame-0 stacked render + 10f exit.
- **Cold open**: `trim_cold_open` (token `cold_open`) — first word ≤ ~0.4s of frame 0, 8f pad.
- **Music dropout**: `plan_music_dropout` (token `dropout`) — one bed-silence window over the
  top emphasis span (OUTPUT coords; hook/CTA-protected).
- ⚠️ **`_enabled_passes` bug fixed**: `all` inside a csv (`all,framing,...`) previously did NOT
  expand — filler/retake/pacing/emphasis/interrupts/sfx/structure were silently OFF in prod.
- Prod env after this round: `RETENTION_PASSES=all,framing,hook_pack,jitter,cold_open,dropout`.
