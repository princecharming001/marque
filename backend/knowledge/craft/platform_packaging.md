# Craft — Platform & Packaging Judgment (evidence-tiered)

Tiers: [P] platform-published · [E] executive statement · [D] vendor dataset ·
[PR] practitioner doctrine. Hard enforcement only for [P]/[E]; [D]/[PR] advise.

What each platform actually ranks [P/E]: TikTok — full begin-to-end watches
carry the greatest weight; follower count is NOT a factor. Shorts — the metric
is viewed-vs-swiped and engaged views (raw Shorts views count any play since
2025-03 — a vanity number). Reels — watch time, likes-per-reach, sends-per-
reach (sends weigh most for non-follower reach). So: completion is the target,
and the shortest cut that preserves the payoff beats a longer cut watched 60%.

Instagram's PUBLISHED demotion list [P]: watermarked or low-res reels, muted
reels, borders/letterboxing, majority-text reels, reposted assets. These are
render-time hard gates, not advice.

Packaging is a contract [P]: the title/cover/hook must accurately promise what
the video delivers — YouTube states mismatch kills watch time and discovery.
High CTR + weak intro retention = overpromise; low CTR + strong retention =
underpromise. Cover text ≤4 words, legible at grid size, same single promise
as the spoken hook.

Retention-curve reading [P]: intro = % surviving the first 30s (a taper is
NORMAL); spikes = rewatched moments (future hook material); dips = beats to
cut. Loop-seam endings (last frame ≈ first) farm replays — replays count.

CTA judgment [P/D]: exactly ONE CTA per video, imperative verb + specific
object, ≤7 words, placed after the first value payoff — not cold-open, not
the final swipe-away frame. TikTok publishes +152% conversion for a clear
text CTA and +44% when the CTA card appears early. Meta DEMOTES bait forms
("like if", "tag a friend", "share this to") — genuine asks are exempt.
Never promise "part 2" unless a series exists.

Compliance a content expert must enforce [P]: realistic synthetic media
(AI b-roll of real-looking scenes/people, voice clones) requires the platform
AI-disclosure toggle on TikTok/YouTube/Meta; stylized/animated assets and
AI-assisted scripts are exempt. Never strip C2PA metadata in transcode.
Hashtags: ≤5 on IG, topical only — Mosseri: they do not drive reach. Never
present best-time-to-post charts as strong evidence.

```yaml
rules:
  - id: plat.completion_first
    principle: "Optimize the shortest cut that preserves payoff; completion outranks length"
    source: "TikTok newsroom ranking doc [P]"
    enforce: prompt
  - id: plat.demotion_gates
    principle: "No third-party watermark, borders, mute, majority-text, or duplicate uploads"
    source: "Instagram Ranking Explained [P]"
    enforce: knob
  - id: pack.promise_contract
    principle: "Title/cover/hook state ONE promise the video verifiably pays off"
    source: "YouTube packaging docs [P]"
    enforce: critic
  - id: cta.single_specific
    principle: "One CTA, imperative + specific, <=7 words, after first payoff; no Meta bait forms"
    source: "TikTok Creative Center [P]; Meta engagement-bait policy [P]"
    enforce: critic
  - id: eth.ai_disclosure
    principle: "Realistic synthetic segments require platform AI labels; stylized exempt"
    source: "YouTube 2024-03 / TikTok C2PA / Meta AI-info policies [P]"
    enforce: advise
```
