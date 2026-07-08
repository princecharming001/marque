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

from app.edl import ms_to_frame, TWEAK_OP_TYPES

OPUS = "claude-opus-4-8"
HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"

FORMAT_IDS = [
    "myth-buster", "listicle", "do-this-not-that", "before-after",
    "green-screen", "faceless", "pov-story", "broll-hook",
]

# The render/edit styles offered in-app right now. `fast_cuts` stays defined in STYLES
# (and its Remotion composition stays registered) but is held back from the active set
# until later — mirror this list in the iOS VideoStyle "offered" list.
ACTIVE_STYLES = [
    "talking_head", "green_screen", "broll_cutaway", "split_three", "duet_split", "faceless",
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
    "broll_cutaway": {
        "label": "B-roll cutaway",
        "formats": ["myth-buster", "listicle", "do-this-not-that"],
        "rubric": (
            "You speak to camera and the editor cuts away to short illustrative b-roll on your key words. Write "
            "`body` around CONCRETE VISUAL NOUNS/ACTIONS, ONE showable thing per sentence, and emit a bracketed "
            "cue right after the clause it illustrates: '… your lower back rounds [broll: rounded-back deadlift] "
            "and the force leaks out.' The bracket text is BOTH a searchable stock query AND the on-screen anchor "
            "— make it specific and filmable ('hands gripping a barbell close up', not 'gym'). Aim for 3–5 cues in "
            "a 30s script (one roughly every 4–6s). The HOOK line and the CTA line carry NO bracket cues — those "
            "beats stay on your face. Never write abstract lines with nothing to show. `shotPlan` lists the "
            "cutaways in order. 22–34 seconds."
        ),
        "exemplar": (
            '{"title":"why your deadlift stalls","summary":"A talking-head with b-roll cutaways on 3 deadlift '
            'mistakes.","hook":"Your deadlift stopped going up — and it\'s not because you\'re weak.",'
            '"hookSignal":"contrarian","formatId":"myth-buster","body":"It\'s three mistakes, starting with your '
            'grip [broll: hands gripping a loaded barbell close up]. Mixed grip stops the bar rolling out of your '
            'fingers [broll: barbell knurling rotating detail] and that alone adds reps. Mistake two: your lower '
            'back rounds under the weight [broll: rounded-back deadlift silhouette] and the force leaks out. Fix '
            'it by bracing like you\'re about to get punched, then add five pounds every session [broll: hand '
            'sliding a small plate onto a barbell].","cta":"Follow for the full program.","shotPlan":["Hook on '
            'face, no b-roll","Cutaway: grip close-up","Cutaway: bar knurling","Cutaway: rounded back",'
            '"Cutaway: adding a plate","Return to face for CTA"],"targetSeconds":30,"predictedScore":86}'
        ),
    },
    "duet_split": {
        "label": "Duet / react split",
        "formats": ["green-screen", "do-this-not-that"],
        "rubric": (
            "A stacked split: the clip you're reacting to plays on TOP, you react on the BOTTOM. Write `body` as a "
            "talk-back to that clip: (1) your hook POINTS AT the other clip and states your stance in one breath "
            "('this guy says X — he\'s half right, and it\'s the dangerous half'); (2) QUOTE or paraphrase the "
            "exact claim out loud so a muted viewer follows; (3) rebut ONE point per beat; (4) end on a concrete "
            "'do this instead' PAYOFF (react formats that only mock underperform). Reference the source verbally "
            "('when he says…', 'notice she skips…'). `shotPlan`: the play/freeze rhythm — ['Let it play 2s', "
            "'Freeze, state stance', 'Release the claim then rebut', 'Payoff + punch-in', 'CTA']. 22–35 seconds."
        ),
        "exemplar": (
            '{"title":"reacting to failure advice","summary":"A duet react to a viral train-to-failure claim.",'
            '"hook":"This guy says train every single set to failure — he\'s half right, and it\'s the dangerous '
            'half.","hookSignal":"contrarian","formatId":"green-screen","body":"Okay, pause. Training to failure '
            'every set spikes fatigue so hard your next sets get weaker — so your total volume, the thing that '
            'actually builds muscle, goes down. Here\'s the rule that works: leave one to two reps in the tank on '
            'your early sets, and only take your LAST set to failure. Same growth, half the wreckage.",'
            '"cta":"Follow for training that keeps you out of the physio.","shotPlan":["Let the clip play 2s on '
            'its own audio","Freeze it, state the stance","Release the claim, then rebut","Payoff: the real rule, '
            'punch-in on your face","CTA to camera"],"targetSeconds":29,"predictedScore":85}'
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
    "broll_cutaway": (
        "You are an expert b-roll-cutaway editor. Rules: (1) The talking head is the spine — keep the whole take, "
        "cut filler words and gaps. (2) Parse every [broll: ...] cue from the body; each becomes ONE broll entry "
        "with cue_text = the bracket text, broll_query = the same concrete search, source='stock'. (3) Set each "
        "broll src_in/src_out as a TIMELINE window (when the cutaway appears): src_in ≈ the cue word's caption "
        "frame minus 12 (a ~0.4s J-cut lead), hold ~75 frames (2.5s). (4) Cutaways must never overlap and stay "
        "≥90 frames (3s) apart; if two cues are closer, keep the stronger and drop the weaker. (5) NEVER place a "
        "cutaway with src_in < 60 (protect the hook) or whose window enters the last 90 frames (protect the CTA — "
        "end on the face). (6) No punch_in, no text_card, no panels. Captions word-by-word. Output valid EDL JSON only."
    ),
    "duet_split": (
        "You are an expert duet/react-split editor. Rules: (1) The creator's recording is the BOTTOM panel talking "
        "head — keep the whole take, cut filler. (2) Build react_schedule for the TOP panel (the reacted-to clip): "
        "open with a 'play' window [0, ~55] (audio_gain 1.0) so the source speaks first, then alternate 'freeze' "
        "windows during the creator's rebuts (audio_gain 0.15, clip_from = where the source paused) and short "
        "'play' windows (audio_gain 1.0, 60–120 frames) that release the next source point. No play window > ~120 "
        "frames. Windows must tile the whole timeline with no gaps. (3) Add text_card overlays for the exact claim "
        "being rebutted (pull-quotes), timed to the freeze that follows the quoted line. (4) ONE punch_in on the "
        "payoff line (scale ~1.12). (5) layout.panels=2, split_fraction=0.58. Output valid EDL JSON only."
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
    "broll_cutaway": '''
Body: "It's your grip [broll: hands gripping a barbell]. Your lower back rounds [broll: rounded-back deadlift]. Add five pounds every session."
(hook occupies frames 0-60; 'grip' lands ~frame 90, 'rounds' ~frame 222; the final CTA
sentence keeps the last 90 frames [330,420] cutaway-free so the video ends on the face)
Output EDL:
{"style":"broll_cutaway","format_id":"myth-buster","segments":[{"src_in":0,"src_out":420}],
"drops":[],
"captions":[{"word":"It's","frame":66},{"word":"your","frame":78},{"word":"grip","frame":90},
{"word":"Your","frame":198},{"word":"lower","frame":204},{"word":"back","frame":210},{"word":"rounds","frame":222},
{"word":"Add","frame":336},{"word":"five","frame":348},{"word":"pounds","frame":360}],
"overlays":[],
"broll":[{"src_in":78,"src_out":153,"cue_text":"hands gripping a barbell","broll_query":"hands gripping a barbell close up","source":"stock"},
{"src_in":210,"src_out":285,"cue_text":"rounded-back deadlift","broll_query":"rounded back deadlift silhouette","source":"stock"}],
"layout":{"style":"broll_cutaway","panels":1},"audio":{"lufs_target":-14.0}}
''',
    "duet_split": '''
Body: "He says train to failure every set. That kills your volume. Do this instead: last set only."
Output EDL:
{"style":"duet_split","format_id":"green-screen","segments":[{"src_in":0,"src_out":300}],
"drops":[],
"captions":[{"word":"He","frame":60},{"word":"says","frame":66},{"word":"train","frame":72}],
"overlays":[{"type":"text_card","src_in":72,"src_out":150,"scale":1.0,"text":"train to failure EVERY set"},
{"type":"punch_in","src_in":240,"src_out":300,"scale":1.12,"text":""}],
"broll":[],"react_source":null,
"react_schedule":[{"state":"play","src_in":0,"src_out":55,"clip_from":0,"audio_gain":1.0},
{"state":"freeze","src_in":55,"src_out":150,"clip_from":55,"audio_gain":0.15},
{"state":"play","src_in":150,"src_out":215,"clip_from":55,"audio_gain":1.0},
{"state":"freeze","src_in":215,"src_out":300,"clip_from":120,"audio_gain":0.15}],
"layout":{"style":"duet_split","panels":2,"split_fraction":0.58},"audio":{"lufs_target":-14.0}}
''',
}


def _span_lines(spans: list | None, limit: int = 40) -> str:
    return ", ".join(f"[{a}-{b}]" for a, b in (spans or [])[:limit]) or "none"


def edl_prompt(style: str, transcript_words: list[dict], script: dict, brand: dict,
               media_context: str = "",
               disfluency_spans: list | None = None,
               emphasis_spans: list | None = None) -> tuple[str, str]:
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
  "broll": [{{"src_in": int, "src_out": int, "cue_text": str, "asset_id": null, "broll_query": str, "source": "stock|own_media"}}, ...],
  "react_source": null,
  "react_schedule": [{{"state": "play|freeze", "src_in": int, "src_out": int, "clip_from": int, "audio_gain": float}}, ...],
  "layout": {{"style": "{style}", "panels": int, "panel_boundaries": [int, ...], "split_fraction": float}},
  "audio": {{"lufs_target": -14.0}}
}}
Note: `broll` is used only by broll_cutaway/faceless; `react_source`/`react_schedule` only by duet_split; `panel_boundaries` only by split_three; `split_fraction` only by duet_split. Leave unused fields as [] or null.

Worked example for {style}:
{exemplar}"""

    grounding = f"""
GROUNDED SIGNALS (from the transcript — trust these over your own guesses):
- FILLER/DISFLUENCY frames already detected (put these in `drops` as reason="filler"; do NOT keep them): {_span_lines(disfluency_spans)}
- HIGH-EMPHASIS frames (the creator's own stressed words + auto-detected key phrases — place `punch_in` overlays HERE, not on flat delivery): {_span_lines(emphasis_spans)}"""

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
{grounding}

Generate the EDL for this {style} edit. Output JSON only."""

    return system, user


def edl_verify_prompt(style: str, edl_json: dict, transcript_words: list[dict],
                      emphasis_spans: list | None = None) -> tuple[str, str]:
    """A strict, cheap invariant check on a generated EDL — the renderer can't
    recover from these, so catch them before we spend a render."""
    last_frame = ms_to_frame(max((w.get("end_ms", 0) for w in transcript_words), default=30000)) \
        if transcript_words else 30000
    system = (
        "You are a video-editor QA gate. Check the EDL against hard invariants and reply with ONLY "
        'JSON: {"verdict": "pass" | "revise", "issues": [str], "fix": str}. '
        "Flag an issue when:\n"
        "- any segment or drop has src_out <= src_in, or a segment extends past the source's last frame;\n"
        "- segments overlap each other;\n"
        "- a caption frame or an overlay/broll window falls outside every kept segment;\n"
        "- a punch_in overlay sits on flat delivery instead of a high-emphasis region;\n"
        "- total kept duration is implausibly short (<3s) or the whole take is one static shot with no "
        "emphasis punch-ins despite emphasis regions existing;\n"
        "- react_source/react_schedule present for a non-duet style, or panel_boundaries missing for split_three.\n"
        "Be precise and terse. verdict='pass' only if there are zero hard issues."
    )
    user = (
        f"Style: {style}\nSource last frame: {last_frame}\n"
        f"High-emphasis regions (good punch-in targets): {_span_lines(emphasis_spans)}\n\n"
        f"EDL:\n{json.dumps(edl_json)[:6000]}\n\nJudge it."
    )
    return system, user


def edl_repair_prompt(style: str, broken_edl: dict, issues: list[str],
                      transcript_words: list[dict], script: dict) -> tuple[str, str]:
    """Fix ONLY the flagged invariant violations, preserving the creative intent."""
    rubric = EDIT_RUBRICS.get(style, EDIT_RUBRICS["talking_head"])
    last_frame = ms_to_frame(max((w.get("end_ms", 0) for w in transcript_words), default=30000)) \
        if transcript_words else 30000
    system = (
        f"You repair a short-form EDL. {rubric}\n\n"
        "A QA gate found the issues listed below. Fix EXACTLY those and nothing else — keep every valid "
        "cut, caption, punch-in, and b-roll cue as-is. Keep all frames within [0, source last frame]. "
        "Output ONLY the corrected EDL as valid JSON, same schema as the input."
    )
    user = (
        f"Source last frame: {last_frame}\n"
        f"Issues to fix:\n" + "\n".join(f"- {i}" for i in issues) + "\n\n"
        f"Current EDL:\n{json.dumps(broken_edl)}\n\nReturn the corrected EDL JSON only."
    )
    return system, user


# ---------------------------------------------------------------------------
# Conversational tweaks — the creator chats small changes to a finished edit.
# The LLM's ONLY job is interpretation: user words -> typed ops (applied
# deterministically by app.edl.apply_edl_ops) + a chat reply. It never writes
# EDL JSON itself.
# ---------------------------------------------------------------------------

# Structured-output envelope. Op params are union-typed with null (SO supports
# null types; the repo schema-guard test only inspects object/array branches).
TWEAK_ENVELOPE_JSON_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["reply", "ops"],
    "properties": {
        "reply": {"type": "string"},
        "ops": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["type", "style", "enabled", "start_frame", "end_frame",
                         "scale", "text", "query", "value", "kind", "frames"],
            "properties": {
                "type": {"type": "string", "enum": TWEAK_OP_TYPES},
                "style": {"type": ["string", "null"]},
                "enabled": {"type": ["boolean", "null"]},
                "start_frame": {"type": ["integer", "null"]},
                "end_frame": {"type": ["integer", "null"]},
                "scale": {"type": ["number", "null"]},
                "text": {"type": ["string", "null"]},
                "query": {"type": ["string", "null"]},
                "value": {"type": ["number", "null"]},
                "kind": {"type": ["string", "null"]},
                "frames": {"type": ["integer", "null"]},
            },
        }},
    },
}


def _edl_summary(edl: dict) -> str:
    """Compact human-readable state of the edit for the tweak prompt — the raw
    captions array alone can be 200+ entries, so summarize instead."""
    segs = edl.get("segments") or []
    drops = edl.get("drops") or []
    overlays = edl.get("overlays") or []
    broll = edl.get("broll") or []
    kept = sum(s["src_out"] - s["src_in"] for s in segs) - sum(d["src_out"] - d["src_in"] for d in drops)
    lines = [
        f"style: {edl.get('style')}  |  kept duration: {kept} frames (~{kept / 30:.1f}s)",
        f"segments: {[(s['src_in'], s['src_out']) for s in segs]}",
        f"cuts (drops): {[(d['src_in'], d['src_out'], d.get('reason', '')) for d in drops] or 'none'}",
        f"overlays: {[(o.get('type'), o['src_in'], o['src_out'], o.get('scale'), (o.get('text') or '')[:30]) for o in overlays] or 'none'}",
        f"b-roll: {[(b['src_in'], b['src_out'], (b.get('cue_text') or '')[:40]) for b in broll] or 'none'}",
        f"captions: {len(edl.get('captions') or [])} words, style={edl.get('caption_style') or 'clean'}",
    ]
    if edl.get("style") == "duet_split":
        lines.append(f"split_fraction: {(edl.get('layout') or {}).get('split_fraction', 0.58)}")
    return "\n".join(lines)


def tweak_prompt(edl: dict, transcript_words: list[dict], instruction: str,
                 history: list[dict] | None = None) -> tuple[str, str]:
    """(system, user) for one conversational tweak turn."""
    system = (
        "You are Marque's edit assistant. A creator is chatting small changes to a FINISHED short-form "
        "edit. Translate their request into zero or more typed operations from this fixed vocabulary — "
        "you NEVER write edit data yourself, the server applies ops deterministically:\n"
        "- set_caption_style {style: clean|bold-word|karaoke}\n"
        "- set_captions_enabled {enabled}\n"
        "- cut_range {start_frame, end_frame} — remove a section of footage\n"
        "- restore_range {start_frame, end_frame} — bring back previously cut footage\n"
        "- remove_overlays {kind: punch_in|text_card|all, start_frame?, end_frame?}\n"
        "- add_punch_in {start_frame, end_frame, scale 1.02-1.35}\n"
        "- add_text_card {start_frame, end_frame, text}\n"
        "- add_broll {start_frame, end_frame, query} (only broll_cutaway/faceless styles)\n"
        "- remove_broll {start_frame?, end_frame?}\n"
        "- set_split_fraction {value 0.3-0.75} (duet only)\n"
        "- trim_start {frames} / trim_end {frames}\n"
        "- undo {} — revert the last tweak\n\n"
        "FRAME MATH: 30fps; frame = round(seconds * 30). The transcript below maps words to frames — "
        "when the creator references CONTENT ('cut the part about X', 'zoom on the punchline'), find "
        "those words and use their frames. When they reference TIME ('at 5 seconds'), convert directly.\n"
        "Unused op params must be null. reply is your short, warm chat answer (1-2 sentences, no "
        "markdown) — confirm what you're changing, or ask ONE clarifying question (with ops=[]) when "
        "the request is genuinely ambiguous, or explain briefly when something isn't possible. If the "
        "creator asks a question about the edit, answer it from the EDIT STATE (ops=[])."
    )
    hist = ""
    if history:
        hist_lines = [f'- "{h.get("instruction", "")}" -> {h.get("summary", "")}' for h in history[-5:]]
        hist = "\nPrevious tweaks this session:\n" + "\n".join(hist_lines) + "\n"
    words_slice = [
        {"word": w.get("word", ""), "frame": ms_to_frame(w.get("start_ms", 0))}
        for w in (transcript_words or [])[:250]
    ]
    user = (
        f"EDIT STATE:\n{_edl_summary(edl)}\n"
        f"{hist}\n"
        f"Word-frame transcript: {json.dumps(words_slice) if words_slice else '(unavailable)'}\n\n"
        f"Creator says: \"{instruction}\"\n\n"
        "Return the envelope JSON."
    )
    return system, user


SIGNALS = "[stakes,authority,curiosity,patternInterrupt,specificity,contrarian,narrative,callOut]"
SIGNAL_LIST = [s.strip() for s in SIGNALS.strip("[]").split(",")]

SCRIPT_SCHEMA = (
    'Each item: {"title": str (≤6 words, a human title), "summary": str (one line), "hook": str, '
    '"hookSignal": one of ' + SIGNALS + ', "formatId": one of the allowed format ids, "body": str, '
    '"cta": str, "shotPlan": [str], "targetSeconds": int, "predictedScore": int 0-100, '
    '"altHooks": [{"text": str, "signal": str, "strength": int}], "style": str}'
)

# --- JSON Schemas for native Structured Outputs (guaranteed-valid generation) -----
# Restrictions honored: additionalProperties:false on every object, all properties
# listed in `required`, NO numeric/length constraints (0-100 is enforced in prose +
# clamped in code, not the schema). These are element schemas; array call sites wrap
# them via main._array_schema (arrays can't be a structured-output root).

_STR = {"type": "string"}
_INT = {"type": "integer"}

SCRIPT_JSON_ELEMENT = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "summary", "hook", "hookSignal", "formatId", "body", "cta",
                 "shotPlan", "targetSeconds", "predictedScore", "altHooks", "style"],
    "properties": {
        "title": _STR, "summary": _STR, "hook": _STR,
        "hookSignal": {"type": "string", "enum": SIGNAL_LIST},
        "formatId": _STR, "body": _STR, "cta": _STR,
        "shotPlan": {"type": "array", "items": _STR},
        "targetSeconds": _INT, "predictedScore": _INT,
        "altHooks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["text", "signal", "strength"],
            "properties": {"text": _STR, "signal": _STR, "strength": _INT}}},
        "style": _STR,
    },
}

HOOK_JSON_ELEMENT = {
    "type": "object", "additionalProperties": False,
    "required": ["text", "signal", "strength"],
    "properties": {"text": _STR, "signal": {"type": "string", "enum": SIGNAL_LIST},
                   "strength": _INT},
}

SCRIPT_JUDGE_JSON_ELEMENT = {
    "type": "object", "additionalProperties": False,
    "required": ["index", "hook_strength", "specificity", "format_fit", "voice_match",
                 "slop", "best_hook", "verdict", "weakest", "note"],
    "properties": {
        "index": _INT, "hook_strength": _INT, "specificity": _INT, "format_fit": _INT,
        "voice_match": _INT, "slop": {"type": "boolean"}, "best_hook": _INT,
        "verdict": {"type": "string", "enum": ["keep", "revise"]},
        "weakest": _STR, "note": _STR,
    },
}

HOOK_JUDGE_JSON_ELEMENT = {
    "type": "object", "additionalProperties": False,
    "required": ["index", "strength", "slop"],
    "properties": {"index": _INT, "strength": _INT, "slop": {"type": "boolean"}},
}


# ---------------------------------------------------------------------------
# Shared brand context
# ---------------------------------------------------------------------------

def _post_lines(posts: list[dict] | None) -> str:
    if not posts:
        return ""
    out = ["", "Their REAL recent posts (analyze these for voice, topics, and what their audience rewards):"]
    for i, p in enumerate(posts[:20], 1):
        cap = (p.get("caption") or "").strip().replace("\n", " ")
        t = (p.get("transcript") or "").strip().replace("\n", " ")
        if not cap and not t:
            continue
        tags = " ".join(p.get("hashtags", [])[:6])
        eng = f"{p.get('likes', 0)} likes / {p.get('comments', 0)} comments"
        line = f"  {i}. \"{cap[:240]}\" [{tags}] ({eng})"
        # Spoken transcript (from reel analysis) — how they actually TALK on camera.
        if t:
            line += f"\n     spoken: \"{t[:400]}\""
        out.append(line)
    return "\n".join(out)


def _voice_exemplars(posts: list[dict] | None, k: int = 4) -> str:
    """Quote the literal opening line of the creator's best-performing posts so
    the generator matches their REAL phrasing/rhythm instead of a described,
    regress-to-generic-AI version. Voice fidelity is example-bound, not float-bound."""
    if not posts:
        return ""
    ranked = sorted(posts, key=lambda p: (p.get("likes", 0) + p.get("comments", 0)), reverse=True)
    openers = []
    for p in ranked[:k]:
        text = (p.get("caption") or p.get("transcript") or "").strip().replace("\n", " ")
        first = text.split(". ")[0][:120].strip()
        if len(first) >= 8:
            openers.append(f'  "{first}"')
    if not openers:
        return ""
    return ("HOW THIS CREATOR REALLY OPENS (their highest-engagement posts — match this exact voice, "
            "diction, and rhythm; do NOT sanitize into generic copy):\n" + "\n".join(openers))


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
    if brand.get('catchphrases'):
        lines.append(
            f"- signature phrases (work these in verbatim where natural): "
            f"{', '.join(f'“{c}”' for c in brand['catchphrases'][:8])}"
        )
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
                   arm_stats: list[dict] | None = None,
                   memory: dict | None = None,
                   mandated_hooks: list[dict] | None = None,
                   emulation: list[dict] | None = None) -> tuple[str, str]:
    s = STYLES.get(style, STYLES["talking_head"])
    voice_ex = _voice_exemplars(posts)
    voice_section = f"\n\n{voice_ex}" if voice_ex else ""
    system = (
        f"You are Marque's script engine writing {s['label']} short-form videos. "
        "Write in the creator's EXACT voice — match their tone sliders, echo their real phrasing, and NEVER use "
        "a banned phrase. The hook must stop the scroll in the first 3 seconds. "
        f"\n\n{VIRALITY_BLOCK}\n\n"
        f"STYLE RULES ({s['label']}): {s['rubric']}\n\n"
        f"A correctly-structured example for this style (match the STRUCTURE, not the content):\n{s['exemplar']}"
        f"{voice_section}\n\n"
        "Reply with ONLY valid JSON, no prose, no code fences."
    )
    media = f"\nReference footage the creator already has (reuse where natural): {media_context}" if media_context else ""
    learn = learning_block(arm_stats or [])
    if not learn:                                    # cold start: no own data yet → niche baseline
        learn = niche_prior_block(brand.get("niche", ""))
    learn_section = f"\n{learn}\n" if learn else ""
    mem = memory_block(memory) if memory else ""
    mem_section = f"\n{mem}\n" if mem else ""
    emul = emulation_block(emulation or [])
    emul_section = f"\n{emul}\n" if emul else ""
    mandate = ""
    if mandated_hooks:
        picks = "\n".join(
            f'  • Script {i + 1} MUST open with: "{h.get("text", "")}"'
            + (f' (signal: {h.get("signal", "")})' if h.get("signal") else "")
            for i, h in enumerate(mandated_hooks[:count])
        )
        mandate = (
            "\nPRE-SELECTED HOOKS: a separate hook engine generated and ranked these as the strongest "
            "openers for this creator. Use each VERBATIM as that script's \"hook\" field (fix only obvious "
            "grammar), set \"hookSignal\" to match, and write the body + CTA to deliver on it:\n"
            f"{picks}\n"
        )
    user = (
        f"{learn_section}"
        f"{mem_section}"
        f"{emul_section}"
        f"{brand_block(brand, posts)}\n"
        f"Content pillar: {pillar.get('name','')} — {pillar.get('summary','')}\n"
        f"Their angle on it: {pillar.get('angle','')}\n"
        f"Example directions: {'; '.join(pillar.get('exampleTopics', []) or [])}{media}\n"
        f"Allowed formatIds for this style: {', '.join(s['formats'])}\n"
        f"{mandate}\n"
        f"Write {count} {s['label']} scripts on this pillar, each a distinct angle. Set \"style\":\"{style}\" on "
        f"each. Return ONLY a JSON array. {SCRIPT_SCHEMA}"
    )
    return system, user


# ---------------------------------------------------------------------------
# Script quality gate: generate -> judge -> targeted self-repair
# ---------------------------------------------------------------------------

# What the judge returns per script. `best_hook` indexes the pooled hook list:
# 0 = the main hook, 1..n = altHooks[0..n-1]. `verdict` is keep|revise.
SCRIPT_JUDGE_SCHEMA = (
    'Reply with ONLY a JSON array, one object per script in order: '
    '{"index": int, "hook_strength": int 0-100, "specificity": int 0-100, '
    '"format_fit": int 0-100, "voice_match": int 0-100, "slop": bool, '
    '"best_hook": int (0 = keep main hook, or the 1-based altHook that would out-hook it), '
    '"verdict": "keep" | "revise", "weakest": str (the axis to fix), "note": str (one concrete fix)}'
)


def script_judge_prompt(scripts: list[dict], style: str) -> tuple[str, str]:
    """A strict independent critic that scores each draft on the axes that
    actually drive short-form performance and flags the ones worth rewriting."""
    s = STYLES.get(style, STYLES["talking_head"])
    system = (
        "You are Marque's harshest short-form editor, grading draft scripts a JUNIOR wrote. "
        "You did not write these — be adversarial, not generous. Score each on four axes 0-100:\n"
        "- hook_strength: does the first line stop the scroll in 1.5s? A concrete claim/number mid-thought "
        "scores high; a greeting, a set-up, a question-opener, or a vague promise scores low.\n"
        "- specificity: is there at least ONE ownable, concrete detail (a number, a name, a dollar figure, a "
        "timeframe)? Generic advice that fits any creator scores low.\n"
        "- format_fit: does it obey this style's structure? "
        f"STYLE = {s['label']}: {s['rubric']}\n"
        "- voice_match: does it sound like THIS creator (their sliders, phrasing, no banned words) and not like "
        "generic AI copy?\n"
        "Set slop=true if the hook uses an AI-tell opener ('In today's video', 'Let me tell you', 'Here's the "
        "thing', 'Ever wondered', 'Picture this', 'Buckle up') or reads like filler. "
        "Then compare the main hook against the altHooks and set best_hook to the index of the strongest "
        "(0 = main hook is already best; otherwise the 1-based position in altHooks). "
        "verdict='revise' if hook_strength<70 OR specificity<65 OR format_fit<65 OR slop is true; else 'keep'. "
        "Be decisive and consistent.\n\n"
        f"{VIRALITY_BLOCK}\n\n" + SCRIPT_JUDGE_SCHEMA
    )
    items = []
    for i, sc in enumerate(scripts):
        alts = "; ".join(
            f"[{j+1}] {a.get('text','')}" for j, a in enumerate(sc.get("altHooks", []) or [])
        ) or "(none)"
        items.append(
            f"SCRIPT {i}\n"
            f"  hook (index 0): {sc.get('hook','')}\n"
            f"  altHooks: {alts}\n"
            f"  formatId: {sc.get('formatId','')}\n"
            f"  body: {sc.get('body','')}\n"
            f"  cta: {sc.get('cta','')}"
        )
    user = "Judge each draft. Return the array in the same order.\n\n" + "\n\n".join(items)
    return system, user


def script_revise_prompt(brand: dict, style: str, flagged: list[dict],
                         posts: list[dict] | None = None) -> tuple[str, str]:
    """Rewrite ONLY the scripts the judge flagged, guided by its critique.
    Keeps everything that already works; fixes the named weak axis."""
    s = STYLES.get(style, STYLES["talking_head"])
    system = (
        f"You are Marque's senior script editor rewriting weak {s['label']} drafts. A strict critic flagged "
        "each script below with its weakest axis and a fix. Rewrite each to fix EXACTLY that problem while "
        "preserving the creator's voice, the pillar, and anything already strong. Do not blandify — make it "
        "sharper and more specific, not safer. The hook must land in the first 1.5 seconds with a concrete "
        "claim; never open with a greeting, set-up, question, or AI-tell phrase.\n\n"
        f"{VIRALITY_BLOCK}\n\n"
        f"STYLE RULES ({s['label']}): {s['rubric']}\n\n"
        f"Keep \"style\":\"{style}\" and a valid formatId on each. "
        "Return ONLY a JSON array, same length and order as the input. " + SCRIPT_SCHEMA
    )
    blocks = []
    for f in flagged:
        sc = f["script"]
        v = f["verdict"]
        blocks.append(
            f"— Fix this (weakest: {v.get('weakest','hook')}; critic note: {v.get('note','')}):\n"
            f"{json.dumps(sc, ensure_ascii=False)}"
        )
    user = (
        f"{brand_block(brand, posts)}\n\n"
        "Rewrite each of these drafts:\n\n" + "\n\n".join(blocks)
    )
    return system, user


# ---------------------------------------------------------------------------
# Hooks / steer / captions / teardown / insights
# ---------------------------------------------------------------------------

def hooks_prompt(brand: dict, topic: str, style: str = "talking_head",
                 arm_stats: list[dict] | None = None,
                 memory: dict | None = None,
                 emulation: list[dict] | None = None) -> tuple[str, str]:
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
    if not learn:                                    # cold start: no own data yet → niche baseline
        learn = niche_prior_block(brand.get("niche", ""))
    extra = f"\n{learn}" if learn else ""
    mem = memory_block(memory) if memory else ""
    mem_section = f"\n{mem}\n" if mem else ""
    emul = emulation_block(emulation or [])
    emul_section = f"\n{emul}\n" if emul else ""
    user = (
        f"{brand_block(brand)}\n{mem_section}{emul_section}Topic: {topic}\n"
        f"Style: {STYLES.get(style, STYLES['talking_head'])['label']}\n{extra}"
        f"Return ONLY a JSON array of 6 hooks with diverse signals and structures. Each: "
        f'{{\"text\": str, \"signal\": one of {SIGNALS}, \"strength\": int 0-100}}'
    )
    return system, user


def hook_judge_prompt(topic: str, hooks: list[dict]) -> tuple[str, str]:
    """Re-score a batch of generated hooks with an independent critic, drop the
    AI-slop / duplicate ones, and re-rank by honest strength."""
    system = (
        "You are a ruthless short-form hook critic. You did NOT write these hooks — grade them honestly, harder "
        "than the writer did. For each, re-score strength 0-100 on ONE question: would a scrolling stranger stop "
        "in the first 1.5 seconds? Reward a concrete claim/number/contrarian reversal opened mid-thought; punish "
        "greetings, set-ups, question-openers, and vague promises. Set slop=true for AI-tell openers ('In this "
        "video', 'Let me tell you', 'Here's the thing', 'Ever wondered', 'Buckle up') or near-duplicates of a "
        "stronger hook in the set. "
        "Reply with ONLY a JSON array (same order as input): "
        '{"index": int, "strength": int 0-100, "slop": bool}.\n\n' + VIRALITY_BLOCK
    )
    items = "\n".join(f'{i}. [{h.get("signal","")}] {h.get("text","")}' for i, h in enumerate(hooks))
    user = f"Topic: {topic}\nHooks:\n{items}\n\nJudge each."
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


SCORE_SCHEMA = (
    'Reply with ONLY a JSON object, no prose, no code fences: '
    '{"hook": "High"|"Mid"|"Low", "fluff": "High"|"Mid"|"Low", '
    '"satisfaction": "High"|"Mid"|"Low", "overall": int 0-100, '
    '"strongest": str, "weakest": str, "fix": str (one concrete improvement)}'
)


def score_script_prompt(hook: str, body: str, style: str = "talking_head") -> tuple[str, str]:
    """Port of Palo's all_scores rubric: a DETERMINISTIC, independent read of a
    short-form script on Hook / Fluff / Viewer-Satisfaction, shown to the creator
    BEFORE they film. Deliberately NOT wired into the bandit reward — it judges
    content quality, not realized performance, so it can't masquerade as a metric."""
    system = (
        "You are an expert short-form (TikTok/Reels) scriptwriter scoring a script meant to be SPOKEN OUT "
        "LOUD in a talking-head video. Prioritize engaging, conversational flow and concise storytelling; "
        "visual formatting (line breaks, spacing) is irrelevant since this is read aloud.\n\n"
        "Consistency is critical: apply the criteria strictly so identical content always scores the same "
        "and small changes move the score proportionally. Scores should ENCOURAGE improvement — if there is "
        "real room to better meet a criterion, give 'Mid' rather than 'High' (within reason).\n\n"
        "HOOK — the opening lines. A strong hook is a scroll-stopper that makes the viewer curious about the "
        "story and promises a payoff worth staying for: it provokes a strong reaction (surprise, urgency, "
        "excitement), is clear and easy to follow, and sets up a compelling conclusion. Grabbing attention "
        "AND establishing the narrative payoff is the whole job; specific techniques are secondary to that.\n"
        "FLUFF — how much unnecessary filler exists (High = a lot, and worse). From a scrolling viewer's "
        "perspective, is there redundant content that kills engagement and invites a scroll-away? Fluff that "
        "adds to the bones of the narrative is fine; only distracting, unnecessary filler counts against it.\n"
        "SATISFACTION — the concluding payoff. Did the script hold attention to build to it, does the payoff "
        "meet or exceed expectations, and does the ending feel satisfying or impactful? A strong payoff is "
        "what earns the like/share.\n\n" + SCORE_SCHEMA
    )
    user = (
        f"Style: {STYLES.get(style, STYLES['talking_head'])['label']}\n"
        f"HOOK:\n{hook}\n\nBODY (spoken aloud):\n{body}\n\nScore it strictly and consistently."
    )
    return system, user


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
        "grounded in their real topics and specific enough that they recognize themselves. "
        "When posts include a spoken: transcript, weigh HOW they SPEAK above how they caption for the voice "
        "axes, and catchphrases must be verbatim phrases from the spoken transcripts where available. "
        "Reply with ONLY a JSON object."
    )
    user = f"{brand_block(brand, posts)}\n\nAnalyze the posts above and derive the brand. {DERIVE_SCHEMA}"
    return system, user


# ---------------------------------------------------------------------------
# Emulate creators — analyze a target creator's style DNA and thread it into
# script/hook generation as structural inspiration (never content to copy).
# ---------------------------------------------------------------------------

EMULATION_SCHEMA = (
    'Return ONLY a JSON object: {"top_hooks": [str] (3-5 verbatim opening lines from '
    'their strongest posts), "hook_signals": [str] (which of the 8 signal types they '
    'lean on), "top_format": str (their dominant structural pattern, one short phrase), '
    '"pacing": str (one sentence on their editing/speaking rhythm), '
    '"voice": {"funnyToSerious": 0-1, "polishedToRaw": 0-1, "teacherToPeer": 0-1}, '
    '"never_borrow": [str] (their specific claims/stories/niche-facts that must NEVER '
    'be reused — only the mechanics are transferable)}'
)


def derive_emulation_prompt(handle: str, posts: list[dict]) -> tuple[str, str]:
    """Extract a target creator's transferable style DNA from their real posts —
    hook mechanics, format, pacing, voice — with an explicit list of what must
    NOT be borrowed (their specific claims/stories), so downstream generation
    can channel the mechanics without copying content."""
    system = (
        "You are Marque's style analyst. You are given a creator's REAL recent posts. Extract what makes "
        "their content WORK structurally — their hook mechanics, format, pacing, voice — so another creator "
        "in a DIFFERENT niche could learn from the mechanics without copying the substance. "
        "Quote hooks VERBATIM. Be explicit in never_borrow about their specific claims, stories, and niche "
        "facts that are theirs alone. Reply with ONLY a JSON object."
    )
    user = f"Creator: @{handle}{_post_lines(posts)}\n\nAnalyze the posts above. {EMULATION_SCHEMA}"
    return system, user


# Hand-authored style-DNA for the onboarding presets — these must work keyless/
# offline (no scrape needed) so the emulate step never blocks on a live call.
# Keyed by the exact display name the iOS EmulateStep sends.
PRESET_EMULATION: dict[str, dict] = {
    "Alex Hormozi": {
        "top_hooks": [
            "Here's the math nobody wants to do.",
            "I made $100M and this is the only thing that mattered.",
            "Most people quit right before this works.",
        ],
        "hook_signals": ["authority", "specificity", "stakes"],
        "top_format": "proof-stacked direct response — claim, then the number that backs it, then the mechanism",
        "pacing": "flat, unhurried delivery; the confidence comes from certainty, not energy",
        "voice": {"funnyToSerious": 0.25, "polishedToRaw": 0.4, "teacherToPeer": 0.15},
        "never_borrow": ["his specific business numbers, deals, or client stories"],
    },
    "Andrew Tate": {
        "top_hooks": [
            "Nobody tells you this because it's uncomfortable.",
            "There are two types of people. You're probably the wrong one.",
            "This is why you're losing and you don't even know it.",
        ],
        "hook_signals": ["contrarian", "callOut", "stakes"],
        "top_format": "declarative frame — plant a polarizing claim, defend it fast, close with a challenge",
        "pacing": "high-intensity, short punchy sentences, minimal hedging",
        "voice": {"funnyToSerious": 0.3, "polishedToRaw": 0.7, "teacherToPeer": 0.1},
        "never_borrow": ["his specific claims, persona, or any content that isn't purely structural"],
    },
    "Shelby Sapp": {
        "top_hooks": [
            "If you're not doing this, you're leaving money on the table.",
            "I closed this deal in under five minutes. Here's exactly how.",
            "Stop overthinking the pitch — do this instead.",
        ],
        "hook_signals": ["authority", "curiosity", "patternInterrupt"],
        "top_format": "high-energy sales talk-track — hook, quick proof, one actionable script line",
        "pacing": "fast, upbeat, conversational — like coaching a friend mid-call",
        "voice": {"funnyToSerious": 0.45, "polishedToRaw": 0.55, "teacherToPeer": 0.7},
        "never_borrow": ["her specific client names, deals, or numbers"],
    },
    "MrBeast": {
        "top_hooks": [
            "I gave away $100,000 to whoever did this first.",
            "This is the last person to leave the circle wins.",
            "I spent 50 hours doing this so you don't have to.",
        ],
        "hook_signals": ["stakes", "curiosity", "specificity"],
        "top_format": "escalating-stakes challenge — open on the biggest number, keep raising it, pay off the loop",
        "pacing": "relentless, zero dead air, a new visual beat every couple seconds",
        "voice": {"funnyToSerious": 0.6, "polishedToRaw": 0.5, "teacherToPeer": 0.75},
        "never_borrow": ["his specific stunts, giveaways, dollar amounts, or contestants"],
    },
}


def emulation_block(profiles: list[dict]) -> str:
    """Render resolved emulation profiles into the prompt block generation
    threads into scripts_prompt/hooks_prompt. Explicit about borrowing
    MECHANICS only — never the target's content, claims, or niche facts."""
    if not profiles:
        return ""
    lines = ["STYLE INSPIRATION — the creator wants to channel these creators' MECHANICS "
             "(hook shapes, pacing, structure), NEVER their content, claims, or stories. "
             "Adapt everything to THIS creator's own niche and voice:"]
    for p in profiles[:3]:
        name = p.get("name", "")
        hooks = "; ".join(f'"{h}"' for h in (p.get("top_hooks") or [])[:2])
        lines.append(f"- {name}: format = {p.get('top_format', '')}; "
                    f"pacing = {p.get('pacing', '')}; example hook shapes: {hooks}")
        never = p.get("never_borrow") or []
        if never:
            lines.append(f"  NEVER borrow: {'; '.join(never)}")
    return "\n".join(lines)


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


def classify_arm_lift(lift_pct: int) -> str:
    """Palo's channel-analysis-v2 performance bands, mapped onto our lift scale.
    lift_pct is the arm's raw engagement composite vs the CREATOR'S OWN mean
    (see main._arm_lift), so the multiplier is (1 + lift_pct/100). DRIVER ≥ 1.8×
    (lift ≥ +80), ERROR ≤ 0.65× (lift ≤ -35), everything between is noise. The
    per-creator baseline is what makes these bands reachable — a fixed 0.5 baseline
    on the sigmoid reward saturated every arm and no band ever fired."""
    mult = 1.0 + lift_pct / 100.0
    if mult >= 1.8:
        return "driver"
    if mult <= 0.65:
        return "error"
    return "noise"


def learning_block(arm_stats: list[dict]) -> str:
    """The learning context injected into script/hook/converse prompts. Renders the
    creator's OWN settled-post signal with sample counts + confidence bands so the
    model can weight a confirmed winner over an early read, plus an exploration cue
    when the data is still thin."""
    if not arm_stats:
        return ""
    lines = ["CREATOR PERFORMANCE DATA (their own settled posts — weight these by confidence):"]
    any_confirmed = False
    for s in arm_stats[:5]:
        lift = s.get("lift_pct", 0)
        label = s.get("label", "")
        if not label or abs(lift) < 5:
            continue
        n = s.get("n", 0)
        conf = s.get("confidence", "early_read")
        band = classify_arm_lift(lift)
        if conf == "confirmed":
            any_confirmed = True
        tag = f" [{band}]" if band != "noise" else ""
        lines.append(f"- {label} — n={n} settled, {conf}{tag}")
    if len(lines) == 1:
        return ""
    if any_confirmed:
        lines.append("Exploit the confirmed drivers; keep exploring where the data is still an early read.")
    else:
        lines.append("These are EARLY READS (n<8) — lean toward them but keep experimenting; don't over-fit yet.")
    return "\n".join(lines)


ATTRIBUTION_SCHEMA = (
    'Reply with ONLY a JSON object, no prose: '
    '{"dimension": "hook_signal"|"style"|"format_id"|"pillar"|"none", '
    '"arm_value": str, "lift_pct": int, "band": "driver"|"error"|"noise", '
    '"confidence": "confirmed"|"early_read"|"insufficient", '
    '"verdict": str (ONE sentence, <=22 words, using ONLY the provided lift number)}'
)


def attribute_from_arms(arms: list[dict]) -> dict:
    """Deterministic keyless attribution: the strongest driver/error arm with at least
    an early read, else 'none'. Same shape attribution_prompt asks the model to emit —
    so the keyless path and the live path agree."""
    for a in sorted(arms, key=lambda x: abs(x.get("lift_pct", 0)), reverse=True):
        lift = int(a.get("lift_pct", 0))
        band = classify_arm_lift(lift)
        if band != "noise" and a.get("confidence") in ("confirmed", "early_read"):
            return {"dimension": a.get("dimension", ""), "arm_value": a.get("value", ""),
                    "lift_pct": lift, "band": band, "confidence": a.get("confidence", "early_read"),
                    "verdict": f"{a.get('label', 'This dimension')} — a {band} in your data."}
    return {"dimension": "none", "arm_value": "", "lift_pct": 0, "band": "noise",
            "confidence": "insufficient", "verdict": "Not enough settled data yet to attribute this one."}


def attribution_prompt(settled_post: dict, arms: list[dict]) -> tuple[str, str]:
    """Structured attribution for a just-settled post: name the single dimension that
    most drove the outcome, using ONLY pre-computed lift numbers. Ported from Palo's
    video-thoughts / channel-analysis-v2 number-discipline — NO math, cite the provided
    lift verbatim, lock one number, and return dimension='none' rather than invent a
    cause when nothing clears the driver/error band."""
    system = (
        "You attribute why one short-form post landed where it did, for the creator's own learning. "
        "You are given PRE-COMPUTED performance lifts per content dimension; reason ONLY from those numbers.\n"
        "HARD RULES (a single fabricated or drifted number destroys trust):\n"
        "- Use ONLY the lift numbers provided. NEVER estimate, extrapolate, combine, or do ANY arithmetic.\n"
        "- Pick exactly ONE number (the driving dimension's lift) and use it verbatim in the verdict.\n"
        "- Attribute to the SINGLE strongest signal — a 'driver' if it overperformed, an 'error' if it "
        "underperformed. If no dimension clears the driver/error band, or every arm is 'insufficient', "
        'return dimension="none". Do not manufacture a cause.\n'
        "- Never project weakness ('small sample', 'not sure'); attribute confidently from the data or none.\n\n"
        + ATTRIBUTION_SCHEMA
    )
    arm_lines = "\n".join(
        f"- {a.get('label', '')} — lift={a.get('lift_pct', 0)}, band={classify_arm_lift(a.get('lift_pct', 0))}, "
        f"n={a.get('n', 0)}, {a.get('confidence', 'insufficient')}"
        for a in arms
    ) or "- (no dimension has an early read yet)"
    user = (
        f"Post just settled with outcome score y={settled_post.get('outcome_y')}. "
        f"Its dimensions: pillar={settled_post.get('pillar', '')}, style={settled_post.get('style', '')}, "
        f"format_id={settled_post.get('format_id', '')}, hook_signal={settled_post.get('hook_signal', '')}.\n"
        f"Per-dimension performance (pre-computed — do NOT recompute):\n{arm_lines}\n\n"
        "Attribute the result to the single driving dimension, or none."
    )
    return system, user


# ---------------------------------------------------------------------------
# Cold-start niche priors — what tends to over-index in a niche BEFORE a creator
# has any performance data of their own. Rendered by niche_prior_block() ONLY when
# learning_block() is empty (no arm has an early read yet); the creator's own data
# always wins the moment it exists. Keyless-safe: pure hand-authored constants, no
# model call. `signals` are hook_signal values (see SIGNALS); `formats` are
# FORMAT_IDS; `styles` are ACTIVE_STYLES. Ported framing from Palo's cold-start
# discipline (mobile-onboarding-interaction-bouncer: "niche knowledge, not their
# catalog"; onboarding-prompt-direction-options MODE-2 generic format lanes).
# ---------------------------------------------------------------------------

NICHE_PRIORS: dict[str, dict] = {
    "fitness": {
        "signals": ["contrarian", "authority", "specificity"],
        "formats": ["myth-buster", "do-this-not-that", "before-after"],
        "styles": ["talking_head", "broll_cutaway"],
        "note": "Form-check myth-busting and before/after transformations over-index; back every claim with receipts (a 90-day log, an exact number).",
    },
    "finance": {
        "signals": ["specificity", "stakes", "contrarian"],
        "formats": ["listicle", "do-this-not-that", "myth-buster"],
        "styles": ["talking_head", "green_screen", "faceless"],
        "note": "Exact dollar figures and 'this is costing you money' stakes convert; myth-bust the money advice everyone repeats.",
    },
    "business": {
        "signals": ["contrarian", "stakes", "authority"],
        "formats": ["myth-buster", "pov-story", "listicle"],
        "styles": ["talking_head", "green_screen"],
        "note": "Contrarian takes on conventional advice plus a specific revenue/mistake number travel; a first-person POV story builds trust fast.",
    },
    "marketing": {
        "signals": ["authority", "specificity", "contrarian"],
        "formats": ["listicle", "do-this-not-that", "myth-buster"],
        "styles": ["green_screen", "talking_head"],
        "note": "Teardown listicles and receipts (real numbers, real examples) earn authority; green-screen over an example beats abstract advice.",
    },
    "food": {
        "signals": ["curiosity", "specificity", "patternInterrupt"],
        "formats": ["broll-hook", "do-this-not-that", "before-after"],
        "styles": ["faceless", "broll_cutaway"],
        "note": "Process b-roll plus one surprising technique or number dominates; faceless voiceover over cooking visuals is the default that works.",
    },
    "beauty": {
        "signals": ["contrarian", "curiosity", "authority"],
        "formats": ["myth-buster", "do-this-not-that", "before-after"],
        "styles": ["talking_head", "broll_cutaway"],
        "note": "Ingredient myth-busting and before/after reveals travel; derm/pro-authority receipts beat vibes.",
    },
    "fashion": {
        "signals": ["curiosity", "specificity", "patternInterrupt"],
        "formats": ["before-after", "listicle", "do-this-not-that"],
        "styles": ["talking_head", "split_three", "broll_cutaway"],
        "note": "Styling reveals and transformations plus 'X ways to wear it' listicles carry; a strong visual first frame is non-negotiable.",
    },
    "tech": {
        "signals": ["curiosity", "stakes", "specificity"],
        "formats": ["listicle", "do-this-not-that", "broll-hook"],
        "styles": ["green_screen", "talking_head", "faceless"],
        "note": "'This tool does X in Y seconds' curiosity plus green-screen demos win; stakes on being left behind add urgency.",
    },
    "education": {
        "signals": ["specificity", "contrarian", "authority"],
        "formats": ["do-this-not-that", "listicle", "myth-buster"],
        "styles": ["talking_head", "green_screen"],
        "note": "Study-method myth-busting and named specific techniques over-index; authority comes from receipts, not credentials.",
    },
    "mindset": {
        "signals": ["contrarian", "narrative", "stakes"],
        "formats": ["pov-story", "myth-buster", "listicle"],
        "styles": ["talking_head"],
        "note": "Contrarian reframes and a sincere personal narrative land; talking-head to camera carries the sincerity.",
    },
    "real_estate": {
        "signals": ["specificity", "stakes", "authority"],
        "formats": ["listicle", "do-this-not-that", "before-after"],
        "styles": ["talking_head", "broll_cutaway"],
        "note": "Exact numbers (price, ROI, rates) plus 'the mistake buyers make' stakes convert; walkthrough b-roll adds proof.",
    },
    "health": {
        "signals": ["contrarian", "authority", "specificity"],
        "formats": ["myth-buster", "do-this-not-that", "listicle"],
        "styles": ["talking_head", "faceless"],
        "note": "Nutrition/wellness myth-busting with study-backed authority over-indexes; one specific protocol beats generic advice.",
    },
    "parenting": {
        "signals": ["narrative", "contrarian", "curiosity"],
        "formats": ["pov-story", "do-this-not-that", "listicle"],
        "styles": ["talking_head"],
        "note": "Relatable POV moments and gentle contrarian takes on common parenting advice resonate; sincerity over polish.",
    },
    "travel": {
        "signals": ["curiosity", "specificity", "patternInterrupt"],
        "formats": ["listicle", "broll-hook", "before-after"],
        "styles": ["broll_cutaway", "faceless"],
        "note": "Destination b-roll hooks plus '$X for Y days' specificity carry; open on the most striking visual, not a greeting.",
    },
    "comedy": {
        "signals": ["patternInterrupt", "narrative", "curiosity"],
        "formats": ["pov-story", "broll-hook"],
        "styles": ["talking_head", "split_three", "duet_split"],
        "note": "Pattern-interrupt cold opens and relatable POV skits travel; the first frame has to break the scroll's expectation.",
    },
    "career": {
        "signals": ["contrarian", "stakes", "specificity"],
        "formats": ["do-this-not-that", "listicle", "myth-buster"],
        "styles": ["talking_head", "green_screen"],
        "note": "Contrarian career advice plus salary/number specifics and 'this is quietly killing your promotion' stakes convert.",
    },
    "creator": {
        "signals": ["authority", "specificity", "contrarian"],
        "formats": ["listicle", "do-this-not-that", "myth-buster"],
        "styles": ["talking_head", "green_screen"],
        "note": "Growth receipts (real view/follower numbers) and algorithm myth-busting over-index; show the data on screen.",
    },
    "default": {
        "signals": ["contrarian", "specificity", "curiosity"],
        "formats": ["myth-buster", "listicle", "do-this-not-that"],
        "styles": ["talking_head"],
        "note": "Open mid-thought on a specific, contrarian claim; talking-head straight to camera is the safest default that works.",
    },
}

# (keyword substrings -> canonical niche slug). First match wins, so order the more
# specific entries before the generic ones. Freeform onboarding niche text is matched
# case-insensitively against these.
_NICHE_ALIASES: list[tuple[tuple[str, ...], str]] = [
    (("fitness", "gym", "workout", "lifting", "bodybuild", "personal train", "calisthenic", "crossfit", "run"), "fitness"),
    (("finance", "money", "invest", "stock", "wealth", "budget", "personal finance", "fire ", "crypto", "trading"), "finance"),
    (("business", "entrepreneur", "startup", "founder", "ecommerce", "e-commerce", "dropship", "saas", "small business"), "business"),
    (("marketing", "agency", "social media", "copywrit", "seo", "paid ads", "growth marketing", "branding"), "marketing"),
    (("food", "cook", "recipe", "baking", "chef", "kitchen", "meal prep", "barista"), "food"),
    (("beauty", "skincare", "makeup", "cosmetic", "esthet", "derm", "hair", "nails"), "beauty"),
    (("fashion", "style", "outfit", "streetwear", "thrift", "wardrobe"), "fashion"),
    (("tech", "ai ", " ai", "artificial intel", "software", "coding", "developer", "programming", "gadget", "no-code", "cybersec"), "tech"),
    (("study", "student", "education", "teacher", "exam", "language learning", "academ", "college", "medical school"), "education"),
    (("mindset", "self-improve", "self improvement", "motivation", "discipline", "productivity", "stoic", "spiritual", "manifest"), "mindset"),
    (("real estate", "realtor", "property", "mortgage", "airbnb", "landlord"), "real_estate"),
    (("health", "wellness", "nutrition", "diet", "gut ", "hormone", "sleep", "biohack", "therapist", "mental health"), "health"),
    (("parent", "mom", "dad", "toddler", "newborn", "family", "motherhood"), "parenting"),
    (("travel", "digital nomad", "backpack", "destination", "van life"), "travel"),
    (("comedy", "skit", "entertain", "funny", "prank", "meme"), "comedy"),
    (("career", "corporate", "9-5", "9 to 5", "resume", "job interview", "salary", "consulting"), "career"),
    (("creator", "content creation", "influencer", "youtube", "podcast", "streamer"), "creator"),
]


def match_niche(niche: str) -> str:
    """Map freeform niche text to a canonical NICHE_PRIORS slug ('default' if none)."""
    n = (niche or "").strip().lower()
    if not n:
        return "default"
    for keys, slug in _NICHE_ALIASES:
        if any(k in n for k in keys):
            return slug
    return "default"


def niche_priors_for(niche: str) -> dict:
    """The prior dict for a niche (always returns something; falls back to default)."""
    return NICHE_PRIORS.get(match_niche(niche), NICHE_PRIORS["default"])


def niche_prior_block(niche: str) -> str:
    """Cold-start baseline injected ONLY when the creator has no performance data
    yet (learning_block empty). Framed as niche priors to lean on until their own
    data lands — never as facts about THIS creator."""
    p = niche_priors_for(niche)
    slug = match_niche(niche)
    if slug == "default":
        head = "NICHE BASELINE (no performance data yet — general short-form priors until your own data lands):"
    else:
        head = (f"NICHE BASELINE ({slug.replace('_', ' ')} — what tends to over-index here, "
                "until your own data lands):")
    sig = ", ".join(p["signals"])
    fmt = ", ".join(p["formats"])
    sty = ", ".join(s.replace("_", " ") for s in p["styles"])
    return (
        f"{head}\n"
        f"- hooks that tend to work: {sig}\n"
        f"- formats that tend to travel: {fmt}\n"
        f"- styles worth defaulting to: {sty}\n"
        f"- why: {p['note']}\n"
        "Treat these as a starting bias, not a rule — override the moment this creator's own data disagrees."
    )


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
    '"intent_args_json": str (a JSON-ENCODED object of the intent\'s args per the intent rules; "{}" when none), '
    '"chips": [str] (2-3 short suggested next messages, ≤6 words each)}'
)

