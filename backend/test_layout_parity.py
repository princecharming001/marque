"""Layout-constant parity across the three sides of the render contract: the JS
render/src/layout.json, the Python backend/app/layout_constants.py, and the Swift
ios/.../LayoutConstants.swift. A silent drift between these is exactly the class of
bug the formatting-hardening pass targets (e.g. the caption pos_y clamp mismatch
found during design: backend/iOS agreed at [0.15,0.85], render alone drifted to
[0.16,0.84]). Update all three files together; this test is the tripwire."""
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAYOUT_JSON = REPO_ROOT / "render" / "src" / "layout.json"
SWIFT_CONSTANTS = REPO_ROOT / "ios" / "Marque" / "Features" / "Editor" / "LayoutConstants.swift"
RENDER_SRC = REPO_ROOT / "render" / "src"


def _load_layout_json() -> dict:
    return json.loads(LAYOUT_JSON.read_text())


def _load_backend_constants() -> dict:
    import app.layout_constants as lc
    return {
        "FRAME_W": lc.FRAME_W, "FRAME_H": lc.FRAME_H, "FPS": lc.FPS,
        "SAFE_TOP_PX": lc.SAFE_TOP_PX, "SAFE_BOTTOM_PX": lc.SAFE_BOTTOM_PX,
        "CAPTION_ANCHOR_Y": lc.CAPTION_ANCHOR_Y,
        "CAPTION_POS_Y_MIN": lc.CAPTION_POS_Y_MIN, "CAPTION_POS_Y_MAX": lc.CAPTION_POS_Y_MAX,
        "CAPTION_MAX_LINES": lc.CAPTION_MAX_LINES, "CAPTION_MIN_SHRINK": lc.CAPTION_MIN_SHRINK,
        "CAPTION_HIDE_AFTER_LAST": lc.CAPTION_HIDE_AFTER_LAST, "CAPTION_SILENCE_GAP": lc.CAPTION_SILENCE_GAP,
        "DEFAULT_WORD_FRAMES": lc.DEFAULT_WORD_FRAMES,
        "SIZE_MULT": lc.SIZE_MULT, "PHRASE_LEN": lc.PHRASE_LEN, "LINE_LEN": lc.LINE_LEN,
        "STICKER_POS_X_MIN": lc.STICKER_POS_X_MIN, "STICKER_POS_X_MAX": lc.STICKER_POS_X_MAX,
        "STICKER_POS_Y_MIN": lc.STICKER_POS_Y_MIN, "STICKER_POS_Y_MAX": lc.STICKER_POS_Y_MAX,
        "CARD_MAX_LINES": lc.CARD_MAX_LINES, "CARD_MIN_FONT": lc.CARD_MIN_FONT,
        "QUOTE_MAX_LINES": lc.QUOTE_MAX_LINES, "QUOTE_MIN_FONT": lc.QUOTE_MIN_FONT,
        "CREDIT_CHIP_TOP_PX": lc.CREDIT_CHIP_TOP_PX,
        "MIN_CLIP_OUTPUT_FRAMES": lc.MIN_CLIP_OUTPUT_FRAMES,
    }


_SWIFT_SCALAR = re.compile(r'static let (\w+):\s*(?:Double|Int)\s*=\s*([\d.]+)')
_SWIFT_DICT = re.compile(r'static let (\w+):\s*\[String:\s*Double\]\s*=\s*\[(.*?)\]', re.DOTALL)
_SWIFT_DICT_ENTRY = re.compile(r'"(\w+)":\s*([\d.]+)')

