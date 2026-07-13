"""Single-source layout constants — MIRRORS render/src/layout.json exactly.

apply_edl_ops clamps (edl.py) and the retention passes import from here rather than
hardcoding literals, so backend/render/iOS drift is caught by
backend/test_layout_parity.py instead of discovered visually in a delivered render.

Keep this file's VALUES byte-for-byte equal to render/src/layout.json and
ios/Marque/Features/Editor/LayoutConstants.swift — the parity test asserts all three
agree. Update all three together.
"""

FRAME_W = 1080
FRAME_H = 1920
FPS = 30

SAFE_TOP_PX = 280
SAFE_BOTTOM_PX = 320

CAPTION_ANCHOR_Y = {"top": 0.1458, "middle": 0.46, "bottom": 0.8333}
CAPTION_POS_Y_MIN = 0.15
CAPTION_POS_Y_MAX = 0.85
CAPTION_MAX_LINES = 2
CAPTION_MIN_SHRINK = 0.5

CAPTION_HIDE_AFTER_LAST = 12
CAPTION_SILENCE_GAP = 30
DEFAULT_WORD_FRAMES = 15

SIZE_MULT = {"small": 0.78, "medium": 1.0, "large": 1.24}
PHRASE_LEN = 3
LINE_LEN = 5

STICKER_POS_X_MIN = 0.08
STICKER_POS_X_MAX = 0.92
STICKER_POS_Y_MIN = 0.15
STICKER_POS_Y_MAX = 0.78

CARD_MAX_LINES = 5
CARD_MIN_FONT = 26
QUOTE_MAX_LINES = 3
QUOTE_MIN_FONT = 22

CREDIT_CHIP_TOP_PX = 120

MIN_CLIP_OUTPUT_FRAMES = 12