# Structured-output schema for the converse envelope. intent_args is carried as a
# JSON string (intent_args_json) because its shape varies by intent — a free-form
# object can't satisfy the additionalProperties:false requirement. Parsed server-side.
CONVERSE_ENVELOPE_JSON_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["reply", "memory_updates", "intent", "intent_args_json", "chips"],
    "properties": {
        "reply": {"type": "string"},
        "memory_updates": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["op", "field", "value"],
            "properties": {
                "op": {"type": "string", "enum": ["add", "remove", "set"]},
                "field": {"type": "string", "enum": ["facts", "perspective", "ideas", "preferences", "angle"]},
                "value": {"type": "string"}}}},
        "intent": {"type": "string",
                   "enum": ["none", "generate_scripts", "day_plan", "save_idea", "update_brand_angle"]},
        "intent_args_json": {"type": "string"},
        "chips": {"type": "array", "items": {"type": "string"}},
    },
}

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
    '"intent": "update_brand_angle", "intent_args_json": "{}", '
    '"chips": ["Write the anabolic window script", "What else should I debunk?", "Build my day"]}'
)


_PERSONA_VOICES = {
    "machine": (
        "PERSONA — The Machine: huge, generous, over-the-top hype energy. You talk in big numbers and records "
        "('that's a 10x hook', 'this could be your biggest week'), you get genuinely excited about their wins, "
        "and you push them to go bigger, faster, more ambitious. Enthusiastic, never mean."
    ),
    "closer": (
        "PERSONA — The Closer: blunt, no-fluff business operator. You talk ROI, conversion, and what actually "
        "moves the number. You cut straight to the action item, skip the pleasantries, and call out anything "
        "that's a waste of their time. Respectful but zero patience for excuses dressed up as strategy."
    ),
    "sergeant": (
        "PERSONA — The Sergeant: disciplined, tough-love, no-excuses mental-toughness coach. You hold them to "
        "the standard they set for themselves, call out when they're stalling, and frame consistency as a "
        "matter of self-respect. Direct and demanding, but never demeaning — the goal is to build them up."
    ),
}

