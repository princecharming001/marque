"""The golden set (docs/07-ai-system.md §8.5).

Three things live here:
  - CASES      : generation fixtures (brand × pillar × style) the live harness
                 generates against and then scores.
  - KNOWN_GOOD : hand-written scripts that MUST pass every gate with zero quality
                 flags. If the invariants ever fail these, the harness is broken.
  - KNOWN_BAD  : scripts that MUST be caught, each annotated with the check that
                 should catch it. This is the regression tripwire — it proves the
                 gate actually rejects slop/banned/invalid output.
"""
from __future__ import annotations

_FIT_BRAND = {
    "niche": "fitness for busy parents", "audience": "overwhelmed parents",
    "known_for": "15-minute home workouts", "what_you_do": "coach at-home fitness",
    "goal": "Grow my audience", "voice": {"funnyToSerious": 0.4, "polishedToRaw": 0.7},
    "non_negotiables": ["shredded", "no pain no gain"],
    "catchphrases": ["small reps, big life"],
}
_FIN_BRAND = {
    "niche": "personal finance", "audience": "first-time investors",
    "known_for": "plain-English money advice", "what_you_do": "explain investing",
    "goal": "Grow my audience", "voice": {"funnyToSerious": 0.6, "polishedToRaw": 0.4},
    "non_negotiables": ["get rich quick", "guaranteed returns"],
}

# Posts the live generator can mine for verbatim voice exemplars.
_FIT_POSTS = [
    {"caption": "Stop counting reps. Count the days you showed up.", "likes": 900, "comments": 60},
    {"caption": "Your kids don't need a perfect parent. They need one who moves.", "likes": 700, "comments": 30},
]

CASES = [
    {"id": "fit-myth-talkinghead", "brand": _FIT_BRAND, "posts": _FIT_POSTS,
     "pillar": "Myth-busting", "pillar_summary": "Correct bad fitness advice",
     "pillar_angle": "You debunk gym-bro myths for time-poor parents",
     "style": "talking_head", "count": 2},
    {"id": "fin-listicle-faceless", "brand": _FIN_BRAND, "posts": [],
     "pillar": "Teach the fundamentals", "pillar_summary": "Explain one money idea",
     "pillar_angle": "You make one investing concept click in 30s",
     "style": "faceless", "count": 2},
    {"id": "fit-hottake-greenscreen", "brand": _FIT_BRAND, "posts": _FIT_POSTS,
     "pillar": "Hot takes", "pillar_summary": "Stake a contrarian position",
     "pillar_angle": "You argue against a popular fitness trend",
     "style": "green_screen", "count": 2},
]

