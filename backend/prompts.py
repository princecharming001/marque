"""Marque AI prompt library — the actual product quality lives here.

Every prompt builder returns (system, user) strings. The design principles:
- GROUND in real evidence (a creator's actual posts / their spoken interview) whenever we have it,
  so pillars and scripts sound like THIS creator, not a generic archetype.
- STYLE-AWARE: the three video styles produce structurally different scripts.
- JUDGE: pillars pass a specificity gate (generate-then-judge) before we trust them.
"""
from __future__ import annotations

OPUS = "claude-opus-4-8"
HAIKU = "claude-haiku-4-5-20251001"

FORMAT_IDS = [
    "myth-buster", "listicle", "do-this-not-that", "before-after",
    "green-screen", "faceless", "pov-story", "broll-hook",
]

# ---------------------------------------------------------------------------
# Video styles — the coarse lane the creator chooses; each shapes the script.
# `formats` are the fine-grained recipes allowed within the style.
# ---------------------------------------------------------------------------

STYLES = {
    "talking_head": {
        "label": "Talking-Head",
        "formats": ["myth-buster", "listicle", "pov-story", "green-screen"],
        "rubric": (
            "The creator speaks DIRECTLY TO CAMERA. Write `body` as first-person spoken words — "
            "the exact sentences they'd say out loud, in their voice. No stage directions inside the body. "
            "Cold-open on the hook (never 'hey guys', never an intro). One core idea, backed by ONE specific "
            "lived detail or number, landing a single clear takeaway. Keep it tight: 18–40 seconds. "
            "`shotPlan` is camera direction: e.g. ['Hook on frame 1, direct eye contact', "
            "'One punch-in on the key line', 'CTA to camera']."
        ),
        "exemplar": (
            '{"title":"the 2-minute inbox rule","summary":"A talking-head myth-buster on email overwhelm.",'
            '"hook":"You don\'t have an inbox problem. You have a decision problem.","hookSignal":"contrarian",'
            '"formatId":"myth-buster","body":"Everyone tells you to check email less. Wrong. The reason your '
            'inbox stresses you out is that every message is an open loop. So I do this: if it takes under two '
            'minutes, I answer it right now. If it doesn\'t, it goes on one list with a date. That\'s it. I '
            'stopped \'managing\' email and started closing loops, and my inbox went from 300 to zero in a week.",'
            '"cta":"Try the two-minute rule tomorrow and tell me your inbox number.","shotPlan":["Hook on frame 1, '
            'direct eye contact","Punch-in on \'two minutes\'","CTA to camera"],"targetSeconds":28,'
            '"predictedScore":86}'
        ),
    },
    "faceless": {
        "label": "Faceless voiceover",
        "formats": ["faceless", "broll-hook"],
        "rubric": (
            "NO on-camera presence — a voiceover over visuals. Write `body` as a tight VOICEOVER script in "
            "exactly 3 beats: claim → proof → do-this. `shotPlan` is a TIMESTAMPED b-roll cue list, one cue per "
            "beat, each describing the EXACT visual to show (concrete, searchable — e.g. 'overhead of hands "
            "kneading dough', not 'something relevant'): e.g. ['0–3s: <visual for the claim>', "
            "'3–8s: <visual for the proof>', '8–12s: <visual for do-this> + on-screen text card']. "
            "Captions carry the message. 20–35 seconds."
        ),
        "exemplar": (
            '{"title":"why your bread is dense","summary":"A faceless voiceover on the #1 sourdough mistake.",'
            '"hook":"Your sourdough is dense because of one number nobody tells you.","hookSignal":"curiosity",'
            '"formatId":"faceless","body":"Claim: dense crumb isn\'t about your starter — it\'s your dough '
            'temperature. Proof: under 24 degrees and the yeast barely moves; I proofed the same dough at 22 and '
            'at 26 and the warm one doubled in half the time. Do this: stick a cheap thermometer in your dough, '
            'aim for 25, and proof on top of the fridge.","cta":"Save this for your next bake.",'
            '"shotPlan":["0–3s: close-up of dense torn crumb","3–8s: two dough bowls side by side, one risen high",'
            '"8–12s: thermometer pushed into dough reading 25 + text card \'aim for 25°C\'"],"targetSeconds":24,'
            '"predictedScore":84}'
        ),
    },
    "split_three": {
        "label": "3-Way Split",
        "formats": ["listicle", "do-this-not-that", "before-after"],
        "rubric": (
            "A vertical 3-panel split where THREE short segments play one after another, each a DIFFERENT point "
            "or solution to the same problem, escalating to the best one last. Write `body` as THREE clearly-"
            "labeled segments ('Solution 1: …', 'Solution 2: …', 'Solution 3: …'), each 1–2 sentences that stand "
            "alone in their panel. `shotPlan` describes each panel + reveal order: ['Panel 1 (top): <segment 1 "
            "visual>', 'Panel 2 (mid): <segment 2>', 'Panel 3 (bottom): <best, segment 3>']. 20–35 seconds."
        ),
        "exemplar": (
            '{"title":"3 fixes for knee pain","summary":"A 3-way split on squat knee pain, worst to best.",'
            '"hook":"Three ways to kill squat knee pain — the third one actually works.","hookSignal":"specificity",'
            '"formatId":"listicle","body":"Solution 1: widen your stance and point your toes out — helps a little. '
            'Solution 2: slow the descent to three seconds — better, your knees stop caving. Solution 3, the real '
            'fix: screw your feet into the floor and lead with your hips, not your knees — pain gone.",'
            '"cta":"Try solution three on your next set.","shotPlan":["Panel 1 (top): wide stance, toes out",'
            '"Panel 2 (mid): slow 3-count descent","Panel 3 (bottom): hips-back, feet screwed in — the fix"],'
            '"targetSeconds":26,"predictedScore":85}'
        ),
    },
    "fast_cuts": {
        "label": "Fast Cuts",
        "formats": ["listicle", "broll-hook", "myth-buster"],
        "rubric": (
            "A rapid-fire montage: 5–8 PUNCHY one-line beats, each a HARD CUT to a new shot, building momentum. "
            "Write `body` as the numbered rapid-fire lines (each ≤12 words, no filler, no connective tissue). "
            "`shotPlan` is one cut per line describing the shot: ['Cut 1: <shot>', 'Cut 2: <shot>', …]. "
            "Energetic, 15–30 seconds."
        ),
        "exemplar": (
            '{"title":"7 gym mistakes","summary":"A fast-cut list of strength mistakes.",'
            '"hook":"Seven things killing your gym progress — go.","hookSignal":"patternInterrupt",'
            '"formatId":"listicle","body":"One: five sets of curls, nothing for your back. Two: ego-lifting with '
            'half reps. Three: no warm-up, straight to heavy. Four: same weight for six months. Five: skipping legs. '
            'Six: training to failure every set. Seven: no sleep, all pre-workout.",'
            '"cta":"How many were you? Comment the number.","shotPlan":["Cut 1: curls in the mirror",'
            '"Cut 2: half-rep bench","Cut 3: heavy bar, no warm-up","Cut 4: same dumbbells, calendar flip",'
            '"Cut 5: walking past the squat rack","Cut 6: collapsing after a set","Cut 7: pre-workout scoop"],'
            '"targetSeconds":22,"predictedScore":84}'
        ),
    },
    "green_screen": {
        "label": "Green-Screen React",
        "formats": ["green-screen"],
        "rubric": (
            "You stand in front of a screenshot / post / chart (green-screen) and REACT to it. Write `body` as "
            "your spoken reaction that EXPLICITLY references what's on the screen behind you ('this post says X… "
            "here's why that's wrong'). `shotPlan`: ['Key in the screenshot/post', 'Point + react to the specific "
            "part', 'One-line verdict']. 18–30 seconds."
        ),
        "exemplar": (
            '{"title":"reacting to bad advice","summary":"A green-screen react to a viral fitness claim.",'
            '"hook":"This post has two million likes and it\'s completely wrong.","hookSignal":"contrarian",'
            '"formatId":"green-screen","body":"So this post behind me says you have to train a muscle six times a '
            'week to grow. Look at this line — \'more frequency always wins.\' No. Past a point you\'re just piling '
            'up fatigue you can\'t recover from. Twice a week, hard, beats six times half-baked.",'
            '"cta":"Screenshot this for the next time you see that claim.","shotPlan":["Key in the screenshot of '
            'the post","Point at the \'six times a week\' line and react","One-line verdict: twice, hard, wins"],'
            '"targetSeconds":24,"predictedScore":84}'
        ),
    },
}