_LENGTH_STYLES = {
    "concise": "Keep it to ONE short sentence. No exceptions.",
    "medium": "Two or three sentences — enough to be useful, no more.",
    "detailed": "Go deeper: several sentences with specifics, and end by offering to go further.",
}


def converse_system(mode: str = "chat", persona: str = "closer", response_length: str = "medium") -> str:
    """System prompt for /v1/converse. mode: voice | chat. persona: closer | machine | sergeant."""
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
    persona_block = _PERSONA_VOICES.get(persona, _PERSONA_VOICES["closer"])
    length_block = _LENGTH_STYLES.get(response_length, _LENGTH_STYLES["medium"])
    return (
        "You are Marque — a personal content strategist who KNOWS this creator and talks with them every day. "
        "You are an elite short-form expert (hooks, retention, platform mechanics) AND their thinking partner: "
        "they share morning thoughts, perspective shifts, brand-angle changes, and raw ideas; you sharpen them "
        "and remember everything.\n\n"
        f"{persona_block}\n\n"
        f"RESPONSE LENGTH: {length_block}\n\n"
        f"{VIRALITY_BLOCK}\n\n"
        f"CONVERSATION STYLE: {style}\n\n"
        "MEMORY RULES: You maintain a persistent memory of this creator. After every exchange, emit memory_updates "
        "for anything durable they revealed: stable facts about them/their life ('facts'), how they see the world "
        "or their positioning ('perspective'), content ideas worth keeping ('ideas'), workflow/format preferences "
        "('preferences'), and 'angle' (op=set) when their brand direction shifts. Write each value as one crisp "
        "self-contained sentence. Do NOT store small talk, questions, or anything transient. 0–3 updates per turn "
        "is normal; empty list is fine.\n\n"
        "INTENT RULES: Set intent when the creator asks for one of these, else \"none\". intent_args_json is a "
        "JSON-ENCODED STRING of the object described (e.g. \"{\\\"topic\\\": \\\"...\\\", \\\"count\\\": 1}\"); use \"{}\" when empty.\n"
        "- generate_scripts: they want a script/scripts written now. args: {\"topic\": str, "
        "\"style\": one of [talking_head, green_screen, broll_cutaway, split_three, duet_split, faceless] or \"\", \"count\": 1-3}. "
        "Your reply should tee up the scripts conversationally (they are generated and attached automatically).\n"
        "- day_plan: they want their day/content day built out. args: {\"plan\": {\"blocks\": "
        "[{\"time\": str (e.g. \"9:00\"), \"action\": str (≤6 words), \"detail\": str (one sentence)}]}} — "
        "build a realistic filming/posting day from their weekly target, blockers, and active ideas (4-6 blocks).\n"
        "- save_idea: they shared an idea to remember (also add it to memory ideas). args: {}.\n"
        "- update_brand_angle: their brand direction/angle shifted (also set memory angle). args: {}.\n\n"
        f"OUTPUT: Reply with ONLY a valid JSON object matching exactly: {CONVERSE_ENVELOPE_SCHEMA}\n"
        "No prose outside the JSON, no code fences.\n\n"
        f"Worked example:\n{CONVERSE_ENVELOPE_EXEMPLAR}"
    )


