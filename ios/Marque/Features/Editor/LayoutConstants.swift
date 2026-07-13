import Foundation

/// Single-source layout constants — MIRRORS render/src/layout.json and
/// backend/app/layout_constants.py exactly. The editor preview (ProEditorView) and
/// LocalEDLEngine's op clamps read from here so a future value change updates all
/// three sides from their own canonical file; backend/test_layout_parity.py asserts
/// all three agree. Update all three together.
enum LayoutConstants {
    static let frameW: Double = 1080
    static let frameH: Double = 1920
    static let fps: Double = 30

    static let safeTopPx: Double = 280
    static let safeBottomPx: Double = 320

    static let captionAnchorY: [String: Double] = ["top": 0.1458, "middle": 0.46, "bottom": 0.8333]
    static let captionPosYMin: Double = 0.15
    static let captionPosYMax: Double = 0.85
    static let captionMaxLines: Int = 2
    static let captionMinShrink: Double = 0.5

    static let captionHideAfterLast: Int = 12
    static let captionSilenceGap: Int = 30
    static let defaultWordFrames: Int = 15

    static let sizeMult: [String: Double] = ["small": 0.78, "medium": 1.0, "large": 1.24]
    static let phraseLen: Int = 3
    static let lineLen: Int = 5

    static let stickerPosXMin: Double = 0.08
    static let stickerPosXMax: Double = 0.92
    static let stickerPosYMin: Double = 0.15
    static let stickerPosYMax: Double = 0.78

    static let cardMaxLines: Int = 5
    static let cardMinFont: Double = 26
    static let quoteMaxLines: Int = 3
    static let quoteMinFont: Double = 22

    static let creditChipTopPx: Double = 120

    static let minClipOutputFrames: Int = 12
}