# camelCase Swift name -> UPPER_SNAKE json/python key
_SWIFT_TO_CANONICAL = {
    "frameW": "FRAME_W", "frameH": "FRAME_H", "fps": "FPS",
    "safeTopPx": "SAFE_TOP_PX", "safeBottomPx": "SAFE_BOTTOM_PX",
    "captionAnchorY": "CAPTION_ANCHOR_Y",
    "captionPosYMin": "CAPTION_POS_Y_MIN", "captionPosYMax": "CAPTION_POS_Y_MAX",
    "captionMaxLines": "CAPTION_MAX_LINES", "captionMinShrink": "CAPTION_MIN_SHRINK",
    "captionHideAfterLast": "CAPTION_HIDE_AFTER_LAST", "captionSilenceGap": "CAPTION_SILENCE_GAP",
    "defaultWordFrames": "DEFAULT_WORD_FRAMES",
    "sizeMult": "SIZE_MULT", "phraseLen": "PHRASE_LEN", "lineLen": "LINE_LEN",
    "stickerPosXMin": "STICKER_POS_X_MIN", "stickerPosXMax": "STICKER_POS_X_MAX",
    "stickerPosYMin": "STICKER_POS_Y_MIN", "stickerPosYMax": "STICKER_POS_Y_MAX",
    "cardMaxLines": "CARD_MAX_LINES", "cardMinFont": "CARD_MIN_FONT",
    "quoteMaxLines": "QUOTE_MAX_LINES", "quoteMinFont": "QUOTE_MIN_FONT",
    "creditChipTopPx": "CREDIT_CHIP_TOP_PX",
    "minClipOutputFrames": "MIN_CLIP_OUTPUT_FRAMES",
}


def _load_swift_constants() -> dict:
    text = SWIFT_CONSTANTS.read_text()
    out: dict = {}
    for name, value in _SWIFT_SCALAR.findall(text):
        canon = _SWIFT_TO_CANONICAL.get(name)
        if canon:
            out[canon] = float(value)
    for name, body in _SWIFT_DICT.findall(text):
        canon = _SWIFT_TO_CANONICAL.get(name)
        if canon:
            out[canon] = {k: float(v) for k, v in _SWIFT_DICT_ENTRY.findall(body)}
    return out


def test_layout_json_matches_backend_constants():
    js = _load_layout_json()
    py = _load_backend_constants()
    assert set(js.keys()) == set(py.keys()), (set(js.keys()) ^ set(py.keys()))
    for key in js:
        assert js[key] == py[key], f"{key}: layout.json={js[key]!r} != layout_constants.py={py[key]!r}"


def test_layout_json_matches_swift_literals():
    js = _load_layout_json()
    swift = _load_swift_constants()
    missing = set(js.keys()) - set(swift.keys())
    assert not missing, f"LayoutConstants.swift is missing: {missing}"
    for key, expected in js.items():
        actual = swift[key]
        if isinstance(expected, dict):
            assert set(expected.keys()) == set(actual.keys()), f"{key}: {expected.keys()} != {actual.keys()}"
            for sub in expected:
                assert abs(expected[sub] - actual[sub]) < 1e-6, f"{key}.{sub}: {expected[sub]} != {actual[sub]}"
        else:
            assert abs(float(expected) - float(actual)) < 1e-6, f"{key}: {expected} != {actual}"


# Literals that must NOT reappear as bare numbers in these files once they're wired
# to the shared constants — a regression here means a new site was added with a
# hardcoded number instead of importing the shared module.
_STRAY_LITERAL_PATTERNS = [
    re.compile(r'"system-ui"'),
]


def test_no_stray_layout_literals_in_render_src():
    for path in (RENDER_SRC / "components" / "Captions.tsx",
                 RENDER_SRC / "components" / "TextStickers.tsx"):
        text = path.read_text()
        for pattern in _STRAY_LITERAL_PATTERNS:
            assert not pattern.search(text), f"{path.name} contains a stray literal matching {pattern.pattern}"


def test_ios_editor_uses_layout_constants():
    # Spot-check the sites this fix touched — each must reference LayoutConstants,
    # not a bare numeric literal, for the specific clamps this test cares about.
    pro_editor = (REPO_ROOT / "ios" / "Marque" / "Features" / "Editor" / "ProEditorView.swift").read_text()
    assert "LayoutConstants.captionAnchorY" in pro_editor
    assert "LayoutConstants.captionPosYMin" in pro_editor and "LayoutConstants.captionPosYMax" in pro_editor
    assert "LayoutConstants.stickerPosXMin" in pro_editor and "LayoutConstants.stickerPosYMin" in pro_editor

    local_engine = (REPO_ROOT / "ios" / "Marque" / "Features" / "Editor" / "LocalEDLEngine.swift").read_text()
    assert "LayoutConstants.stickerPosXMin" in local_engine
    assert "LayoutConstants.stickerPosYMin" in local_engine
    assert "LayoutConstants.captionPosYMin" in local_engine