# ---------------------------------------------------------------------------
# Mimic — rewrite an influencer reel as THIS creator (skeleton stays, substance swaps)
# ---------------------------------------------------------------------------

def mimic_prompt(reel: dict, brand: dict, memory: dict | None = None) -> tuple[str, str]:
    system = (
        "You are Marque's mimic engine. You take a proven viral reel and rewrite it AS a different creator — "
        "keeping the STRUCTURAL SKELETON that made it work (hook shape, beat order, pacing, loop structure, "
        "where the payoff lands) while swapping ALL substance for this creator's niche, facts, and voice.\n\n"
        f"{VIRALITY_BLOCK}\n\n"
        "HARD RULES:\n"
        "- NO plagiarism: never reuse the original's sentences, examples, numbers, or catchphrases. If the "
        "original said 'I tested 5 diets for 30 days', the mimic might be 'I ran 5 cold-outreach scripts for "
        "2 weeks' — same skeleton, entirely different substance.\n"
        "- The creator's voice sliders, catchphrases, and banned words are law.\n"
        "- Match the original's energy and length, not its topic.\n"
        "- Set style/formatId appropriate to how THIS creator films.\n\n"
        "Worked example:\n"
        "Original (fitness reel): hook 'I ate 200g of protein every day for 30 days — my bloodwork shocked my "
        "doctor', beats: bold claim → daily proof montage → surprising result → one takeaway.\n"
        "Mimic for a personal-finance creator: hook 'I tracked every dollar for 30 days — the leak wasn't "
        "where I thought', beats: bold claim → daily tracking montage → surprising category reveal → one rule "
        "to copy. Same skeleton; zero shared substance.\n\n"
        "Reply with ONLY one JSON object, no prose."
    )
    mem = memory_block(memory) if memory else ""
    user = (
        f"{brand_block(brand)}\n{mem}\n\n"
        "ORIGINAL REEL TO MIMIC:\n"
        f"- creator: @{reel.get('creator_handle','unknown')} ({reel.get('platform','tiktok')})\n"
        f"- title: {reel.get('title','')}\n"
        f"- hook: \"{reel.get('hook_text','')}\"\n"
        f"- transcript: {reel.get('transcript','')}\n"
        f"- why it's working: {reel.get('why_trending','')}\n"
        f"- stats: {reel.get('views',0)} views, {reel.get('likes',0)} likes\n\n"
        f"Rewrite this AS the creator above, in their niche and voice. Return ONLY one JSON object. {SCRIPT_SCHEMA}"
    )
    return system, user


