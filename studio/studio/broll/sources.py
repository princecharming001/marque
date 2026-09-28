"""B-roll sourcing: stock (Pexels), generated stills (Higgsfield), creator media and web captures.

Every sourcing path returns :class:`BrollCandidate` objects. A candidate becomes a job asset with
:func:`download` (or by passing ``job=`` to the generating/capturing functions): the file lands under
``assets/broll/<asset_id>.<ext>``, a licence record ``assets/broll/<asset_id>.licence.json`` is written
next to it, and the :class:`~studio.doc.model.AssetRef` (with its :class:`~studio.doc.model.Licence`) is
registered in ``assets/registry/`` so ops can reference it by ID (invariant 5: a model never authors a
licence record).

Sources, in the doctrine's preference order (``skills/editing/broll-sourcing.md``)
--------------------------------------------------------------------------------
* :func:`creator_media` — the creator's own footage and stills (``creator-owned``; no licence risk).
* :func:`screenshot` — Playwright capture of a real page at a phone viewport. Default viewport is
  360x640 CSS px (a phone's layout) at DPR 4, i.e. **1440x2560 device pixels**: text is rasterised by
  the browser at 4/3 of the output size, so the conform only ever *down*-samples it (Lanczos) — through a
  7 % still push, a crop to one element for a split, or a 1.33x punch — and type stays razor-crisp where a
  1080-wide capture would be upsampled and soften. The capture URL and time are recorded.
  Raises :class:`NotAvailable` when Playwright or its Chromium build is not installed. Browser builds
  live in ``models/ms-playwright`` (``PLAYWRIGHT_BROWSERS_PATH``), never in the user's caches.
* :func:`search` — Pexels video/photo search (``/v1/videos/search``, ``/v1/search``; verified against
  https://www.pexels.com/api/documentation/ in Sept 2026). Portrait results are asked for first, then
  any orientation fills the list (a 4K landscape clip still yields a 1215x2160 9:16 crop). Per video
  the **best rendition** is chosen for the 1080x1920 cover crop: the largest mp4 (≤ UHD) that fills
  the frame without upscaling, else the largest available; a smaller ≥540p rendition is kept as
  ``analysis_url`` so ranking gates can sample frames without downloading the master. Candidates
  whose best rendition is < 1080 px on its short side are dropped. The Pexels licence URL, the page
  URL and the author are recorded (the API guidelines ask for a Pexels link and author credit).
  Pexels' terms bar systematic collection for ML, so nothing here builds a persistent index: preview
  images are fetched per query, used, and discarded.
* :func:`generate_still` — Higgsfield text-to-image (``https://api.higgsfield.ai/<model_id>``, header
  ``Authorization: Key <id>:<secret>``, async submit → poll ``status_url`` until
  ``completed|failed|nsfw|canceled``; verified against docs.higgsfield.ai in Sept 2026). Doctrine says
  AI stills default to **stylized**, not photoreal (photoreal gets auto-labelled and costs ~7–8 % of
  likes), so ``style="stylized"`` uses Recraft V4.1 Pro at its 2K tier (the highest-resolution image
  model on the platform) and ``style="photo"`` uses SOUL Cinema at 1080p. Every generated asset records
  ``ai_generated`` and, for photoreal, ``disclosure_required``. Polling follows the documented
  schedule (2 s growing ×1.5 to 10 s, jitter); a timed-out request is cancelled (only possible while
  queued) and never blindly re-submitted (submissions are not idempotent).

Local media: stills open through Pillow with EXIF orientation (HEIC/HEIF via ``pillow-heif`` when
installed; ffmpeg's decoder otherwise); an animated GIF is a *video* (it plays and loops). A candidate's
ranked use range (``meta["use_ms"]``, measured by :mod:`studio.broll.rank`) becomes the registered asset's
``in_ms``/``out_ms``.

Keys come only from :class:`studio.config.Settings` and are never logged. All HTTP goes through one
``httpx.Client`` that tests replace with a ``MockTransport``.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import mimetypes
import os
import random
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from studio.config import Settings, get_settings

if TYPE_CHECKING:  # pragma: no cover
    from studio.doc.model import AssetRef, Licence
    from studio.jobs import Job

__all__ = [
    "BrollCandidate",
    "SourceError",
    "NotAvailable",
    "GenerationFailed",
    "PEXELS_API",
    "PEXELS_LICENSE_URL",
    "HIGGSFIELD_API",
    "STILL_MODELS",
    "search",
    "search_many",
    "best_video_file",
    "download",
    "generate_still",
    "creator_media",
    "screenshot",
    "playwright_available",
    "licence_from_dict",
    "register_heif",
]

PEXELS_API = "https://api.pexels.com/v1"
PEXELS_LICENSE_URL = "https://www.pexels.com/license/"
PEXELS_TERMS_URL = "https://www.pexels.com/terms-of-service/"
HIGGSFIELD_API = "https://api.higgsfield.ai"
HIGGSFIELD_TERMS_URL = "https://higgsfield.ai/terms-of-use"
USER_AGENT = "YunicornStudio/0.1 (+broll)"

#: Output frame the renditions are chosen for (the 9:16 master).
TARGET_W, TARGET_H = 1080, 1920
#: Never download above this many pixels (UHD + 10 %): more only costs time, the crop is ≤ UHD.
_MAX_RENDITION_PIXELS = int(3840 * 2160 * 1.1)
#: Analysis renditions: the smallest mp4 whose short side is at least this (gates run at 540 px).
_ANALYSIS_SHORT_SIDE = 540

_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".hevc"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif"}

#: Higgsfield still models: style -> (endpoint id, fixed body fields, prompt guard suffix).
STILL_MODELS: dict[str, tuple[str, dict[str, Any], str]] = {
    "stylized": ("recraft/v4.1/pro/text-to-image", {"resolution": "2k", "output_format": "png"},
                 "Stylized editorial illustration, clean composition, single clear subject, "
                 "no text, no letters, no watermark, no logos"),
    "photo": ("higgsfield-ai/soul/cinema", {"resolution": "1080p", "batch_size": 1, "enhance_prompt": False},
              "Natural light, true-to-life colour, single clear subject, no text, no watermark, no logos"),
    "soul": ("higgsfield-ai/soul/v2/standard", {"resolution": "1080p", "batch_size": 1, "enhance_prompt": False},
             "No text, no watermark, no logos"),
}
_HF_TERMINAL = {"completed", "failed", "nsfw", "canceled"}


class SourceError(RuntimeError):
    """A sourcing call failed (HTTP error, bad response, missing file…)."""


class NotAvailable(SourceError):
    """A sourcing tool is not installed / not usable here (e.g. Playwright without Chromium)."""


class GenerationFailed(SourceError):
    """A generation request ended ``failed``/``nsfw``/``canceled`` or timed out."""

    def __init__(self, message: str, *, status: str | None = None, request_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.request_id = request_id


# ============================================================================================ candidate
@dataclass
class BrollCandidate:
    """One sourcing result. ``url`` is the full-quality download URL ('' for local-only candidates).

    ``licence`` is a JSON-able dict (``name``, ``url``, ``holder``, ``attribution`` …, see
    :func:`licence_from_dict`); ``meta`` carries source-specific extras (queries that found it, AI
    disclosure flags, capture time …). ``asset`` is set once the candidate has been downloaded into a job.
    """

    source: str
    source_id: str
    kind: str  # "video" | "image" | "screenshot" | "generated_image"
    url: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    preview_url: str | None = None
    description: str = ""
    query: str = ""
    licence: dict[str, Any] = field(default_factory=dict)
    page_url: str | None = None
    author: str | None = None
    author_url: str | None = None
    fps: float | None = None
    preview_urls: list[str] = field(default_factory=list)
    analysis_url: str | None = None
    renditions: list[dict[str, Any]] = field(default_factory=list)
    local_path: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    asset: AssetRef | None = None

    # ------------------------------------------------------------------ identity
    @property
    def id(self) -> str:
        """Stable candidate/asset ID, e.g. ``pxv_2499611`` (Pexels video), ``pxp_…`` (photo),
        ``cr_<hash>`` (creator), ``hf_<request>`` (Higgsfield), ``ss_<hash>`` (screenshot)."""
        prefix = {
            ("pexels", "video"): "pxv",
            ("pexels", "image"): "pxp",
            ("creator", "video"): "cr",
            ("creator", "image"): "cr",
            ("higgsfield", "generated_image"): "hf",
            ("playwright", "screenshot"): "ss",
        }.get((self.source, self.kind), re.sub(r"[^a-z0-9]+", "", self.source.lower())[:6] or "x")
        raw = f"{prefix}_{self.source_id}"
        return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")[:64]

    @property
    def is_video(self) -> bool:
        return self.kind in ("video", "generated_video")

    @property
    def is_still(self) -> bool:
        return not self.is_video

    @property
    def aspect(self) -> float | None:
        if self.width and self.height:
            return self.width / self.height
        return None

    @property
    def orientation(self) -> str | None:
        if not (self.width and self.height):
            return None
        if self.height > self.width * 1.05:
            return "portrait"
        if self.width > self.height * 1.05:
            return "landscape"
        return "square"

    def to_dict(self) -> dict[str, Any]:
        """JSON-able view (the registered asset appears as ``asset_id``; numpy scalars become Python ones)."""
        import copy

        from studio.jobs import to_jsonable

        d = {k: copy.deepcopy(v) for k, v in self.__dict__.items() if k != "asset"}
        d["id"] = self.id
        d["asset_id"] = self.asset.id if self.asset is not None else None
        return to_jsonable(d)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], *, job: Job | None = None) -> BrollCandidate:
        """Inverse of :meth:`to_dict` (agent tools keep candidates in the job between calls); the registered
        asset is reloaded from ``job`` when its ``asset_id`` is known."""
        import dataclasses

        names = {f.name for f in dataclasses.fields(cls)} - {"asset"}
        c = cls(**{k: v for k, v in d.items() if k in names})
        aid = d.get("asset_id")
        if aid and job is not None:
            c.asset = job.load_asset(str(aid))
        return c

    def summary(self) -> str:
        """One line for tool output / logs (no URLs with tokens; Pexels/HF URLs carry none)."""
        dims = f"{self.width}x{self.height}" if self.width and self.height else "?x?"
        dur = f" {self.duration_ms / 1000:.1f}s" if self.duration_ms else ""
        fps = f" {self.fps:g}fps" if self.fps else ""
        who = f" by {self.author}" if self.author else ""
        what = self.description or self.meta.get("original_name") or self.query or ""
        return f"{self.id} [{self.kind}] {dims}{dur}{fps}{who}: {what[:80]}"


def licence_from_dict(d: Mapping[str, Any], *, record_path: str | None = None) -> Licence:
    """Build the doc :class:`~studio.doc.model.Licence` from a candidate's licence dict."""
    from studio.doc.model import Licence

    allowed = set(Licence.model_fields)
    data = {k: v for k, v in d.items() if k in allowed}
    if record_path is not None:
        data["record_path"] = record_path
    data.setdefault("name", "unknown")
    return Licence(**data)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _settings(settings: Settings | None) -> Settings:
    return settings if settings is not None else get_settings()


