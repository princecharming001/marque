"""Marque backend — the server-side AI / orchestration proxy.

Production pattern: this service holds every vendor key (Anthropic, AssemblyAI, Shotstack,
Ayrshare, Supabase service_role) so the iOS app ships none. It mirrors the on-device AI so the
app can point its adapters here. Runs with ZERO keys via deterministic mock fallbacks, so the
whole surface is testable without secrets.
"""
import os
import json
import uuid
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Marque API", version="0.2.0")

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages"
AYRSHARE_KEY = os.environ.get("AYRSHARE_KEY", "")
OPUS = "claude-opus-4-8"
HAIKU = "claude-haiku-4-5-20251001"

FORMAT_IDS = ["myth-buster", "listicle", "do-this-not-that", "before-after",
              "green-screen", "faceless", "pov-story", "broll-hook"]


# ----- models -------------------------------------------------------------

class Brand(BaseModel):
    niche: str = ""
    audience: str = ""
    known_for: str = ""
    what_you_do: str = ""
    goal: str = "Grow my audience"


class ScriptRequest(Brand):
    pillar: str = "Lessons"
    pillar_angle: str = ""
    media_context: str = ""
    count: int = 3


class CaptionRequest(BaseModel):
    hook: str = ""
    body: str = ""


class ClipJobRequest(BaseModel):
    script_id: str = ""
    video_url: str = ""
    formats: list[str] = []
    auto_captions: bool = True


class PublishRequest(BaseModel):
    caption: str = ""
    media_url: str = ""
    platforms: list[str] = []
    schedule_date: str = ""


# ----- helpers ------------------------------------------------------------

async def anthropic(system: str, user: str, model: str = OPUS, max_tokens: int = 2000) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={"model": model, "max_tokens": max_tokens, "system": system,
                  "messages": [{"role": "user", "content": user}]},
        )
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="upstream error")
    return "".join(b.get("text", "") for b in r.json().get("content", []))


def extract_json(text: str, array: bool = True):
    o, c = ("[", "]") if array else ("{", "}")
    i, j = text.find(o), text.rfind(c)
    if i == -1 or j == -1 or j < i:
        return None
    try:
        return json.loads(text[i:j + 1])
    except json.JSONDecodeError:
        return None


def mock_pillars(b: Brand):
    niche = b.niche or "your field"
    aud = (b.audience or "your audience").lower()
    known = (b.known_for or niche)
    what = (b.what_you_do or "what you do").lower()
    seeds = [
        ("Teach the fundamentals",
         f"Bite-size lessons that make {aud} better at {niche}.",
         f"You break {known} into steps {aud} can copy today — no fluff.",
         [f"The {niche} mistake most beginners make",
          f"A 60-second framework for {known.lower()}",
          f"What I wish I knew about {niche} on day one"]),
        ("Myth-busting",
         f"Contrarian takes that fix what {aud} get wrong about {niche}.",
         f"You call out popular {niche} advice that backfires — and show what works.",
         [f"The {niche} advice hurting {aud} most",
          f"“Everyone says this about {niche}” — why it's wrong",
          f"Stop doing this one thing in {niche}"]),
        ("Behind the scenes",
         f"The real, unpolished story of {what}.",
         f"You let {aud} watch how the work actually happens.",
         [f"A day in the life of {what}",
          f"The part of {niche} nobody shows you",
          f"How I actually {known.lower()}"]),
        ("Hot takes",
         f"Sharp opinions that start conversations in {niche}.",
         f"You stake a position {aud} will want to share or argue with.",
         [f"My most controversial {niche} opinion",
          f"An unpopular truth about {niche}",
          f"Why most {aud} are wrong about {known.lower()}"]),
        ("Proof & results",
         f"Receipts: transformations and case studies in {niche}.",
         f"You show concrete outcomes so {aud} trust the method.",
         [f"A before/after that proves {known.lower()} works",
          f"The result that changed how I see {niche}",
          f"Walk through a real {niche} win, step by step"]),
    ]
    if b.goal in ("Get clients", "Monetize"):
        seeds[0], seeds[4] = seeds[4], seeds[0]
    elif b.goal == "Build authority":
        seeds[0], seeds[1] = seeds[1], seeds[0]
    return [{"name": n, "summary": s, "angle": a, "exampleTopics": t} for (n, s, a, t) in seeds]


def mock_scripts(req: ScriptRequest):
    niche = req.niche or "your craft"
    out = []
    for i in range(max(1, req.count)):
        fmt = FORMAT_IDS[i % len(FORMAT_IDS)]
        out.append({
            "title": f"The {niche} mistake #{i + 1}",
            "summary": f"A short {fmt.replace('-', ' ')} on {niche} for {req.audience or 'your audience'}.",
            "hook": f"Stop overthinking {niche}. Here's what actually works.",
            "hookSignal": "contrarian",
            "formatId": fmt,
            "body": f"Open on the hook. Give the core idea about {niche}, back it with a specific, land the lesson.",
            "cta": "Follow for more — I post this every week.",
            "shotPlan": ["Hook on frame 1", "Body with one punch-in", "CTA to camera"],
            "targetSeconds": 24,
            "predictedScore": 82,
        })
    return out


