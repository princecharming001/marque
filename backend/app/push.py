"""APNs push adapter (UX-B2a) — token-based (.p8 / ES256) HTTP/2 provider API.

Fail-soft doctrine: `PUSH_CONFIGURED` is False unless ALL of APNS_KEY_ID / APNS_TEAM_ID /
APNS_P8 / APNS_TOPIC are set — every public function is then a clean no-op, so keyless
dev/CI never blocks. Device tokens persist in the `device_tokens` table when Supabase is
wired (main.py injects the client via the module-level `SUPABASE`), with an in-memory
fallback keyed (token, environment) so registration + dedup are fully testable keyless.

Transport seams (`_post_apns`, `_client`) are monkeypatchable — unit tests exercise JWT
claims with a throwaway ES256 key and 410 soft-disable with a canned transport.
"""
from __future__ import annotations

import json
import logging
import os
import time

log = logging.getLogger("push")

APNS_KEY_ID = os.environ.get("APNS_KEY_ID", "")
APNS_TEAM_ID = os.environ.get("APNS_TEAM_ID", "")
APNS_P8 = os.environ.get("APNS_P8", "")            # the .p8 key's PEM contents
APNS_TOPIC = os.environ.get("APNS_TOPIC", "")      # the app bundle id
PUSH_CONFIGURED = bool(APNS_KEY_ID and APNS_TEAM_ID and APNS_P8 and APNS_TOPIC)

_HOSTS = {"sandbox": "https://api.sandbox.push.apple.com",
          "prod": "https://api.push.apple.com"}

# Injected by main.py after the Supabase client is constructed; None → memory only.
SUPABASE = None

# In-memory registry fallback: (token, environment) → row dict.
_mem_tokens: dict[tuple[str, str], dict] = {}


# ---------------------------------------------------------------------------
# Provider JWT — ES256, cached ~40 min (Apple accepts 20–60 min old tokens)
# ---------------------------------------------------------------------------

_jwt_cache: dict = {"token": None, "issued_at": 0.0}
_JWT_TTL_S = 40 * 60