SIGNALS = "[stakes,authority,curiosity,patternInterrupt,specificity,contrarian,narrative,callOut]"

SCRIPT_SCHEMA = (
    'Each item: {"title": str (≤6 words, a human title), "summary": str (one line), "hook": str, '
    '"hookSignal": one of ' + SIGNALS + ', "formatId": one of the allowed format ids, "body": str, '
    '"cta": str, "shotPlan": [str], "targetSeconds": int, "predictedScore": int 0-100, '
    '"altHooks": [{"text": str, "signal": str, "strength": int}], "style": str}'
)


# ---------------------------------------------------------------------------
# Shared brand context
# ---------------------------------------------------------------------------

def _post_lines(posts: list[dict] | None) -> str:
    if not posts:
        return ""
    out = ["", "Their REAL recent posts (analyze these for voice, topics, and what their audience rewards):"]
    for i, p in enumerate(posts[:20], 1):
        cap = (p.get("caption") or p.get("transcript") or "").strip().replace("\n", " ")
        tags = " ".join(p.get("hashtags", [])[:6])
        eng = f"{p.get('likes', 0)} likes / {p.get('comments', 0)} comments"
        out.append(f"  {i}. \"{cap[:240]}\" [{tags}] ({eng})")
    return "\n".join(out)


def brand_block(brand: dict, posts: list[dict] | None = None) -> str:
    v = brand.get("voice", {}) or {}
    return (
        "Creator brand:\n"
        f"- niche: {brand.get('niche','')}\n"
        f"- what they do: {brand.get('what_you_do','')}\n"
        f"- audience: {brand.get('audience','')}\n"
        f"- wants to be known for: {brand.get('known_for','')}\n"
        f"- goal: {brand.get('goal','Grow my audience')}\n"
        f"- voice (0..1): funny→serious {v.get('funnyToSerious',0.5)}, "
        f"polished→raw {v.get('polishedToRaw',0.5)}, teacher→peer {v.get('teacherToPeer',0.5)}\n"
        f"- never say: {', '.join(brand.get('non_negotiables', []) or [])}"
        + _post_lines(posts)
    )


