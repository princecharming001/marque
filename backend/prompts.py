"""Marque AI prompt library — the actual product quality lives here.

Every prompt builder returns (system, user) strings. The design principles:
- GROUND in real evidence (a creator's actual posts / their spoken interview) whenever we have it,
  so pillars and scripts sound like THIS creator, not a generic archetype.
- STYLE-AWARE: the three video styles produce structurally different scripts.
- JUDGE: pillars pass a specificity gate (generate-then-judge) before we trust them.
"""
from __future__ import annotations
import json
import logging

OPUS = "claude-opus-4-8"
HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"

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

# ---------------------------------------------------------------------------
# Per-style edit rubrics and EDL exemplars
# ---------------------------------------------------------------------------

EDIT_RUBRICS = {
    "talking_head": (
        "You are an expert talking-head editor. Rules: (1) Keep the whole take unless the creator says "
        "something clearly wrong or trails off without recovering. (2) Cut filler words (um/uh/like/you know) "
        "and dead-air gaps > 350ms. (3) Place exactly ONE punch-in overlay (scale 1.0→1.08) on the single "
        "most load-bearing line (not the hook, not the CTA). (4) Never insert B-roll over the creator's face "
        "during the hook or CTA — those must be face-to-camera. (5) Captions are word-by-word, every word. "
        "Output valid EDL JSON only."
    ),
    "faceless": (
        "You are an expert faceless/voiceover editor. Rules: (1) The script has 3 beats (claim/proof/do-this). "
        "Map each beat to a broll slot — use the shotPlan cue_text verbatim as broll.cue_text. "
        "(2) Every second must have a visual — no empty segments without broll. "
        "(3) Captions are the primary channel — word-by-word, large, centered. "
        "(4) Cut filler and gaps ≤ 80ms between beats. Output valid EDL JSON only."
    ),
    "split_three": (
        "You are an expert 3-way split editor. Rules: (1) Detect the 3 solution boundaries from the transcript "
        "(look for 'Solution 1/2/3' markers or equivalent). (2) Assign each solution to one panel "
        "(panels: 3). (3) The best/last solution gets the most screen time. "
        "(4) panel_boundaries is a list of 2 frame numbers separating panel 1|2 and panel 2|3. "
        "Output valid EDL JSON only."
    ),
    "fast_cuts": (
        "You are an expert fast-cuts editor. Rules: (1) Hard cut on every numbered/bullet line. "
        "(2) Inter-line silence trimmed to ≤ 80ms. (3) Each segment maps to one cut/shot. "
        "(4) Total duration 15–30s. (5) Caption cards appear on each cut. Output valid EDL JSON only."
    ),
    "green_screen": (
        "You are an expert green-screen react editor. Rules: (1) Keep the speaker on-screen the entire time. "
        "(2) Add a text_card overlay with the reference post/screenshot description at src_in=0. "
        "(3) The creator's speech drives cuts — don't cut mid-sentence. "
        "(4) Duration 18–30s. Output valid EDL JSON only."
    ),
}