# ---------------------------------------------------------------------------
# Video-link analysis — pasted URL → what makes it work → your version
# ---------------------------------------------------------------------------

def analyze_video_prompt(url: str, transcript: str, brand: dict, memory: dict | None = None) -> tuple[str, str]:
    system = (
        "You are Marque's video analyst. Given a short-form video's transcript, produce a tight teardown of "
        "why it works and a version rewritten for a specific creator.\n\n"
        f"{VIRALITY_BLOCK}\n\n"
        "Reply with ONLY valid JSON matching:\n"
        '{"hook_analysis": str (1-2 sentences on the hook mechanic and why it stops the scroll), '
        '"structure_beats": [str] (3-6 beats naming the structural moves in order), '
        '"why_it_works": str (2-3 sentences: retention mechanics, specificity, emotional driver), '
        '"suggestions": [str] (2-3 concrete ways this creator could use or improve on the pattern), '
        f'"your_version": {SCRIPT_SCHEMA.replace("Each item: ", "")}}}\n'
        "your_version follows the same no-plagiarism rule as a mimic: keep the skeleton, swap ALL substance "
        "for this creator's niche and voice."
    )
    mem = memory_block(memory) if memory else ""
    user = (
        f"{brand_block(brand)}\n{mem}\n\n"
        f"VIDEO: {url}\n"
        f"TRANSCRIPT:\n{transcript[:4000]}\n\n"
        "Analyze it and write this creator's version. JSON only."
    )
    return system, user


# ---------------------------------------------------------------------------
# Brand summary — "what Marque knows about you" (Profile hero card)
# ---------------------------------------------------------------------------

def brand_summary_prompt(brand: dict, memory: dict | None = None,
                         arm_stats: list[dict] | None = None) -> tuple[str, str]:
    system = (
        "You write the 'What Marque knows about you' card on a creator's profile — a mirror that makes them "
        "feel SEEN. Editorial, warm, specific; second person ('you'). Never generic, never flattering fluff: "
        "every sentence should be traceable to something real about them.\n\n"
        "Reply with ONLY valid JSON:\n"
        '{"summary": str (one tight paragraph, 3-4 sentences: who they are, who they serve, what makes their '
        "take different, and where their content is headed), "
        '"traits": [str] (3-5 short chips, ≤4 words each, e.g. "contrarian teacher", "receipts over hype"), '
        '"working_on": str (one sentence on their current angle/direction, from memory if present)}'
    )
    mem = memory_block(memory) if memory else ""
    learn = learning_block(arm_stats or [])
    user = f"{brand_block(brand)}\n{mem}\n{learn}\n\nWrite the profile card. JSON only."
    return system, user


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
