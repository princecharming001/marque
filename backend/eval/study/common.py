"""Shared vocabulary for the study-the-winners harness: config, paths, manifest
I/O, per-reel stage state. See eval/study/README in the plan file — the corpus
stores permalinks + metrics + measurements ONLY (the broll_cadence_probe
analysis-only boundary: footage lives in a per-reel tempdir and is deleted in a
`finally`; nothing under data/ contains pixels)."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

STUDY_DIR = Path(__file__).parent
DATA_DIR = STUDY_DIR / "data"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
OCR_DIR = DATA_DIR / "ocr"
ANATOMY_DIR = DATA_DIR / "anatomy"
OUT_DIR = DATA_DIR / "out"
MANIFEST_PATH = DATA_DIR / "corpus_manifest.json"
STATE_PATH = DATA_DIR / "state.json"

SCHEMA_VERSION = 1

# Aggregate floors (verify.py enforces): below these a finding is DIRECTIONAL,
# never a headline number.
CORPUS_FLOOR = 30
PER_NICHE_FLOOR = 4


@dataclass
class StudyConfig:
    niches: list[str] = field(default_factory=lambda: [
        "fitness coaching", "personal finance", "business growth",
        "nutrition", "ai tips", "skincare", "real estate"])
    per_niche: int = 8
    overprovision: float = 2.0
    platforms: tuple = ("instagram", "tiktok")
    views_floor: int = 10_000
    likes_floor: int = 1_000
    ocr_fps: float = 5.0
    scene_threshold: float = 0.30
    ocr_engine: str = "ocrmac"          # ocrmac | fake (paddle if ever installed)
    concurrency: int = 4


def ensure_dirs() -> None:
    for d in (DATA_DIR, TRANSCRIPTS_DIR, OCR_DIR, ANATOMY_DIR, OUT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def reel_id(platform: str, permalink: str) -> str:
    tag = "ig" if platform == "instagram" else "tt"
    return f"{tag}_{hashlib.sha1(permalink.encode()).hexdigest()[:10]}"


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {"built_at": None, "config": {}, "reels": []}


def save_manifest(m: dict) -> None:
    ensure_dirs()
    MANIFEST_PATH.write_text(json.dumps(m, indent=1))


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def mark_stage(rid: str, stage: str, ok: bool, err: str = "") -> None:
    ensure_dirs()
    st = load_state()
    entry = st.setdefault(rid, {})
    entry[stage] = {"ok": ok, "err": err[:300], "ts": time.time()}
    STATE_PATH.write_text(json.dumps(st))


def stage_done(rid: str, stage: str) -> bool:
    return bool(load_state().get(rid, {}).get(stage, {}).get("ok"))


def norm_token(t: str) -> str:
    """Casefold + strip punctuation/emoji for text alignment."""
    return "".join(c for c in t.casefold() if c.isalnum())


def config_dict(cfg: StudyConfig) -> dict:
    d = asdict(cfg)
    d["platforms"] = list(d["platforms"])
    return d