def _provider_jwt(now: float | None = None) -> str | None:
    """Signed provider token, reusing the cached one while it's fresh."""
    if not PUSH_CONFIGURED:
        return None
    now = now if now is not None else time.time()
    if _jwt_cache["token"] and now - _jwt_cache["issued_at"] < _JWT_TTL_S:
        return _jwt_cache["token"]
    try:
        import jwt as pyjwt
        token = pyjwt.encode({"iss": APNS_TEAM_ID, "iat": int(now)}, APNS_P8,
                             algorithm="ES256", headers={"kid": APNS_KEY_ID})
        _jwt_cache.update(token=token, issued_at=now)
        return token
    except Exception as e:                       # bad key / missing cryptography
        log.warning("apns: provider JWT signing failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Token registry (Supabase when wired, memory fallback)
# ---------------------------------------------------------------------------

async def upsert_device(creator_id: str, token: str, environment: str = "sandbox",
                        platform: str = "ios", app_version: str = "",
                        timezone: str = "", permission: str = "") -> dict:
    """Idempotent (token, environment) upsert; re-registering re-enables a token."""
    environment = environment if environment in ("sandbox", "prod") else "sandbox"
    row = {"creator_id": creator_id or "default", "token": token,
           "environment": environment, "platform": platform,
           "app_version": app_version, "timezone": timezone,
           "permission": permission, "last_seen_at": time.time(), "disabled_at": None}
    _mem_tokens[(token, environment)] = row
    if SUPABASE:
        try:
            await SUPABASE.upsert_device_token(row)
        except Exception as e:
            log.warning("apns: device upsert persist failed: %s", e)
    return row


async def tokens_for(creator_id: str) -> list[dict]:
    """Enabled tokens for a creator (Supabase first, memory fallback/merge)."""
    rows: list[dict] = []
    if SUPABASE:
        try:
            rows = await SUPABASE.load_device_tokens(creator_id) or []
        except Exception as e:
            log.warning("apns: device load failed: %s", e)
    if not rows:
        rows = [r for r in _mem_tokens.values() if r["creator_id"] == creator_id]
    return [r for r in rows if not r.get("disabled_at")]


async def _disable(token: str, environment: str) -> None:
    """410 Unregistered / 400 BadDeviceToken → soft-disable (never hard-delete)."""
    key = (token, environment)
    if key in _mem_tokens:
        _mem_tokens[key]["disabled_at"] = time.time()
    if SUPABASE:
        try:
            await SUPABASE.disable_device_token(token, environment)
        except Exception as e:
            log.warning("apns: device disable persist failed: %s", e)


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

_http_client = None


def _client():
    """Lazy shared HTTP/2 client (APNs requires HTTP/2)."""
    global _http_client
    if _http_client is None:
        import httpx
        _http_client = httpx.AsyncClient(http2=True, timeout=10)
    return _http_client


async def _post_apns(host: str, token: str, payload: dict, jwt_token: str) -> tuple[int, str]:
    """The single APNs transport boundary (monkeypatched in tests)."""
    r = await _client().post(
        f"{host}/3/device/{token}",
        headers={"authorization": f"bearer {jwt_token}", "apns-topic": APNS_TOPIC,
                 "apns-push-type": "alert", "apns-priority": "10"},
        json=payload)
    return r.status_code, r.text


async def send_clips_ready(creator_id: str, clip_id: str, count: int = 1) -> int:
    """'Your clip is ready' to every enabled device of a creator. Returns sends
    attempted-and-accepted. Keyless / no tokens / any error → 0, never raises."""
    if not PUSH_CONFIGURED or not creator_id:
        return 0
    jwt_token = _provider_jwt()
    if not jwt_token:
        return 0
    body = "Tap to watch and share it." if count <= 1 else f"{count} clips are ready to watch."
    payload = {
        "aps": {"alert": {"title": "Your clip is ready", "body": body},
                "sound": "default", "thread-id": "clips_ready", "category": "clips_ready"},
        "deeplink": f"marque://library/clip/{clip_id}",
        "clip_id": clip_id,
    }
    sent = 0
    for row in await tokens_for(creator_id):
        host = _HOSTS.get(row.get("environment", "sandbox"), _HOSTS["sandbox"])
        try:
            status, text = await _post_apns(host, row["token"], payload, jwt_token)
        except Exception as e:
            log.warning("apns: send failed (%s)", e)
            continue
        if status == 200:
            sent += 1
        elif status == 410 or (status == 400 and "BadDeviceToken" in text):
            await _disable(row["token"], row.get("environment", "sandbox"))
        else:
            log.warning("apns: %s → %s %s", row["token"][:8], status, text[:120])
    return sent


async def send_insight(creator_id: str, title: str, body: str, insight_id: str = "",
                       seed: dict | None = None) -> int:
    """Palo port: a proactive insight push. The deeplink + seed open the chat pre-seeded
    from the tapped insight (the insight→converse bridge). Keyless / no tokens → 0."""
    if not PUSH_CONFIGURED or not creator_id:
        return 0
    jwt_token = _provider_jwt()
    if not jwt_token:
        return 0
    payload = {
        "aps": {"alert": {"title": title[:120], "body": body[:180]},
                "sound": "default", "thread-id": "insights", "category": "insight"},
        "deeplink": f"marque://chat?insight={insight_id}",
        "insight_id": insight_id, "seed": seed or {},
    }
    sent = 0
    for row in await tokens_for(creator_id):
        host = _HOSTS.get(row.get("environment", "sandbox"), _HOSTS["sandbox"])
        try:
            status, text = await _post_apns(host, row["token"], payload, jwt_token)
        except Exception as e:
            log.warning("apns insight: send failed (%s)", e)
            continue
        if status == 200:
            sent += 1
        elif status == 410 or (status == 400 and "BadDeviceToken" in text):
            await _disable(row["token"], row.get("environment", "sandbox"))
    return sent