EDL_EXEMPLARS = {
    "talking_head": '''
Transcript words: [{"word":"You","start_ms":0,"end_ms":200},{"word":"don't","start_ms":220,"end_ms":400},
{"word":"have","start_ms":420,"end_ms":560},{"word":"um","start_ms":580,"end_ms":680},
{"word":"an","start_ms":700,"end_ms":780},{"word":"inbox","start_ms":800,"end_ms":980},
{"word":"problem","start_ms":1000,"end_ms":1300}]
Output EDL:
{"style":"talking_head","format_id":"myth-buster","segments":[{"src_in":0,"src_out":39}],
"drops":[{"src_in":17,"src_out":20,"reason":"filler"}],
"captions":[{"word":"You","frame":0},{"word":"don't","frame":7},{"word":"have","frame":13},
{"word":"an","frame":21},{"word":"inbox","frame":24},{"word":"problem","frame":30}],
"overlays":[{"type":"punch_in","src_in":24,"src_out":30,"scale":1.08,"text":""}],
"broll":[],"layout":{"style":"talking_head","panels":1},"audio":{"lufs_target":-14.0}}
''',
    "faceless": '''
ShotPlan beats: ["0-3s: close-up of dense torn crumb","3-8s: two dough bowls side by side","8-12s: thermometer in dough + text card"]
Transcript words: [{"word":"Your","start_ms":0,"end_ms":150},{"word":"sourdough","start_ms":170,"end_ms":500},
{"word":"is","start_ms":520,"end_ms":580},{"word":"dense","start_ms":600,"end_ms":900}]
Output EDL:
{"style":"faceless","format_id":"faceless","segments":[{"src_in":0,"src_out":27}],
"drops":[],"captions":[{"word":"Your","frame":0},{"word":"sourdough","frame":5},{"word":"is","frame":16},{"word":"dense","frame":18}],
"overlays":[],"broll":[{"src_in":0,"src_out":90,"cue_text":"close-up of dense torn crumb"},
{"src_in":90,"src_out":240,"cue_text":"two dough bowls side by side"},
{"src_in":240,"src_out":360,"cue_text":"thermometer in dough + text card"}],
"layout":{"style":"faceless","panels":1},"audio":{"lufs_target":-14.0}}
''',
    "split_three": '''
Body: "Solution 1: widen stance. Solution 2: slow descent to 3 seconds. Solution 3: screw feet into the floor."
Output EDL:
{"style":"split_three","format_id":"listicle","segments":[{"src_in":0,"src_out":234}],
"drops":[],"captions":[],"overlays":[],"broll":[],
"layout":{"style":"split_three","panels":3,"panel_boundaries":[78,156]},
"audio":{"lufs_target":-14.0}}
''',
    "fast_cuts": '''
Body: "One: check back. Two: forward. Three: check up."
Transcript: [{"word":"One","start_ms":0,"end_ms":200},{"word":"check","start_ms":220,"end_ms":380},
{"word":"back","start_ms":400,"end_ms":600},{"word":"Two","start_ms":900,"end_ms":1100}]
Output EDL:
{"style":"fast_cuts","format_id":"listicle",
"segments":[{"src_in":0,"src_out":18},{"src_in":27,"src_out":33}],
"drops":[{"src_in":18,"src_out":27,"reason":"dead_air"}],
"captions":[{"word":"One","frame":0},{"word":"check","frame":7},{"word":"back","frame":12},{"word":"Two","frame":27}],
"overlays":[],"broll":[],"layout":{"style":"fast_cuts","panels":1},"audio":{"lufs_target":-14.0}}
''',
    "green_screen": '''
Body: "This post says train six times a week. Wrong — twice beats six."
Output EDL:
{"style":"green_screen","format_id":"green-screen","segments":[{"src_in":0,"src_out":630}],
"drops":[],"captions":[],"overlays":[{"type":"text_card","src_in":0,"src_out":30,"scale":1.0,"text":"Viral post: train 6x/week"}],
"broll":[],"layout":{"style":"green_screen","panels":1},"audio":{"lufs_target":-14.0}}
''',
}