# ---------------------------------------------------------------------------
# T2 (superintelligence epic) — shared fixtures for eval/path_eval.py's
# all-paths live scorecard. A richer brand+posts pair than CASES' fixtures
# (real voice sliders, multiple catchphrases/non_negotiables, and every post
# carrying BOTH a caption and a spoken transcript — transcripts are what
# _voice_exemplars/brand_block actually mine for verbatim voice grounding, so
# a posts fixture missing them can't meaningfully exercise voice_match).
# ---------------------------------------------------------------------------
EVAL_BRAND = {
    "niche": "sustainable fashion for young professionals",
    "audience": "25-35 year olds building a capsule wardrobe on a budget",
    "known_for": "thrift-flip styling and cost-per-wear breakdowns",
    "what_you_do": "help people dress well without buying fast fashion",
    "goal": "Grow my audience",
    "voice": {"funnyToSerious": 0.35, "polishedToRaw": 0.6},
    "non_negotiables": ["haul", "shop my closet clickbait", "guilt-trip the viewer"],
    "catchphrases": ["cost per wear, not cost per cart", "your closet already has the outfit",
                     "buy it once, wear it forever", "slow is the new flex"],
}
EVAL_POSTS = [
    {"caption": "This $12 thrift blazer has a lower cost-per-wear than your $80 fast-fashion one.",
     "transcript": "This blazer cost me twelve dollars at a thrift store two years ago. I've worn it "
                   "forty-one times. That's thirty cents a wear. Compare that to the eighty dollar "
                   "blazer sitting in your closet with the tag still on it.",
     "likes": 41200, "comments": 890},
    {"caption": "Your closet already has the outfit. You just haven't looked at it sideways yet.",
     "transcript": "You do not need to buy anything new for this weekend. Open your closet. That "
                   "blazer you wear to work? Belt it over a slip dress. Done. Your closet already "
                   "has the outfit.",
     "likes": 27800, "comments": 512},
    {"caption": "Cost per wear, not cost per cart. Do the math before you check out.",
     "transcript": "Before you buy anything, divide the price by how many times you'll actually "
                   "wear it. A forty dollar tee you wear weekly beats a twelve dollar tee that sits "
                   "in a drawer. Cost per wear, not cost per cart.",
     "likes": 55600, "comments": 1204},
    {"caption": "Buy it once, wear it forever — three fabrics that actually hold up.",
     "transcript": "Three fabrics I trust to last more than a season: wool, real leather, and heavy "
                   "cotton twill. Everything else in fast fashion is designed to fall apart. Buy it "
                   "once, wear it forever.",
     "likes": 33900, "comments": 640},
    {"caption": "Slow is the new flex. Here's what my six-year-old jacket has been through.",
     "transcript": "This jacket is six years old. It has been through two moves, one breakup, and "
                   "more job interviews than I can count. Slow is the new flex.",
     "likes": 19700, "comments": 301},
    {"caption": "I stopped hauling and started tracking cost-per-wear. My spending dropped 60 percent.",
     "transcript": "A year ago I filmed haul videos every week. Then I started tracking cost per "
                   "wear instead. My clothing spending dropped sixty percent and I actually like my "
                   "closet now.",
     "likes": 48300, "comments": 977},
    {"caption": "The $12 blazer versus the $80 blazer — a real cost-per-wear breakdown.",
     "transcript": "Let's do the actual math. Twelve dollar thrifted blazer, forty one wears, thirty "
                   "cents each. Eighty dollar new blazer, worn three times because the fit's a "
                   "little off, twenty-six dollars a wear. The thrift find wins every time.",
     "likes": 61500, "comments": 1390},
    {"caption": "You don't need a capsule wardrobe guide. You need to stop buying trend pieces.",
     "transcript": "Every capsule wardrobe guide tells you to buy fifteen new basics. Skip that. "
                   "Look at what you already own, keep what earns its cost per wear, and stop "
                   "buying anything described as a trend piece.",
     "likes": 22100, "comments": 455},
]

KNOWN_GOOD = [
    {"brand": _FIT_BRAND, "script": {
        "title": "The 15-minute lie", "summary": "Debunk the hour-long-workout myth.",
        "hook": "You don't need an hour. You need 15 honest minutes, 4 times a week.",
        "hookSignal": "contrarian", "formatId": "myth-buster",
        "body": "Everyone sells the 60-minute grind. For a parent that's a fantasy. Here's the 15-minute block that actually moves the needle: 5 squats, 5 push-ups, 5 rows, repeat.",
        "cta": "Save this for tomorrow morning.", "predictedScore": 82, "style": "talking_head"}},
    {"brand": _FIN_BRAND, "script": {
        "title": "Index funds, plainly", "summary": "One concept, 30 seconds.",
        "hook": "An index fund is just buying a tiny slice of 500 companies at once.",
        "hookSignal": "authority", "formatId": "faceless",
        "body": "Instead of betting on one stock, you own a sliver of all of them. When the market grows, you grow with it. That's it. No stock-picking, no timing.",
        "cta": "Follow for the next one.", "predictedScore": 78, "style": "faceless"}},
]