def _client(client: httpx.Client | None, timeout: float = 30.0) -> tuple[httpx.Client, bool]:
    """``(client, owned)``: the given client, or a new one the caller must close."""
    if client is not None:
        return client, False
    return httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0), follow_redirects=True,
                        headers={"User-Agent": USER_AGENT}), True


def _request(client: httpx.Client, method: str, url: str, *, attempts: int = 3, backoff_s: float = 1.5,
             retry_status: Iterable[int] = (429, 500, 502, 503, 504), sleep: Callable[[float], None] = time.sleep,
             **kw: Any) -> httpx.Response:
    """HTTP with bounded retries on transport errors and transient statuses (never on 4xx other than 429)."""
    retry_status = set(retry_status)
    last_exc: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            r = client.request(method, url, **kw)
        except httpx.TransportError as e:  # connect/read errors
            last_exc = e
        else:
            if r.status_code not in retry_status or i == attempts - 1:
                return r
        sleep(backoff_s * (2 ** i) + random.uniform(0, 0.25))
    raise SourceError(f"{method} {_safe_url(url)} failed: {last_exc}") from last_exc


def _safe_url(url: str) -> str:
    """URL without query string (query strings may carry tokens on some CDNs)."""
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}"
    except Exception:  # pragma: no cover
        return "<url>"