def edl_prompt(style: str, transcript_words: list[dict], script: dict, brand: dict,
               media_context: str = "") -> tuple[str, str]:
    """Return (system, user) for the per-style EDL generation call."""
    rubric = EDIT_RUBRICS.get(style, EDIT_RUBRICS["talking_head"])
    exemplar = EDL_EXEMPLARS.get(style, "")
    shot_plan = script.get("shotPlan", [])
    body = script.get("body", "")
    hook = script.get("hook", "")
    cta = script.get("cta", "")
    format_id = script.get("formatId", "myth-buster")

    system = f"""{rubric}

EDL JSON schema (output ONLY valid JSON matching this schema, no prose):
{{
  "style": "{style}",
  "format_id": "{format_id}",
  "segments": [{{"src_in": int, "src_out": int}}, ...],
  "drops": [{{"src_in": int, "src_out": int, "reason": "filler|dead_air|false_start"}}, ...],
  "captions": [{{"word": str, "frame": int}}, ...],
  "overlays": [{{"type": "punch_in|text_card", "src_in": int, "src_out": int, "scale": float, "text": str}}, ...],
  "broll": [{{"src_in": int, "src_out": int, "cue_text": str, "asset_id": null, "broll_query": str}}, ...],
  "layout": {{"style": "{style}", "panels": int, "panel_boundaries": [int, ...]}},
  "audio": {{"lufs_target": -14.0}}
}}

Worked example for {style}:
{exemplar}"""

    user = f"""Script:
Hook: {hook}
Body: {body}
CTA: {cta}
ShotPlan: {json.dumps(shot_plan)}
Style: {style}
Format: {format_id}

Word-level transcript (30fps, frame = round(start_ms/1000*30)):
{json.dumps(transcript_words[:200])}

Media context: {media_context or "none"}

Generate the EDL for this {style} edit. Output JSON only."""

    return system, user


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
    lines = [
        "Creator brand:",
        f"- niche: {brand.get('niche','')}",
        f"- what they do: {brand.get('what_you_do','')}",
        f"- audience: {brand.get('audience','')}",
        f"- wants to be known for: {brand.get('known_for','')}",
        f"- goal: {brand.get('goal','Grow my audience')}",
        f"- voice (0..1): funny→serious {v.get('funnyToSerious',0.5)}, "
        f"polished→raw {v.get('polishedToRaw',0.5)}, teacher→peer {v.get('teacherToPeer',0.5)}",
        f"- never say: {', '.join(brand.get('non_negotiables', []) or [])}",
    ]
    if brand.get('primary_platform'):
        lines.append(f"- primary platform: {brand['primary_platform']}")
    if brand.get('stage'):
        lines.append(f"- creator stage: {brand['stage']} (calibrate authority level accordingly)")
    if brand.get('posting_frequency'):
        lines.append(f"- current posting frequency: {brand['posting_frequency']}")
    if brand.get('biggest_blocker'):
        blocker_map = {
            'ideas': 'generate hooks and topics generously',
            'time': 'keep scripts tight and batch-friendly',
            'editing': 'favor simple single-shot formats over complex cuts',
            'confidence': 'lean toward voiceover/faceless formats to build comfort',
        }
        blocker = brand['biggest_blocker']
        hint = blocker_map.get(blocker, '')
        lines.append(f"- biggest blocker: {blocker}" + (f" → {hint}" if hint else ""))
    if brand.get('camera_comfort'):
        comfort_map = {
            'natural': 'talking-head and green-screen styles preferred',
            'getting_there': 'mix of talking-head and faceless; build confidence gradually',
            'prefer_off': 'faceless voiceover and fast-cuts preferred; minimize on-camera',
        }
        comfort = brand['camera_comfort']
        hint = comfort_map.get(comfort, '')
        lines.append(f"- camera comfort: {comfort}" + (f" → {hint}" if hint else ""))
    if brand.get('weekly_target'):
        lines.append(f"- weekly post target: {brand['weekly_target']} posts (plan batch scripts to hit this)")
    return "\n".join(lines) + _post_lines(posts)


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
                   media_context: str = "", posts: list[dict] | None = None,
                   arm_stats: list[dict] | None = None) -> tuple[str, str]:
    s = STYLES.get(style, STYLES["talking_head"])
    system = (
        f"You are Marque's script engine writing {s['label']} short-form videos. "
        "Write in the creator's EXACT voice — match their tone sliders, echo their real phrasing, and NEVER use "
        "a banned phrase. The hook must stop the scroll in the first 3 seconds. "
        f"\n\n{VIRALITY_BLOCK}\n\n"
        f"STYLE RULES ({s['label']}): {s['rubric']}\n\n"
        f"A correctly-structured example for this style (match the STRUCTURE, not the content):\n{s['exemplar']}\n\n"
        "Reply with ONLY valid JSON, no prose, no code fences."
    )
    media = f"\nReference footage the creator already has (reuse where natural): {media_context}" if media_context else ""
    learn = learning_block(arm_stats or [])
    learn_section = f"\n{learn}\n" if learn else ""
    user = (
        f"{learn_section}"
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

def hooks_prompt(brand: dict, topic: str, style: str = "talking_head",
                 arm_stats: list[dict] | None = None) -> tuple[str, str]:
    system = (
        "You are Marque's hook engine. Generate scroll-stopping first-3-second hooks in the creator's voice "
        "across the 8 signal types. Each hook must be DIFFERENT in structure and signal — no two hooks "
        "should have the same opening pattern. Ranked strongest first.\n\n"
        f"{VIRALITY_BLOCK}\n\n"
        "Example output for a fitness creator on 'protein intake':\n"
        '[\n'
        '  {"text": "You\'re eating enough protein. You\'re just eating it wrong.", "signal": "contrarian", "strength": 91},\n'
        '  {"text": "I tracked every gram for 90 days. Here\'s what actually moved the needle.", "signal": "authority", "strength": 88},\n'
        '  {"text": "The protein timing window is a myth — here\'s what isn\'t.", "signal": "curiosity", "strength": 85}\n'
        "]\n\nReply with ONLY a JSON array, no prose."
    )
    learn = learning_block(arm_stats or [])
    extra = f"\n{learn}" if learn else ""
    user = (
        f"{brand_block(brand)}\nTopic: {topic}\nStyle: {STYLES.get(style, STYLES['talking_head'])['label']}\n{extra}"
        f"Return ONLY a JSON array of 6 hooks with diverse signals and structures. Each: "
        f'{{\"text\": str, \"signal\": one of {SIGNALS}, \"strength\": int 0-100}}'
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
    system = (
        "You explain in one tight insight why a short-form clip performed, plus the single next move. "
        "Reply with ONLY a JSON object.\n\n"
        "Example (high performer):\n"
        '{"headline": "This beat 73% of your posts", '
        '"detail": "The contrarian open created a pattern interrupt in the first 1.5 seconds and the single '
        'specific detail (\\"42 days\\") gave the claim credibility. Fast cuts matched the energy of the hook.", '
        '"liftPercent": 73}\n\n'
        "Example (average performer):\n"
        '{"headline": "Solid clip — one tweak to punch up the next one", '
        '"detail": "The hook opened on a question, which tends to underperform vs. a statement. '
        'Try leading with the unexpected conclusion next time.", '
        '"liftPercent": 12}'
    )
    metrics = clip.get("metrics", {}) or {}
    metrics_line = ""
    if metrics.get("views", 0) > 0:
        metrics_line = (
            f"\nReal metrics: {metrics.get('views',0)} views, {metrics.get('likes',0)} likes, "
            f"{metrics.get('comments',0)} comments, {metrics.get('shares',0)} shares, "
            f"{metrics.get('saves',0)} saves, {metrics.get('avg_watch_pct',0)*100:.0f}% avg watch"
        )
    user = (
        f"Clip: format={clip.get('formatName','')}, caption=\"{clip.get('caption','')}\", "
        f"predicted score={clip.get('predictedScore',0)}.{metrics_line}\n"
        'Return ONLY: {"headline": str (≤12 words), "detail": str (2 tight sentences), "liftPercent": int}'
    )
    return system, user


def insights_prompt(brand: dict, summary: str) -> tuple[str, str]:
    system = (
        "You are Marque's growth coach. Give exactly TWO tight sentences: "
        "sentence 1 names the single strongest signal (format, hook type, or topic) that's working; "
        "sentence 2 names the exact next move. No fluff, no preamble, no lists."
    )
    return system, f"{brand_block(brand)}\nThis week's performance summary: {summary}\nTwo sentences."


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


def media_analyze_prompt(filename: str, kind: str = "photo") -> tuple[str, str]:
    """Return (system, user) for multimodal media analysis."""
    system = (
        "You analyze creator media assets (photos and videos) to determine their B-roll suitability. "
        "Return ONLY valid JSON matching this schema exactly:\n"
        '{"description": str (concrete, searchable, 1-2 sentences — write as if describing it to someone '
        "who can't see it), "
        '"scene": str (indoor/outdoor/studio/nature/urban/other), '
        '"subjects": [str] (list of main subjects — people, objects, actions), '
        '"has_face": bool, '
        '"on_screen_text": str (any text visible in the frame, empty string if none), '
        '"motion": "none|slow|medium|fast", '
        '"quality": "low|medium|high", '
        '"dominant_colors": [str] (top 3 color names), '
        '"broll_suitability": int 0-100 (100 = perfect B-roll, 0 = unusable — talking-head takes score 0-20), '
        '"broll_suitability_reason": str (one sentence why), '
        '"usable_as": "broll"|"take"|"thumbnail"|"other", '
        '"suggested_kind": "photo"|"video"|"screen", '
        '"tags": [str] (5-10 searchable tags)}'
    )
    user = f"Analyze this {kind} asset for B-roll use in short-form social videos. File: {filename}"
    return system, user


def broll_match_prompt(cue_text: str, candidates: list[dict]) -> tuple[str, str]:
    """Return (system, user) for Haiku tie-break among B-roll candidates."""
    system = (
        "You select the best B-roll clip for a video beat. Given a shot description and candidates, "
        'return ONLY: {"chosen_index": int, "reason": str (≤10 words why this clip fits the beat)}'
    )
    user = (f"Beat: \"{cue_text}\"\n\n"
            f"Candidates:\n{json.dumps(candidates, indent=2)}\n\n"
            "Which candidate index best matches the beat? Return JSON only.")
    return system, user


def learning_block(arm_stats: list[dict]) -> str:
    """Generate the learning context block injected into script/pillar prompts."""
    if not arm_stats:
        return ""
    lines = ["CREATOR PERFORMANCE DATA (use to inform hook/format choices):"]
    for s in arm_stats[:5]:
        lift = s.get("lift_pct", 0)
        label = s.get("label", "")
        if label and abs(lift) >= 5:
            lines.append(f"- {label} ({s.get('confidence', 'early_read')})")
    if len(lines) == 1:
        return ""
    lines.append("Lean into outperforming signals; avoid confirmed underperformers.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Virality expertise — shared knowledge block injected into converse / scripts /
# mimic / analyze prompts so every surface reasons like a short-form editor.
# ---------------------------------------------------------------------------

VIRALITY_BLOCK = (
    "SHORT-FORM MASTERY (2026 rules — apply these when writing or judging content):\n"
    "- The first 1.5 seconds decide everything. Open mid-thought on the most surprising claim; never greet, "
    "never introduce, never set up. The first FRAME should already be interesting (motion, tight framing, or "
    "an on-screen line that contradicts expectation).\n"
    "- Watch-time is the master metric on both TikTok and Reels. Retention beats likes: a 70% avg-watch clip "
    "out-distributes a 10x-liked clip. Cut anything that doesn't earn its second.\n"
    "- Retention mechanics: change something visually every 2–4 seconds (cut, punch-in, caption card, prop). "
    "Open a loop in the hook ('the third one changed everything') and close it only at the end. Use pattern "
    "interrupts at the 30% and 70% marks where drop-off spikes.\n"
    "- Specificity converts: '42 days', '$3,180', 'the 6am rule' outperform vague claims every time. One "
    "concrete number in the hook is worth three adjectives.\n"
    "- Hooks that work: contrarian reversal ('everyone says X — it's backwards'), stakes ('this mistake costs "
    "you followers daily'), authority-with-receipts ('I posted 90 days straight; here's the data'), curiosity "
    "gap with a payoff you actually deliver. Question-openers underperform statements.\n"
    "- CTA norms: one CTA max, spoken in the last 2 seconds, matched to the goal (follows → 'follow for the "
    "next one', saves → 'save this for your next X', comments → a one-word prompt). Never stack CTAs.\n"
    "- Platform notes: TikTok rewards raw, native-feeling, trend-aware content with on-screen text from frame "
    "one; Reels rewards slightly more polished, loopable clips and shares-to-DM. Captions: TikTok = short + "
    "keyword-loaded (search is real), IG = a hook line then whitespace then substance.\n"
    "- Cadence compounds: 3–7 posts/week beats bursts. Consistency + iteration on what the data says beats "
    "chasing every trend. Trend-jack only when the creator can add their OWN take within 48h of the wave."
)


# ---------------------------------------------------------------------------
# Conversation engine — the creator's daily strategist (voice bubble + chat)
# ---------------------------------------------------------------------------

MEMORY_FIELDS = ["facts", "perspective", "ideas", "preferences"]  # list fields; "angle" is a single string


def memory_block(memory: dict | None) -> str:
    """Format the client-held creator memory for prompt injection."""
    if not memory:
        return "CREATOR MEMORY: (empty — this is a new relationship; start learning who they are)"
    lines = ["CREATOR MEMORY (what you already know about this creator — treat as ground truth):"]
    angle = (memory.get("angle") or "").strip()
    if angle:
        lines.append(f"- current brand angle: {angle}")
    labels = {"facts": "facts", "perspective": "their perspective/beliefs",
              "ideas": "active content ideas", "preferences": "preferences"}
    for field in MEMORY_FIELDS:
        items = [x for x in (memory.get(field) or []) if isinstance(x, str) and x.strip()]
        if items:
            lines.append(f"- {labels[field]}:")
            lines.extend(f"    • {x}" for x in items[:30])
    if len(lines) == 1:
        return "CREATOR MEMORY: (empty — this is a new relationship; start learning who they are)"
    return "\n".join(lines)


CONVERSE_ENVELOPE_SCHEMA = (
    '{"reply": str, '
    '"memory_updates": [{"op": "add"|"remove"|"set", "field": "facts"|"perspective"|"ideas"|"preferences"|"angle", "value": str}], '
    '"intent": "none"|"generate_scripts"|"day_plan"|"save_idea"|"update_brand_angle", '
    '"intent_args": object (see intent rules), '
    '"chips": [str] (2-3 short suggested next messages, ≤6 words each)}'
)

CONVERSE_ENVELOPE_EXEMPLAR = (
    'User said: "I\'ve been thinking my content is too soft. I want to take harder stances on training myths. '
    'Also had an idea about debunking the anabolic window."\n'
    "Correct envelope:\n"
    '{"reply": "Love this direction — harder stances is exactly where your authority shows. The anabolic-window '
    'debunk is a perfect first swing: it\'s a myth half your audience still believes, and you can bring receipts. '
    'Want me to write it up as a script?", '
    '"memory_updates": ['
    '{"op": "set", "field": "angle", "value": "Taking harder, evidence-backed stances against training myths"}, '
    '{"op": "add", "field": "ideas", "value": "Debunk the anabolic window myth (with receipts)"}], '
    '"intent": "update_brand_angle", "intent_args": {}, '
    '"chips": ["Write the anabolic window script", "What else should I debunk?", "Build my day"]}'
)


def converse_system(mode: str = "chat") -> str:
    """System prompt for /v1/converse. mode: voice | chat."""
    voice_style = (
        "This is a SPOKEN conversation (the creator is talking to you out loud; your reply is read aloud by TTS). "
        "Reply in 2–4 short conversational sentences. NO markdown, NO lists, NO emoji, no stage directions — "
        "just natural speech, warm and direct, like a sharp friend who happens to be a content strategist."
    )
    chat_style = (
        "This is a TEXT chat. Keep replies tight (under ~120 words unless asked for depth). Markdown is fine "
        "(bold for emphasis, short lists when genuinely useful). Never pad; never repeat their message back."
    )
    style = voice_style if mode == "voice" else chat_style
    return (
        "You are Marque — a personal content strategist who KNOWS this creator and talks with them every day. "
        "You are an elite short-form expert (hooks, retention, platform mechanics) AND their thinking partner: "
        "they share morning thoughts, perspective shifts, brand-angle changes, and raw ideas; you sharpen them "
        "and remember everything.\n\n"
        f"{VIRALITY_BLOCK}\n\n"
        f"CONVERSATION STYLE: {style}\n\n"
        "MEMORY RULES: You maintain a persistent memory of this creator. After every exchange, emit memory_updates "
        "for anything durable they revealed: stable facts about them/their life ('facts'), how they see the world "
        "or their positioning ('perspective'), content ideas worth keeping ('ideas'), workflow/format preferences "
        "('preferences'), and 'angle' (op=set) when their brand direction shifts. Write each value as one crisp "
        "self-contained sentence. Do NOT store small talk, questions, or anything transient. 0–3 updates per turn "
        "is normal; empty list is fine.\n\n"
        "INTENT RULES: Set intent when the creator asks for one of these, else \"none\".\n"
        "- generate_scripts: they want a script/scripts written now. intent_args: {\"topic\": str, "
        "\"style\": one of [talking_head, faceless, split_three, fast_cuts, green_screen] or \"\", \"count\": 1-3}. "
        "Your reply should tee up the scripts conversationally (they are generated and attached automatically).\n"
        "- day_plan: they want their day/content day built out. intent_args: {\"plan\": {\"blocks\": "
        "[{\"time\": str (e.g. \"9:00\"), \"action\": str (≤6 words), \"detail\": str (one sentence)}]}} — "
        "build a realistic filming/posting day from their weekly target, blockers, and active ideas (4-6 blocks).\n"
        "- save_idea: they shared an idea to remember (also add it to memory ideas). intent_args: {}.\n"
        "- update_brand_angle: their brand direction/angle shifted (also set memory angle). intent_args: {}.\n\n"
        f"OUTPUT: Reply with ONLY a valid JSON object matching exactly: {CONVERSE_ENVELOPE_SCHEMA}\n"
        "No prose outside the JSON, no code fences.\n\n"
        f"Worked example:\n{CONVERSE_ENVELOPE_EXEMPLAR}"
    )


def converse_user(brand: dict, memory: dict | None, messages: list[dict],
                  arm_stats: list[dict] | None = None, trends: list[dict] | None = None) -> str:
    """User content for /v1/converse: brand + memory + performance + recent transcript."""
    parts = [brand_block(brand), "", memory_block(memory)]
    learn = learning_block(arm_stats or [])
    if learn:
        parts += ["", learn]
    if trends:
        tl = "; ".join(f"{t.get('title','')}" for t in trends[:3] if t.get("title"))
        if tl:
            parts += ["", f"Trending in their niche right now: {tl}"]
    parts += ["", "CONVERSATION (most recent last):"]
    for m in messages[-20:]:
        role = "Creator" if m.get("role") == "user" else "You"
        content = (m.get("content") or "").strip()
        if content:
            parts.append(f"{role}: {content}")
    parts += ["", "Respond to the creator's last message. Output the JSON envelope only."]
    return "\n".join(parts)