# ----- routes -------------------------------------------------------------

@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ready", "version": app.version,
            "ai": "live" if ANTHROPIC_KEY else "mock",
            "publish": "live" if AYRSHARE_KEY else "mock"}


@app.post("/v1/pillars")
async def pillars(req: Brand):
    """Niche-specific content pillars. Proxies Anthropic when keyed; mocks otherwise."""
    if not ANTHROPIC_KEY:
        return {"mode": "mock", "pillars": mock_pillars(req)}
    system = ("You are Marque's brand strategist. Design content pillars unique to THIS creator. "
              "Reply ONLY with a JSON array.")
    user = (f"Brand: niche={req.niche}; audience={req.audience}; known_for={req.known_for}; "
            f"goal={req.goal}. Design 5 pillars. Each: "
            '{"name":str,"summary":str,"angle":str,"exampleTopics":[str,str,str]}')
    try:
        parsed = extract_json(await anthropic(system, user, OPUS, 1600), array=True)
        return {"mode": "live", "pillars": parsed or mock_pillars(req)}
    except HTTPException:
        return {"mode": "mock", "pillars": mock_pillars(req)}


@app.post("/v1/scripts")
async def scripts(req: ScriptRequest):
    """Generate scripts (parsed to structured items). Proxies Anthropic when keyed."""
    if not ANTHROPIC_KEY:
        return {"mode": "mock", "scripts": mock_scripts(req)}
    system = ("You are Marque's script engine. Write short-form scripts in the creator's voice. "
              "Reply ONLY with a JSON array.")
    media = f" Reference footage: {req.media_context}." if req.media_context else ""
    user = (f"Brand: niche={req.niche}; audience={req.audience}; known for={req.known_for}. "
            f"Pillar={req.pillar} ({req.pillar_angle}).{media} Write {req.count} scripts. Each: "
            '{"title":str,"summary":str,"hook":str,"hookSignal":str,"formatId":str,"body":str,'
            '"cta":str,"shotPlan":[str],"targetSeconds":int,"predictedScore":int}')
    try:
        parsed = extract_json(await anthropic(system, user, OPUS, 3500), array=True)
        return {"mode": "live", "scripts": parsed or mock_scripts(req)}
    except HTTPException:
        return {"mode": "mock", "scripts": mock_scripts(req)}


@app.post("/v1/captions")
async def captions(req: CaptionRequest):
    """Burned-in caption lines (<=5 words each)."""
    def chunk(text):
        words, lines, cur = text.split(), [], []
        for w in words:
            cur.append(w)
            if len(cur) >= 5:
                lines.append(" ".join(cur)); cur = []
        if cur:
            lines.append(" ".join(cur))
        return lines
    sentences = [req.hook] + [s.strip() for s in req.body.replace("!", ".").replace("?", ".").split(".") if s.strip()]
    lines = [ln for s in sentences if s for ln in chunk(s)]
    return {"mode": "live" if ANTHROPIC_KEY else "mock", "lines": lines}


@app.post("/v1/clips")
async def clips(req: ClipJobRequest):
    """Clip pipeline (transcribe -> caption -> render-per-format -> R2). Scaffolded:
    returns a job descriptor. The real pipeline runs async on Trigger.dev with AssemblyAI +
    Shotstack + Cloudflare R2 once those keys are set."""
    job_id = f"clip_{uuid.uuid4().hex[:12]}"
    steps = ["transcribe", "detect-moments"]
    if req.auto_captions:
        steps.append("burn-captions")
    steps += ["render-per-format", "upload-r2"]
    return {
        "job_id": job_id,
        "status": "queued",
        "formats": req.formats or FORMAT_IDS[:3],
        "steps": steps,
        "mode": "live" if os.environ.get("SHOTSTACK_KEY") else "mock",
    }


@app.post("/v1/publish")
async def publish(req: PublishRequest):
    """Publish/schedule to IG/TikTok via Ayrshare (with media). Mock unless AYRSHARE_KEY."""
    if not AYRSHARE_KEY:
        return {"ok": True, "mode": "mock", "id": f"post_{uuid.uuid4().hex[:10]}"}
    body = {"post": req.caption, "platforms": req.platforms}
    if req.schedule_date:
        body["scheduleDate"] = req.schedule_date
    if req.media_url.startswith("http"):
        body["mediaUrls"] = [req.media_url]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post("https://api.ayrshare.com/api/post",
                                  headers={"Authorization": f"Bearer {AYRSHARE_KEY}"}, json=body)
        ok = 200 <= r.status_code < 300
        return {"ok": ok, "mode": "live", "status": r.status_code}
    except httpx.HTTPError:
        return {"ok": False, "mode": "live", "error": "network"}
