# Craft — Story Judgment in the Edit (what a content expert holds)

The edit serves ONE idea (one video, one claim) with a visible arc:
PROMISE (the hook's claim) -> PROOF/ESCALATION (each beat raises or deepens —
never restates) -> PAYOFF (the promise is cashed, explicitly). A video that
hooks a question must ANSWER it; an unpaid promise is the highest-order
failure (audiences punish clickbait with abandonment — the promise-payoff
contract). Diagnostics from the house story doctrine that bind at EDIT time:
HOOK (does the first line earn the next 3s), LOOP (is a question left open to
pull through the middle), PAYOFF (is the open loop closed before the end),
RE-HOOK (in videos >30s, does a mid-video beat re-open tension).

Endings are a dedicated judgment (the industry converged here — Vizard ships a
role whose ONLY job is finding the natural ending): end on the argument
closing, the story landing, or the CTA — never mid-thought, never trailing
into a topic pivot. Our loop-tail XOR end-card is the mechanism; the judgment
is that the LAST KEPT SENTENCE must complete.

Score axes an expert holds simultaneously (Opus's published factor set):
HOOK (grabs + relates to the topic), FLOW (logical progression, satisfying
conclusion), VALUE (resonates, delivers something), TREND (fits what the
audience currently watches). Rank edits by these, explain the ranking in
words — a score without a reason is not a judgment.

```yaml
rules:
  - id: story.promise_payoff
    principle: "The hook's promise is explicitly cashed before the end"
    source: "Promise-payoff doctrine; Opus Flow/Value factors"
    enforce: critic
  - id: story.ending_complete
    principle: "The last kept sentence completes — no mid-thought endings"
    source: "Vizard Clip Editor doctrine; GoE"
    enforce: lint
  - id: story.rehook_long
    principle: "Videos >30s carry a mid-video re-hook beat"
    source: "House story doctrine REHOOK-01"
    enforce: prompt
```