# ---------------------------------------------------------------------------
# Pillars (grounded) + the specificity judge
# ---------------------------------------------------------------------------

def pillars_prompt(brand: dict, posts: list[dict] | None = None, avoid: list[str] | None = None) -> tuple[str, str]:
    system = (
        "You are Marque's brand strategist. You design short-form content pillars that are UNIQUE to one "
        "creator. A pillar must be specific enough that the creator reads it and thinks 'that's exactly me' — "
        "NEVER a generic bucket (like 'Behind the scenes', 'Tips & tricks', 'Myth-busting') that would fit any "
        "creator in the niche. When real posts are provided, ground every pillar in the evidence — their actual "
        "topics, their phrasing, the formats their audience already rewards. Reply with ONLY a JSON array."
    )
    avoid_line = ""
    if avoid:
        avoid_line = (
            "\nThese earlier pillars were REJECTED for being too generic — do not repeat them or their vibe: "
            + "; ".join(avoid) + ".\n"
        )
    user = (
        f"{brand_block(brand, posts)}\n{avoid_line}\n"
        "Design 5 content pillars for this creator's short-form video. Each must be specific and ownable to "
        "THIS creator. Return ONLY a JSON array. Each: "
        '{"name": str (2-4 words), "summary": str (one line — what the pillar is), '
        '"angle": str (this creator\'s specific take, grounded in their actual content), '
        '"exampleTopics": [str, str, str] (concrete next-video ideas that extend their best themes)}'
    )
    return system, user


