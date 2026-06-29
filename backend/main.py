"""Marque backend — the server-side AI/orchestration proxy.

Production: holds every vendor key (Anthropic, AssemblyAI, Shotstack, Ayrshare, Supabase
service_role) so the iOS app ships none. This skeleton implements the health checks and the
script-generation proxy with a mock fallback so it runs with zero keys.
"""
import os
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Marque API", version="0.1.0")

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages"


class ScriptRequest(BaseModel):
    niche: str = ""
    audience: str = ""
    known_for: str = ""
    pillar: str = "Lessons"
    count: int = 3


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ready", "ai": "live" if ANTHROPIC_KEY else "mock"}


@app.post("/v1/scripts")
async def scripts(req: ScriptRequest):
    """Generate scripts. Proxies Anthropic when ANTHROPIC_API_KEY is set; mocks otherwise."""
    if not ANTHROPIC_KEY:
        return {
            "mode": "mock",
            "scripts": [
                {"hook": f"Stop overthinking {req.pillar.lower()}. Here's what works.",
                 "format_id": "myth-buster", "predicted_score": 82}
                for _ in range(max(1, req.count))
            ],
        }

    system = ("You are Marque's script engine. Write short-form video scripts in the creator's "
              "voice. Reply ONLY with a JSON array.")
    user = (f"Brand: niche={req.niche}; audience={req.audience}; known for={req.known_for}. "
            f"Pillar={req.pillar}. Write {req.count} scripts as a JSON array.")
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-opus-4-8",
                "max_tokens": 2000,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="upstream error")
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    return {"mode": "live", "raw": text}