# ============================================================================================ Pexels
def _pexels_licence(page_url: str | None, author: str | None, author_url: str | None, kind: str) -> dict[str, Any]:
    noun = "Video" if kind == "video" else "Photo"
    return {
        "name": "Pexels License",
        "source": "pexels",
        "url": PEXELS_LICENSE_URL,
        "holder": author,
        "attribution": f"{noun} by {author} on Pexels" if author else f"{noun} from Pexels",
        "attribution_required": False,
        "commercial_use": True,
        "notes": (f"page: {page_url or '-'}; author page: {author_url or '-'}; terms: {PEXELS_TERMS_URL}. "
                  "Free commercial use; attribution appreciated (API guidelines: show a Pexels link and "
                  "credit the author when possible). Restrictions: identifiable people must not be shown in "
                  "a bad light or as endorsing anything; no use of visible trademarks/brands in relation to "
                  "goods and services; Pexels warrants no model/property releases."),
    }


def best_video_file(video: Mapping[str, Any], *, target_w: int = TARGET_W, target_h: int = TARGET_H
                    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """``(best, analysis)`` renditions of one Pexels video object.

    *best*: among mp4 renditions with known size (HLS entries have null dims), the largest ≤ UHD that
    covers ``target_w x target_h`` without upscaling (a 9:16 cover crop), else the largest available.
    Supersampling from a bigger master is sharper than Pexels' own lower-bitrate HD transcodes, so larger
    wins when it fits. *analysis*: the smallest mp4 whose short side is ≥ 540 px (fast frame sampling).
    """
    files = [dict(f) for f in (video.get("video_files") or []) if isinstance(f, Mapping)]
    usable = [f for f in files if f.get("link") and f.get("width") and f.get("height")
              and str(f.get("file_type") or "video/mp4").lower() in ("video/mp4", "video/quicktime")]
    if not usable:
        return None, None

    def area(f: Mapping[str, Any]) -> int:
        return int(f["width"]) * int(f["height"])

    def covers(f: Mapping[str, Any]) -> bool:
        return max(target_w / int(f["width"]), target_h / int(f["height"])) <= 1.0 + 1e-9

    capped = [f for f in usable if area(f) <= _MAX_RENDITION_PIXELS] or usable
    fitting = [f for f in capped if covers(f)]
    pool = fitting or capped
    best = max(pool, key=lambda f: (area(f), float(f.get("fps") or 0)))
    small = [f for f in usable if min(int(f["width"]), int(f["height"])) >= _ANALYSIS_SHORT_SIDE]
    analysis = min(small, key=area) if small else best
    return best, analysis


def _parse_pexels_video(v: Mapping[str, Any], query: str) -> BrollCandidate | None:
    best, analysis = best_video_file(v)
    if best is None:
        return None
    user = v.get("user") or {}
    pics = [p.get("picture") for p in sorted(v.get("video_pictures") or [], key=lambda p: p.get("nr") or 0)
            if isinstance(p, Mapping) and p.get("picture")]
    page = v.get("url")
    author, author_url = user.get("name"), user.get("url")
    desc = _slug_description(page) or query
    renditions = [{k: f.get(k) for k in ("id", "quality", "file_type", "width", "height", "fps", "link")}
                  for f in v.get("video_files") or [] if isinstance(f, Mapping)]
    fps = best.get("fps")
    return BrollCandidate(
        source="pexels", source_id=str(v.get("id")), kind="video", url=str(best["link"]),
        width=int(best["width"]), height=int(best["height"]),
        duration_ms=round(float(v.get("duration") or 0) * 1000) or None,
        preview_url=v.get("image") or (pics[0] if pics else None), description=desc, query=query,
        licence=_pexels_licence(page, author, author_url, "video"), page_url=page, author=author,
        author_url=author_url, fps=float(fps) if fps else None,
        preview_urls=[u for u in [v.get("image"), *pics] if u],
        analysis_url=str(analysis["link"]) if analysis else None,
        renditions=renditions,
        meta={"source_width": v.get("width"), "source_height": v.get("height"), "queries": [query]},
    )


def _parse_pexels_photo(p: Mapping[str, Any], query: str) -> BrollCandidate | None:
    src = p.get("src") or {}
    url = src.get("original") or src.get("large2x")
    if not url:
        return None
    page = p.get("url")
    author, author_url = p.get("photographer"), p.get("photographer_url")
    return BrollCandidate(
        source="pexels", source_id=str(p.get("id")), kind="image", url=str(url),
        width=int(p["width"]) if p.get("width") else None, height=int(p["height"]) if p.get("height") else None,
        preview_url=src.get("large") or src.get("medium") or url,
        description=(p.get("alt") or "").strip() or _slug_description(page) or query, query=query,
        licence=_pexels_licence(page, author, author_url, "image"), page_url=page, author=author,
        author_url=author_url,
        preview_urls=[u for u in [src.get("large"), src.get("portrait")] if u][:1] or [url],
        analysis_url=src.get("large2x") or src.get("large") or url,
        meta={"avg_color": p.get("avg_color"), "queries": [query]},
    )


def _slug_description(page_url: str | None) -> str:
    """``https://www.pexels.com/video/a-person-typing-on-a-laptop-3194277/`` → ``a person typing on a laptop``."""
    if not page_url:
        return ""
    m = re.search(r"/(?:video|photo)/([a-z0-9-]+?)-?\d*/?$", page_url)
    return m.group(1).replace("-", " ").strip() if m else ""


def _short_side(c: BrollCandidate) -> int:
    return min(c.width or 0, c.height or 0)


def _orientation_rank(c: BrollCandidate) -> int:
    return {"portrait": 0, "square": 1, "landscape": 2}.get(c.orientation or "", 3)


def search(query: str, kind: str | None = None, n: int | None = None, *,
           kinds: Sequence[str] = ("video", "image"), orientation: str | None = "portrait", limit: int = 20,
           min_width: int = TARGET_W, fallback_any_orientation: bool = True, page: int = 1,
           locale: str | None = None, settings: Settings | None = None,
           client: httpx.Client | None = None) -> list[BrollCandidate]:
    """Search Pexels for ``query``; returns up to ``limit`` (or ``n``) candidates per kind, portrait first.

    ``kind`` (``"video"`` | ``"photo"``/``"image"``) and ``n`` are shorthands for ``kinds=(kind,)`` and
    ``limit=n``. ``min_width`` is the minimum short side of the chosen rendition (1080 = usable for a
    vertical master). Requires the Pexels key (``PEXELS_KEY``).
    """
    if kind is not None:
        kinds = (kind,)
    if n is not None:
        limit = n
    kinds = tuple("image" if k in ("photo", "photos", "image", "images") else "video" for k in kinds)
    if not query.strip():
        raise ValueError("empty query")
    s = _settings(settings)
    key = s.require_key("pexels")
    cl, owned = _client(client)
    try:
        out: list[BrollCandidate] = []
        for k in kinds:
            found: dict[str, BrollCandidate] = {}
            orients: list[str | None] = [orientation]
            if orientation is not None and fallback_any_orientation:
                orients.append(None)
            for orient in orients:
                if len(found) >= limit:
                    break
                for c in _pexels_page(cl, key, query, k, orient, per_page=min(80, max(limit * 2, 15)), page=page,
                                      locale=locale):
                    if _short_side(c) < min_width:
                        continue
                    found.setdefault(c.id, c)
            ranked = list(found.values())
            if orientation == "portrait":
                ranked.sort(key=_orientation_rank)  # stable: API relevance order within each orientation
            out.extend(ranked[:limit])
        return out
    finally:
        if owned:
            cl.close()


def _pexels_page(client: httpx.Client, key: str, query: str, kind: str, orientation: str | None, *,
                 per_page: int, page: int, locale: str | None) -> list[BrollCandidate]:
    url = f"{PEXELS_API}/videos/search" if kind == "video" else f"{PEXELS_API}/search"
    params: dict[str, Any] = {"query": query, "per_page": per_page, "page": page,
                              "size": "medium"}  # Full HD (video) / 12 MP (photo) minimum
    if orientation:
        params["orientation"] = orientation
    if locale:
        params["locale"] = locale
    r = _request(client, "GET", url, params=params, headers={"Authorization": key, "User-Agent": USER_AGENT})
    if r.status_code == 401 or r.status_code == 403:
        raise SourceError(f"Pexels rejected the API key (HTTP {r.status_code})")
    if r.status_code == 429:
        raise SourceError("Pexels rate limit reached (200/h, 20k/month); retry later")
    if r.status_code >= 400:
        raise SourceError(f"Pexels search failed: HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError as e:
        raise SourceError("Pexels returned a non-JSON body") from e
    items = data.get("videos" if kind == "video" else "photos") or []
    parse = _parse_pexels_video if kind == "video" else _parse_pexels_photo
    out = []
    for it in items:
        if isinstance(it, Mapping):
            c = parse(it, query)
            if c is not None:
                out.append(c)
    return out


def search_many(queries: Sequence[str], *, kinds: Sequence[str] = ("video",), per_query: int = 15,
                max_total: int = 200, **kw: Any) -> list[BrollCandidate]:
    """Fan ``queries`` (5–10 concrete variants per beat) out to :func:`search`; de-duplicate by ID keeping the
    first hit and recording every query that found it in ``meta["queries"]``. A failing query is skipped."""
    seen: dict[str, BrollCandidate] = {}
    errors: list[str] = []
    for q in queries:
        try:
            res = search(q, kinds=kinds, limit=per_query, **kw)
        except SourceError as e:
            errors.append(f"{q}: {e}")
            continue
        for c in res:
            if c.id in seen:
                qs = seen[c.id].meta.setdefault("queries", [])
                if q not in qs:
                    qs.append(q)
            else:
                seen[c.id] = c
            if len(seen) >= max_total:
                break
        if len(seen) >= max_total:
            break
    if not seen and errors:
        raise SourceError("; ".join(errors))
    return list(seen.values())


# ============================================================================================ download
def _ext_for(c: BrollCandidate, content_type: str | None = None) -> str:
    if c.local_path:
        ext = Path(c.local_path).suffix.lower()
        if ext:
            return ext
    path = urlparse(c.url).path if c.url else ""
    ext = Path(path).suffix.lower()
    if ext in _VIDEO_EXTS | _IMAGE_EXTS:
        return ".jpg" if ext == ".jpeg" else ext
    if content_type:
        guess = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guess:
            return ".jpg" if guess in (".jpe", ".jpeg") else guess
    return ".mp4" if c.is_video else ".jpg"


_heif_lock = threading.Lock()
_heif_done = False


def register_heif() -> bool:
    """Teach Pillow to open HEIC/HEIF (iPhone photos) via ``pillow-heif`` when installed; idempotent.
    Returns whether HEIF decoding is available (else ffmpeg's decoder is the fallback)."""
    global _heif_done
    with _heif_lock:
        if not _heif_done:
            try:
                import pillow_heif

                pillow_heif.register_heif_opener()
                _heif_done = True
            except ImportError:
                return False
        return True


def _probe_local(path: Path) -> tuple[str, int | None, int | None, int | None, float | None]:
    """``(kind, width, height, duration_ms, fps)`` of a local media file ("video" or "image"). An animated
    GIF is a video (it plays and loops); a single-frame one is an image."""
    ext = path.suffix.lower()
    if ext in _IMAGE_EXTS:
        register_heif()
        try:
            from PIL import Image, ImageOps

            with Image.open(path) as im:
                if not (ext == ".gif" and getattr(im, "n_frames", 1) > 1):
                    im = ImageOps.exif_transpose(im)
                    return "image", im.width, im.height, None, None
        except Exception:
            pass
    from studio.perception.frames import probe_video

    try:
        vp = probe_video(path)
    except Exception as e:
        raise SourceError(f"not a readable image or video: {path.name} ({e})") from e
    # a one-frame "video" (still image container) → image
    if vp.duration_us <= 0 or vp.frame_count <= 1:
        return "image", vp.width, vp.height, None, None
    return "video", vp.width, vp.height, round(vp.duration_us / 1000), float(vp.fps)


def _stream_download(client: httpx.Client, url: str, dest: Path, *, max_bytes: int = 2 << 30,
                     attempts: int = 3) -> str | None:
    """Stream ``url`` into ``dest`` atomically; returns the content type."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for i in range(attempts):
        fd, tmp = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".part", dir=dest.parent)
        try:
            with os.fdopen(fd, "wb") as fh, client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as r:
                if r.status_code in (429, 500, 502, 503, 504) and i < attempts - 1:
                    raise httpx.TransportError(f"HTTP {r.status_code}")
                if r.status_code >= 400:
                    raise SourceError(f"download failed: HTTP {r.status_code} for {_safe_url(url)}")
                ctype = r.headers.get("content-type")
                if ctype and ctype.startswith(("text/html", "application/json")):
                    raise SourceError(f"download returned {ctype.split(';')[0]}, not media: {_safe_url(url)}")
                n = 0
                for chunk in r.iter_bytes(1 << 20):
                    n += len(chunk)
                    if n > max_bytes:
                        raise SourceError(f"download exceeds {max_bytes >> 20} MB: {_safe_url(url)}")
                    fh.write(chunk)
                fh.flush()
                os.fsync(fh.fileno())
            if n == 0:
                raise SourceError(f"empty download: {_safe_url(url)}")
            os.chmod(tmp, 0o644)  # mkstemp creates 0600; job media are ordinary readable files
            os.replace(tmp, dest)
            return ctype
        except httpx.TransportError as e:
            last = e
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            time.sleep(1.0 * (2 ** i))
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
    raise SourceError(f"download failed after {attempts} attempts: {_safe_url(url)} ({last})")


def download(candidate: BrollCandidate, job: Job, *, settings: Settings | None = None,
             client: httpx.Client | None = None, overwrite: bool = False) -> AssetRef:
    """Put the candidate's media into ``assets/broll/<id>.<ext>`` with a licence json and register it.

    Remote candidates are streamed (atomic temp file + rename); local ones (creator media, screenshots) are
    hard-linked or copied. The stored file is probed so the AssetRef carries the *real* width/height/
    duration. Returns the registered :class:`~studio.doc.model.AssetRef` (also set on ``candidate.asset``).
    """
    from studio.doc.model import AssetRef

    aid = candidate.id
    broll_dir = job.assets_dir / "broll"
    broll_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(p for p in broll_dir.glob(f"{aid}.*")
                      if not p.name.endswith((".licence.json", ".download", ".part")))
    dest: Path
    if existing and not overwrite:
        dest = existing[0]
    elif candidate.local_path:
        src = Path(candidate.local_path)
        if not src.exists():
            raise SourceError(f"local media missing: {src}")
        dest = broll_dir / f"{aid}{_ext_for(candidate)}"
        if dest.exists():
            dest.unlink()
        try:
            os.link(src, dest)
        except OSError:
            shutil.copy2(src, dest)
    else:
        if not candidate.url:
            raise SourceError(f"candidate {aid} has neither a url nor a local path")
        cl, owned = _client(client, timeout=120.0)
        try:
            tmp_dest = broll_dir / f"{aid}.download"
            ctype = _stream_download(cl, candidate.url, tmp_dest)
            dest = broll_dir / f"{aid}{_ext_for(candidate, ctype)}"
            os.replace(tmp_dest, dest)
        finally:
            if owned:
                cl.close()

    kind, w, h, dur_ms, fps = _probe_local(dest)
    rel = dest.relative_to(job.root).as_posix()
    record_rel = f"assets/broll/{aid}.licence.json"
    record = {
        "asset_id": aid,
        "file": rel,
        "source": candidate.source,
        "source_id": candidate.source_id,
        "kind": candidate.kind,
        "licence": candidate.licence,
        "page_url": candidate.page_url,
        "download_url": _safe_url(candidate.url) if candidate.url and "://" in candidate.url else candidate.url,
        "author": candidate.author,
        "author_url": candidate.author_url,
        "query": candidate.query,
        "queries": candidate.meta.get("queries"),
        "description": candidate.description,
        "acquired_at": _now_iso(),
        "sha256": _sha256(dest),
        "media": {"kind": kind, "width": w, "height": h, "duration_ms": dur_ms, "fps": fps},
        "ai_generated": bool(candidate.meta.get("ai_generated", False)),
        "disclosure_required": bool(candidate.meta.get("disclosure_required", False)),
        "meta": {k: v for k, v in candidate.meta.items() if k not in ("queries",)},
    }
    job.save_json(record_rel, record)
    lic = licence_from_dict({**candidate.licence, "acquired_at": record["acquired_at"]}, record_path=record_rel)
    asset_kind = {"video": "video", "image": "image", "screenshot": "screenshot",
                  "generated_image": "generated_image", "generated_video": "generated_video"}.get(candidate.kind, kind)
    if asset_kind in ("video", "generated_video") and kind == "image":
        asset_kind = "image"
    # the use range ranking chose (code-measured: best relevance, no shot change, no fade) travels with
    # the asset, because ops copy the registered record and a model never supplies an in-point
    in_ms, out_ms = 0, None
    if kind == "video":
        use = candidate.meta.get("use_ms") or [candidate.meta.get("in_ms", 0), None]
        in_ms = max(0, int(use[0] or 0))
        out_ms = int(use[1]) if len(use) > 1 and use[1] is not None else None
        if dur_ms is not None and (in_ms >= dur_ms or (out_ms is not None and out_ms > dur_ms + 50)):
            in_ms, out_ms = 0, None  # a range measured on another rendition that does not fit this file
    asset = AssetRef(
        kind=asset_kind, source=candidate.source, source_id=candidate.source_id, path=rel,
        url=candidate.page_url or (candidate.url or None), in_ms=in_ms, out_ms=out_ms,
        width=w, height=h, duration_ms=dur_ms, description=candidate.description[:500],
        query=candidate.query or None, licence=lic,
    )
    rec = job.register_asset(asset, asset_id=aid, overwrite=True)
    candidate.asset = rec
    if fps and not candidate.fps:
        candidate.fps = fps
    candidate.local_path = str(dest)
    return rec


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ============================================================================================ creator media
def creator_media(paths: str | os.PathLike[str] | Sequence[str | os.PathLike[str]], *, job: Job | None = None,
                  description: str = "", holder: str = "creator") -> list[BrollCandidate]:
    """Wrap the creator's own files as candidates (licence ``creator-owned``). With ``job`` each is copied
    into the job and registered. Watermark/privacy checks still apply at ranking (a downloaded TikTok
    carries another platform's watermark; ask for the original)."""
    items = [paths] if isinstance(paths, (str, os.PathLike)) else list(paths)
    out: list[BrollCandidate] = []
    for p in items:
        path = Path(p).expanduser()
        if not path.is_file():
            raise SourceError(f"creator media not found: {path}")
        kind, w, h, dur_ms, fps = _probe_local(path)
        st = path.stat()
        hid = hashlib.sha1(f"{path.name}:{st.st_size}:{_head_hash(path)}".encode()).hexdigest()[:12]
        c = BrollCandidate(
            # no invented description: a file name ("IMG_4123") is noise to the semantic crop and the judge
            source="creator", source_id=hid, kind=kind, url="", width=w, height=h, duration_ms=dur_ms,
            description=description, fps=fps, local_path=str(path.resolve()),
            licence={"name": "creator-owned", "source": "creator", "holder": holder, "commercial_use": True,
                     "attribution_required": False,
                     "notes": f"supplied by the creator ({path.name}); mute its audio, blur bystanders/private data"},
            meta={"original_name": path.name},
        )
        if job is not None:
            download(c, job)
        out.append(c)
    return out


def _head_hash(path: Path, n: int = 1 << 20) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha1(fh.read(n)).hexdigest()[:16]


# ============================================================================================ Higgsfield
def _hf_auth(settings: Settings) -> str:
    key = settings.require_key("higgsfield")
    if ":" not in key:
        raise SourceError("HIGGSFIELD_KEY must be '<key_id>:<key_secret>'")
    return f"Key {key}"


def _hf_status_url(base: str, submit: Mapping[str, Any]) -> str:
    rid = submit.get("request_id") or submit.get("id")
    su = submit.get("status_url")
    if isinstance(su, str) and su.startswith(base.rstrip("/") + "/"):
        return su
    if not rid:
        raise SourceError("Higgsfield submit response has no request_id")
    return f"{base.rstrip('/')}/requests/{rid}/status"


def generate_still(prompt: str, *, job: Job | None = None, style: str = "stylized", model: str | None = None,
                   aspect_ratio: str = "9:16", seed: int | None = None, guard_prompt: bool = True,
                   extra: Mapping[str, Any] | None = None, timeout_s: float = 420.0,
                   settings: Settings | None = None, client: httpx.Client | None = None,
                   sleep: Callable[[float], None] = time.sleep, base_url: str = HIGGSFIELD_API) -> BrollCandidate:
    """Generate one still with Higgsfield (default: stylized, 9:16, highest available resolution).

    ``style``: ``"stylized"`` (Recraft V4.1 Pro, 2K), ``"photo"`` (SOUL Cinema, 1080p; flagged
    ``disclosure_required``) or ``"soul"`` (SOUL V2). ``model`` overrides the endpoint id (then ``extra``
    supplies its fields). ``guard_prompt`` appends "no text, no watermark, no logos" guidance. With ``job``,
    the image is downloaded and registered. Raises :class:`GenerationFailed` on failed/nsfw/timeout.
    """
    if not prompt.strip():
        raise ValueError("empty prompt")
    if style not in STILL_MODELS and model is None:
        raise ValueError(f"unknown style {style!r} (one of {', '.join(STILL_MODELS)})")
    model_id, fixed, guard = STILL_MODELS.get(style, ("", {}, ""))
    body: dict[str, Any] = {} if model is not None else dict(fixed)
    model_id = model or model_id
    text = prompt.strip()
    if guard_prompt and guard:
        text = f"{text.rstrip('.')}. {guard}."
    body.update({"prompt": text, "aspect_ratio": aspect_ratio})
    if seed is not None:
        if not (1 <= int(seed) <= 1_000_000):  # documented range for the SOUL models
            raise ValueError("seed must be within 1–1000000")
        body["seed"] = int(seed)
    if extra:
        body.update(dict(extra))
    s = _settings(settings)
    auth = _hf_auth(s)
    headers = {"Authorization": auth, "Content-Type": "application/json", "User-Agent": USER_AGENT}
    cl, owned = _client(client, timeout=60.0)
    deadline = time.monotonic() + timeout_s
    try:
        submit = _hf_submit(cl, f"{base_url.rstrip('/')}/{model_id}", body, headers, deadline, sleep)
        rid = str(submit.get("request_id") or submit.get("id") or "")
        status_url = _hf_status_url(base_url, submit)
        cancel_url = submit.get("cancel_url") if isinstance(submit.get("cancel_url"), str) and \
            str(submit.get("cancel_url")).startswith(base_url.rstrip("/") + "/") else None
        result = _hf_poll(cl, status_url, headers, deadline, sleep, rid, cancel_url=cancel_url)
        status = str(result.get("status") or "").lower()
        if status != "completed":
            raise GenerationFailed(f"Higgsfield request ended {status or 'unknown'}"
                                   + (f": {result.get('error')}" if result.get("error") else ""),
                                   status=status, request_id=rid)
        images = result.get("images") or []
        url = (images[0] or {}).get("url") if images and isinstance(images[0], Mapping) else None
        if not url and isinstance(result.get("image"), Mapping):
            url = result["image"].get("url")
        if not url:
            raise GenerationFailed("Higgsfield completed without an image url", status=status, request_id=rid)
    finally:
        if owned:
            cl.close()
    # every SOUL model renders photoreal people/places (platform AI labels apply); Recraft is illustration
    photoreal = style == "photo" or "/soul" in model_id
    cand = BrollCandidate(
        source="higgsfield", source_id=(rid or hashlib.sha1(url.encode()).hexdigest())[:36],
        kind="generated_image", url=str(url), description=prompt.strip()[:300], query=prompt.strip()[:300],
        licence={
            "name": "AI-generated (Higgsfield)", "source": "higgsfield", "url": HIGGSFIELD_TERMS_URL,
            "holder": "account owner (AI output)", "attribution_required": False, "commercial_use": True,
            "notes": (f"generated by {model_id} ({style}); prompt recorded in the licence json. "
                      f"{'PHOTOREAL: disclose as AI (platform labels).' if photoreal else 'Stylized AI still.'} "
                      "Purely AI output is not copyrightable in the US. Confirm the account plan grants "
                      "commercial rights to outputs."),
        },
        meta={"ai_generated": True, "photoreal": photoreal, "disclosure_required": photoreal, "model": model_id,
              "style": style, "prompt": text, "seed": seed, "aspect_ratio": aspect_ratio, "request_id": rid,
              "generated_at": _now_iso()},
    )
    if job is not None:
        a = download(cand, job, settings=s, client=client)
        cand.meta["output_size"] = [a.width, a.height]
        want = _ratio(aspect_ratio)
        if want and a.width and a.height and abs((a.width / a.height) / want - 1) > 0.05:
            # the model ignored the requested aspect (the conform still cover-crops it): record it
            cand.meta["aspect_mismatch"] = True
    return cand


def _ratio(ar: str) -> float | None:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[:x/]\s*(\d+(?:\.\d+)?)\s*", ar or "")
    return float(m.group(1)) / float(m.group(2)) if m and float(m.group(2)) > 0 else None


def _hf_submit(client: httpx.Client, url: str, body: Mapping[str, Any], headers: Mapping[str, str],
               deadline: float, sleep: Callable[[float], None]) -> dict[str, Any]:
    """POST a generation. Retries only when the request was definitively *not* accepted (concurrency 400,
    423/503 model unavailable, 5xx before acceptance is ambiguous → no retry; submissions aren't idempotent)."""
    wait = 5.0
    while True:
        try:
            r = client.post(url, json=dict(body), headers=dict(headers))
        except httpx.TransportError as e:
            raise SourceError(f"Higgsfield submit failed (not retried; submissions are not idempotent): {e}") from e
        if r.status_code < 300:
            try:
                return r.json()
            except ValueError as e:
                raise SourceError("Higgsfield submit returned a non-JSON body") from e
        detail = _hf_detail(r)
        concurrency = r.status_code == 400 and "concurrent" in detail.lower()
        if (concurrency or r.status_code in (423, 503)) and time.monotonic() + wait < deadline:
            sleep(wait + random.uniform(0, 1.0))
            wait = min(wait * 1.5, 30.0)
            continue
        if r.status_code == 401:
            raise SourceError("Higgsfield rejected the credentials (HTTP 401)")
        if r.status_code == 403:
            raise SourceError(f"Higgsfield: insufficient credits / forbidden (HTTP 403: {detail[:120]})")
        raise SourceError(f"Higgsfield submit failed: HTTP {r.status_code}: {detail[:200]}")


def _hf_detail(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail")
    except Exception:
        return r.text[:200] if r.text else ""
    return d if isinstance(d, str) else json.dumps(d)[:300]


def _hf_poll(client: httpx.Client, status_url: str, headers: Mapping[str, str], deadline: float,
             sleep: Callable[[float], None], rid: str, *, cancel_url: str | None = None) -> dict[str, Any]:
    delay = 2.0
    errors = 0
    while True:
        try:
            r = client.get(status_url, headers=dict(headers))
        except httpx.TransportError:
            r = None
        if r is not None and r.status_code == 200:
            errors = 0
            try:
                data = r.json()
            except ValueError:
                data = {}
            status = str(data.get("status") or "").lower()
            if status in _HF_TERMINAL:
                return data
        elif r is not None and r.status_code in (401, 404):
            raise GenerationFailed(f"Higgsfield status poll failed: HTTP {r.status_code}", request_id=rid)
        else:
            errors += 1
            if errors > 20:
                raise GenerationFailed("Higgsfield status poll kept failing", request_id=rid)
        if time.monotonic() + delay > deadline:
            _hf_cancel(client, cancel_url or (status_url.rsplit("/", 1)[0] + "/cancel"), headers)
            raise GenerationFailed("Higgsfield generation timed out", status="timeout", request_id=rid)
        sleep(delay + random.uniform(0, 0.5))
        delay = min(delay * 1.5, 10.0)


def _hf_cancel(client: httpx.Client, cancel_url: str, headers: Mapping[str, str]) -> None:
    """Best effort: only a still-queued request can be cancelled (202); a started one answers 400 and is
    billed if it completes (failed/nsfw are refunded)."""
    with contextlib.suppress(Exception):
        client.post(cancel_url, headers=dict(headers))


# ============================================================================================ screenshots
def _browsers_path(settings: Settings) -> Path:
    return settings.models_dir / "ms-playwright"


def playwright_available(settings: Settings | None = None) -> tuple[bool, str]:
    """``(ok, reason)``: Playwright importable and a Chromium build installed under ``models/ms-playwright``
    (or ``PLAYWRIGHT_BROWSERS_PATH``)."""
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False, "playwright is not installed (pip install playwright)"
    s = _settings(settings)
    bp = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or _browsers_path(s))
    if not bp.is_dir() or not any(bp.glob("chromium*")):
        return False, (f"no Chromium build in {bp} (run: PLAYWRIGHT_BROWSERS_PATH={bp} "
                       "python -m playwright install chromium --only-shell)")
    return True, "ok"


_IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
              "Version/17.5 Mobile/15E148 Safari/604.1")


def screenshot(url: str, *, job: Job | None = None, width: int = 360, height: int = 640, scale: float = 4.0,
               full_page: bool = False, selector: str | None = None, dark_mode: bool = False,
               wait_until: str = "networkidle", settle_ms: int = 800, timeout_s: float = 45.0,
               user_agent: str | None = _IPHONE_UA, out_path: str | os.PathLike[str] | None = None,
               description: str = "", settings: Settings | None = None) -> BrollCandidate:
    """Capture ``url`` with headless Chromium at a phone viewport (default 1440x2560 device pixels, 9:16).

    ``selector`` captures just that element (crop to the line that matters); ``full_page`` captures the
    whole scroll height (for a scroll/pan insert). Only ``http(s)`` and ``file`` URLs are accepted. The
    candidate records the URL and capture time; licence is a nominative/fair-use capture of a public page
    (the doctrine: real, unaltered, private individuals obscured, no DMs/private accounts).
    """
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https", "file"):
        raise ValueError(f"unsupported URL scheme for a capture: {scheme or url!r}")
    s = _settings(settings)
    ok, reason = playwright_available(s)
    if not ok:
        raise NotAvailable(f"screenshot unavailable: {reason}")
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_browsers_path(s)))
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    captured_at = _now_iso()
    sid = hashlib.sha1(f"{url}|{captured_at}|{selector}|{full_page}".encode()).hexdigest()[:12]
    if out_path is None:
        base = (job.assets_dir / "broll" / "captures") if job is not None else Path(tempfile.mkdtemp(prefix="ss_"))
        base.mkdir(parents=True, exist_ok=True)
        out = base / f"ss_{sid}.png"
    else:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
    title = ""
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except PWError as e:
                raise NotAvailable(f"Chromium failed to launch: {str(e).splitlines()[0]}") from e
            try:
                ctx = browser.new_context(
                    viewport={"width": int(width), "height": int(height)}, device_scale_factor=float(scale),
                    is_mobile=True, has_touch=True, user_agent=user_agent, locale="en-US",
                    color_scheme="dark" if dark_mode else "light", reduced_motion="reduce")
                page = ctx.new_page()
                try:
                    page.goto(url, wait_until=wait_until, timeout=timeout_s * 1000)  # type: ignore[arg-type]
                except PWTimeout:
                    page.goto(url, wait_until="load", timeout=timeout_s * 1000)
                if settle_ms:
                    page.wait_for_timeout(settle_ms)
                title = page.title() or ""
                if selector:
                    el = page.locator(selector).first
                    el.scroll_into_view_if_needed(timeout=timeout_s * 1000)
                    el.screenshot(path=str(out), timeout=timeout_s * 1000, animations="disabled")
                else:
                    page.screenshot(path=str(out), full_page=full_page, timeout=timeout_s * 1000,
                                    animations="disabled")
            finally:
                browser.close()
    except NotAvailable:
        raise
    except PWError as e:
        raise SourceError(f"capture of {_safe_url(url)} failed: {str(e).splitlines()[0]}") from e
    from PIL import Image

    with Image.open(out) as im:
        w, h = im.size
    host = urlparse(url).netloc or "local file"
    cand = BrollCandidate(
        source="playwright", source_id=sid, kind="screenshot", url="", width=w, height=h,
        description=description or title or host, query=url, page_url=url, local_path=str(out),
        licence={"name": "web capture (nominative fair use)", "source": "playwright", "url": url, "holder": host,
                 "attribution_required": False, "commercial_use": True,
                 "notes": (f"real, unaltered capture of {url} at {captured_at} ({w}x{h}). Use to identify/discuss "
                           "the page only; imply no endorsement; obscure private individuals; no DMs or "
                           "private accounts. Legal edge cases go to counsel.")},
        meta={"captured_at": captured_at, "capture_url": url, "title": title, "selector": selector,
              "full_page": full_page, "viewport": [width, height], "device_scale_factor": scale},
    )
    if job is not None:
        download(cand, job, settings=s)
    return cand