def pillar_judge_prompt(niche: str, pillars: list[dict]) -> tuple[str, str]:
    system = (
        "You are a strict content editor checking pillars for SPECIFICITY. A pillar FAILS if it would apply to "
        "basically any creator in the same niche, or if its angle is vague. It PASSES only if the angle names "
        "something concrete and ownable to this specific creator. Be harsh — generic pillars are the #1 quality "
        "failure. Reply with ONLY a JSON array of {\"index\": int, \"pass\": bool, \"reason\": str}."
    )
    items = "\n".join(
        f'{i}. {p.get("name","")} — angle: {p.get("angle","") or p.get("summary","")}'
        for i, p in enumerate(pillars)
    )
    user = f"Niche: {niche}\nPillars:\n{items}\n\nJudge each."
    return system, user


# ---------------------------------------------------------------------------
# Scripts (style-aware)
# ---------------------------------------------------------------------------

def scripts_prompt(brand: dict, pillar: dict, style: str, count: int,
                   media_context: str = "", posts: list[dict] | None = None) -> tuple[str, str]:
    s = STYLES.get(style, STYLES["talking_head"])
    system = (
        f"You are Marque's script engine writing {s['label']} short-form videos. "
        "Write in the creator's EXACT voice — match their tone sliders, echo their real phrasing, and NEVER use "
        "a banned phrase. The hook must stop the scroll in the first 3 seconds. "
        f"\n\nSTYLE RULES ({s['label']}): {s['rubric']}\n\n"
        f"A correctly-structured example for this style (match the STRUCTURE, not the content):\n{s['exemplar']}\n\n"
        "Reply with ONLY valid JSON, no prose, no code fences."
    )
    media = f"\nReference footage the creator already has (reuse where natural): {media_context}" if media_context else ""
    user = (
        f"{brand_block(brand, posts)}\n"
        f"Content pillar: {pillar.get('name','')} — {pillar.get('summary','')}\n"
        f"Their angle on it: {pillar.get('angle','')}\n"
        f"Example directions: {'; '.join(pillar.get('exampleTopics', []) or [])}{media}\n"
        f"Allowed formatIds for this style: {', '.join(s['formats'])}\n\n"
        f"Write {count} {s['label']} scripts on this pillar, each a distinct angle. Set \"style\":\"{style}\" on "
        f"each. Return ONLY a JSON array. {SCRIPT_SCHEMA}"
    )
    return system, user


# ---------------------------------------------------------------------------
# Hooks / steer / captions / teardown / insights
# ---------------------------------------------------------------------------

def hooks_prompt(brand: dict, topic: str, style: str = "talking_head") -> tuple[str, str]:
    system = (
        "You are Marque's hook engine. Generate scroll-stopping first-3-second hooks in the creator's voice "
        "across the 8 signal types. Reply with ONLY a JSON array, ranked strongest first."
    )
    user = (
        f"{brand_block(brand)}\nTopic: {topic}\nStyle: {STYLES.get(style, STYLES['talking_head'])['label']}\n"
        f"Return ONLY a JSON array of 6 hooks. Each: {{\"text\": str, \"signal\": one of {SIGNALS}, "
        '"strength": int 0-100}'
    )
    return system, user


def steer_prompt(brand: dict, script: dict, instruction: str) -> tuple[str, str]:
    system = (
        "You revise a short-form script per an instruction while preserving the creator's voice and the "
        "structure of its video style. Reply with ONLY a JSON object."
    )
    user = (
        f"{brand_block(brand)}\nStyle: {script.get('style','talking_head')}\n"
        f"Current script:\n- hook: {script.get('hook','')}\n- body: {script.get('body','')}\n- cta: {script.get('cta','')}\n"
        f"Instruction: {instruction}\nReturn ONLY one JSON object. {SCRIPT_SCHEMA}"
    )
    return system, user