# Each: script + the check name(s) it must trip.
KNOWN_BAD = [
    {"why": "stage-direction body (describes what to say)", "expect_flag": "stage direction", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "Most fitness advice is backwards.",
        "hookSignal": "contrarian", "formatId": "myth-buster",
        "body": "Talk about how protein timing is a myth.\n\nMention the anabolic window study, then explain that total daily intake is what matters.\n\nEnd by telling them to stop stressing about the clock.",
        "cta": "Follow.", "predictedScore": 80, "style": "talking_head"}},
    {"why": "beat-sheet body (storyboard scaffolding)", "expect_flag": "stage direction", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "The one cardio mistake stalling your progress.",
        "hookSignal": "curiosity", "formatId": "listicle",
        "body": "Beat 1: the surprising claim about steady-state cardio.\n\nBeat 2: the proof from the data.\n\nBeat 3: what to do instead.",
        "cta": "Follow.", "predictedScore": 80, "style": "talking_head"}},
    {"why": "fabricated client testimonial", "expect_flag": "ungrounded receipt", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "My client Sarah lost 40 pounds in 12 weeks with this.",
        "hookSignal": "authority", "formatId": "before-after",
        "body": "She did exactly what I'm about to show you and the results were undeniable.", "cta": "Follow.",
        "predictedScore": 80, "style": "talking_head"}},
    {"why": "fabricated personal experiment", "expect_flag": "ungrounded receipt", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "I tested 5 diets for 30 days and tracked every macro.",
        "hookSignal": "specificity", "formatId": "myth-buster",
        "body": "The results shocked me and they'll shock you too — here's what happened.", "cta": "Follow.",
        "predictedScore": 80, "style": "talking_head"}},
    {"why": "slop opener", "expect_flag": "slop opener", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "In this video I'll show you three workouts.",
        "hookSignal": "curiosity", "formatId": "myth-buster",
        "body": "Some body text that is definitely long enough.", "cta": "Follow me.",
        "predictedScore": 80, "style": "talking_head"}},
    {"why": "banned phrase in body", "expect_gate": "no_banned_phrase",
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "The one move that fixes your posture in a week.",
        "hookSignal": "stakes", "formatId": "myth-buster",
        "body": "No pain no gain — push until it hurts and you'll get shredded.", "cta": "Follow.",
        "predictedScore": 80, "style": "talking_head"}},
    {"why": "invalid format", "expect_gate": "format_valid",
     "brand": _FIN_BRAND, "script": {
        "title": "x", "hook": "Three money habits that quietly compound.",
        "hookSignal": "specificity", "formatId": "totally-made-up",
        "body": "A perfectly reasonable body of at least twelve characters.", "cta": "Save this.",
        "predictedScore": 70, "style": "faceless"}},
    {"why": "score out of range", "expect_gate": "score_in_range",
     "brand": _FIN_BRAND, "script": {
        "title": "x", "hook": "The compounding trick banks won't advertise.",
        "hookSignal": "curiosity", "formatId": "listicle",
        "body": "A perfectly reasonable body of at least twelve characters.", "cta": "Save this.",
        "predictedScore": 140, "style": "faceless"}},
    {"why": "empty hook", "expect_gate": "has_hook",
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "", "hookSignal": "stakes", "formatId": "myth-buster",
        "body": "A perfectly reasonable body of at least twelve characters.", "cta": "Follow.",
        "predictedScore": 60, "style": "talking_head"}},
    # --- Speakability v2: one fixture per evasion family the live audit found slipping
    # through the v1 lint. Each expect_flag matches a substring of its new reason string.
    {"why": "meta-narration (here I'd break down)", "expect_flag": "meta-narration", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "Most diets fail for one dumb reason.",
        "hookSignal": "contrarian", "formatId": "myth-buster",
        "body": "Here I'd break down why most diets fail. It's not willpower, it's math.",
        "cta": "Follow.", "predictedScore": 80, "style": "talking_head"}},
    {"why": "meta-narration (this is where I get into)", "expect_flag": "meta-narration", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "Cardio isn't the enemy people think it is.",
        "hookSignal": "curiosity", "formatId": "myth-buster",
        "body": "This is where I get into the three mistakes everyone makes with cardio timing.",
        "cta": "Follow.", "predictedScore": 80, "style": "talking_head"}},
    {"why": "coaching (you want to open with)", "expect_flag": "coaching", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "Your first rep sets the whole set.",
        "hookSignal": "specificity", "formatId": "myth-buster",
        "body": "You want to open with a bold claim, then hit them with the data.",
        "cta": "Follow.", "predictedScore": 80, "style": "talking_head"}},
    {"why": "outline label density (Step 1 / Step 2)", "expect_flag": "outline", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "Two steps fix your squat depth.",
        "hookSignal": "specificity", "formatId": "listicle",
        "body": "Step 1 — the hook.\n\nStep 2 — the reveal.",
        "cta": "Follow.", "predictedScore": 80, "style": "talking_head"}},
    {"why": "sequencing scaffold (First/Then/Finally)", "expect_flag": "sequencing scaffold", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "Your warmup order is backwards.",
        "hookSignal": "contrarian", "formatId": "listicle",
        "body": "First, the claim. Then, the proof. Finally, the takeaway.",
        "cta": "Follow.", "predictedScore": 80, "style": "talking_head"}},
    {"why": "intent statement (the idea is to)", "expect_flag": "editorial intent", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "Expectations vs. reality on leg day.",
        "hookSignal": "curiosity", "formatId": "before-after",
        "body": "The idea is to contrast what people expect with what actually works.",
        "cta": "Follow.", "predictedScore": 80, "style": "talking_head"}},
    {"why": "meta-narration (I'll cover)", "expect_flag": "meta-narration", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "Three myths are wrecking your progress.",
        "hookSignal": "stakes", "formatId": "listicle",
        "body": "I'll cover the three biggest myths and why they persist.",
        "cta": "Follow.", "predictedScore": 80, "style": "talking_head"}},
    {"why": "bulleted content summary", "expect_flag": "bulleted content summary", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "Everything wrong with your current split.",
        "hookSignal": "stakes", "formatId": "listicle",
        "body": "- the myth\n- the evidence\n- the fix",
        "cta": "Follow.", "predictedScore": 80, "style": "talking_head"}},
    {"why": "imperative directive (Demonstrate/Highlight)", "expect_flag": "imperative directive", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "The rep range nobody talks about.",
        "hookSignal": "specificity", "formatId": "myth-buster",
        "body": "Demonstrate the move on camera. Highlight the key number.",
        "cta": "Follow.", "predictedScore": 80, "style": "talking_head"}},
    {"why": "visual artifact (picture the chart)", "expect_flag": "visual artifact", "gate_ok": True,
     "brand": _FIN_BRAND, "script": {
        "title": "x", "hook": "Your net worth chart is lying to you.",
        "hookSignal": "curiosity", "formatId": "myth-buster",
        "body": "Picture the graph going up and to the right.",
        "cta": "Save this.", "predictedScore": 80, "style": "faceless"}},
]

