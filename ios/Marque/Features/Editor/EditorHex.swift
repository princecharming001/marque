import Foundation

// MARK: - EditorHex — the ONE hex-colour parser the editor preview uses (ED-6).
//
// Foundation-only (LogicTests). The server stores sticker colours as "#RRGGBB"
// (backend app/edl.py: re.fullmatch(r"#[0-9a-fA-F]{6}")) and caption pills as
// "#RRGGBB" or "#RRGGBBAA"; the old preview parser blindly dropped the first character,
// so an un-prefixed "FFFFFF" previewed as 0x0FFFFF (cyan) and "#RRGGBBAA" lost its alpha
// into the blue channel.

enum EditorHex {
    /// "#RRGGBB" / "#RRGGBBAA", with or without the leading '#'. rgb = 0xRRGGBB.
    static func parse(_ raw: String) -> (rgb: UInt, alpha: Double)? {
        var s = raw.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("#") { s.removeFirst() }
        guard s.count == 6 || s.count == 8, s.allSatisfy({ $0.isHexDigit }),
              let v = UInt(s, radix: 16) else { return nil }
        if s.count == 8 { return (v >> 8, Double(v & 0xFF) / 255.0) }
        return (v, 1.0)
    }

    /// True for exactly the sticker colours the server (and LocalEDLEngine) accept.
    static func isStickerColor(_ s: String) -> Bool {
        s.count == 7 && s.hasPrefix("#") && s.dropFirst().allSatisfy { $0.isHexDigit }
    }

    /// The wire form of a 6-digit swatch ("FFD60A" or "#ffd60a" → "#FFD60A"); nil otherwise.
    static func stickerWire(_ s: String) -> String? {
        guard let p = parse(s), p.alpha == 1.0 else { return nil }
        let hex = String(p.rgb, radix: 16, uppercase: true)
        return "#" + String(repeating: "0", count: max(0, 6 - hex.count)) + hex
    }
}