def captions_prompt(hook: str, body: str) -> tuple[str, str]:
    system = (
        "You turn a short-form script into punchy on-screen caption lines — ≤5 words each, covering all the "
        "spoken content in order. Reply with ONLY a JSON array of strings."
    )
    return system, f"Hook: {hook}\nBody: {body}\nReturn ONLY a JSON array of caption lines."


def teardown_prompt(clip: dict) -> tuple[str, str]:
    system = "You explain why a short-form clip performed, in one tight insight + a follow-up. Reply with ONLY a JSON object."
    user = (
        f"Clip: format={clip.get('formatName','')}, caption=\"{clip.get('caption','')}\", "
        f"predicted score={clip.get('predictedScore',0)}.\n"
        'Return ONLY: {"headline": str, "detail": str, "liftPercent": int}'
    )
    return system, user


def insights_prompt(brand: dict, summary: str) -> tuple[str, str]:
    system = (
        "You are Marque's growth coach. In ONE or two tight sentences name what's working and the single next "
        "move. No fluff, no lists, no preamble."
    )
    return system, f"{brand_block(brand)}\nThis week's performance: {summary}\nGive one or two sentences of coaching."


# ---------------------------------------------------------------------------
# Brand-scan derivation (real posts → brand) + voice-onboarding finalize
# ---------------------------------------------------------------------------

DERIVE_SCHEMA = (
    'Return ONLY a JSON object: {"niche": str, "audience": str, "knownFor": str, '
    '"voice": {"funnyToSerious": 0-1, "polishedToRaw": 0-1, "teacherToPeer": 0-1}, '
    '"bannedWords": [str], "catchphrases": [str], '
    '"pillars": [5 × {"name": str, "summary": str, "angle": str, "exampleTopics": [str,str,str]}]}'
)


def derive_from_posts_prompt(brand: dict, posts: list[dict]) -> tuple[str, str]:
    system = (
        "You are Marque's brand analyst. You are given a creator's REAL recent posts. Derive their ACTUAL niche, "
        "voice, and content pillars from the evidence — not generic archetypes. Infer the voice axes from HOW "
        "they write. Extract catchphrases they actually use and words they'd never say. Every pillar must be "
        "grounded in their real topics and specific enough that they recognize themselves. Reply with ONLY a JSON object."
    )
    user = f"{brand_block(brand, posts)}\n\nAnalyze the posts above and derive the brand. {DERIVE_SCHEMA}"
    return system, user


def voice_finalize_prompt(brand: dict, transcript: list[dict]) -> tuple[str, str]:
    system = (
        "You are Marque's brand analyst. From this spoken brand-interview transcript, extract the creator's "
        "brand. Infer the voice axes from HOW they speak (energy, formality, who they sound like they're talking "
        "to). Pillars must be grounded in the specific ideas and language they used in the interview — quote "
        "their phrasing where natural. Reply with ONLY a JSON object."
    )
    convo = "\n".join(f"{t.get('role','user')}: {t.get('text','')}" for t in transcript)
    user = f"{brand_block(brand)}\n\nInterview transcript:\n{convo}\n\n{DERIVE_SCHEMA}"
    return system, user


# Conversational-agent system prompt for the ElevenLabs voice onboarding interview.
VOICE_AGENT_SYSTEM = (
    "You are Marque's brand interviewer. In a warm, brief spoken conversation (5–7 short turns), help a "
    "short-form video creator articulate what they're really about. Ask ONE question at a time and keep your "
    "turns to one or two sentences. Listen for: their actual niche, who they most want watching, what they want "
    "to be known for, their natural tone (are they more the teacher or more one of the crew), 2–3 concrete "
    "content ideas they're excited about, and anything they'd never say or do on camera. Probe a vague answer "
    "ONCE ('say more about that'). Never lecture. End by reflecting back a one-sentence summary of their brand "
    "and asking them to confirm."
)