# Near-miss phrasings that are LEGITIMATE spoken copy and must NEVER be flagged by the
# v2 speakability lint. Appended to KNOWN_GOOD so the harness self-check (which hard-fails
# any KNOWN_GOOD that trips a quality flag) is the false-positive tripwire.
KNOWN_GOOD.extend([
    {"brand": _FIT_BRAND, "script": {
        "title": "x", "summary": "Teaser for the next clip.",
        "hook": "I'll show you what happened next.",
        "hookSignal": "curiosity", "formatId": "myth-buster",
        "body": "I'll show you what happened next. Three weeks in, my knees stopped hurting for the first time in years.",
        "cta": "Follow.", "predictedScore": 78, "style": "talking_head"}},
    {"brand": _FIT_BRAND, "script": {
        "title": "x", "summary": "Scene-setting spoken hook.",
        "hook": "The scale lied to me for three weeks straight.",
        "hookSignal": "narrative", "formatId": "pov-story",
        "body": "Picture this: you're three weeks in and the scale hasn't moved, but your jeans fit different.",
        "cta": "Follow.", "predictedScore": 78, "style": "talking_head"}},
    {"brand": _FIT_BRAND, "script": {
        "title": "x", "summary": "Two-beat spoken sequence, not an outline.",
        "hook": "This one habit changed everything.",
        "hookSignal": "curiosity", "formatId": "myth-buster",
        "body": "First, you're going to hate this. Then you'll thank me for it in a month.",
        "cta": "Follow.", "predictedScore": 78, "style": "talking_head"}},
    {"brand": _FIT_BRAND, "script": {
        "title": "x", "summary": "Spoken ordinal, not a label.",
        "hook": "One tiny swap fixes your mornings.",
        "hookSignal": "specificity", "formatId": "listicle",
        "body": "One: stop skipping breakfast. It's the difference between a 10am crash and a steady afternoon.",
        "cta": "Follow.", "predictedScore": 78, "style": "talking_head"}},
])

# --- B4: relevance-to-creator (_flag_offbrand) fixtures ------------------------
_POWERLIFT_BRAND = {
    "niche": "powerlifting coaching for busy dads", "audience": "dads over 35",
    "known_for": "strength training programming", "what_you_do": "coach powerlifters",
    "goal": "Grow my audience", "voice": {"funnyToSerious": 0.3, "polishedToRaw": 0.6},
}

KNOWN_BAD.extend([
    {"why": "offbrand (skincare script for a powerlifting coach)", "expect_flag": "offbrand", "gate_ok": True,
     "brand": _POWERLIFT_BRAND, "script": {
        "title": "x", "hook": "The best skincare routine for glowing skin.",
        "hookSignal": "specificity", "formatId": "listicle",
        "body": "Use this serum every morning for radiant, hydrated results.",
        "cta": "Follow.", "predictedScore": 80, "style": "talking_head"}},
    {"why": "offbrand (recipe script for a powerlifting coach)", "expect_flag": "offbrand", "gate_ok": True,
     "brand": _POWERLIFT_BRAND, "script": {
        "title": "x", "hook": "This pasta recipe takes fifteen minutes.",
        "hookSignal": "specificity", "formatId": "listicle",
        "body": "Boil the noodles, toss with garlic and olive oil, and dinner is done.",
        "cta": "Save this.", "predictedScore": 80, "style": "talking_head"}},
    {"why": "offbrand (crypto script for a fitness coach)", "expect_flag": "offbrand", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "This altcoin could 10x by next quarter.",
        "hookSignal": "stakes", "formatId": "myth-buster",
        "body": "The chart pattern here looks exactly like the last breakout before it mooned.",
        "cta": "Follow.", "predictedScore": 80, "style": "talking_head"}},
])

# On-brand scripts that share a LITERAL niche term (so they're a clean tripwire even
# though _flag_offbrand is excluded from the general KNOWN_GOOD hard-check — see
# eval/run_eval.py's self-check comment for why that exclusion exists).
KNOWN_GOOD.extend([
    {"brand": _POWERLIFT_BRAND, "script": {
        "title": "x", "summary": "On-brand: literal 'powerlifting'/'strength' overlap.",
        "hook": "Most dads over 35 train powerlifting completely wrong.",
        "hookSignal": "contrarian", "formatId": "myth-buster",
        "body": "Strength training after 35 needs more recovery, not more volume. Cut your sets, keep the weight.",
        "cta": "Follow.", "predictedScore": 78, "style": "talking_head"}},
    {"brand": _POWERLIFT_BRAND, "script": {
        "title": "x", "summary": "On-brand: literal 'coach'/'powerlifters' overlap.",
        "hook": "Every powerlifting coach says this and it's backwards.",
        "hookSignal": "contrarian", "formatId": "myth-buster",
        "body": "I coach powerlifters who thought more gym days meant more strength. It's the opposite.",
        "cta": "Follow.", "predictedScore": 78, "style": "talking_head"}},
])
