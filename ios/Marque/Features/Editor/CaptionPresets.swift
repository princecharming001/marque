import SwiftUI

// 10 popular caption looks. Each is a base render style (clean / bold-word / karaoke) plus a
// full CaptionOptions bundle (font / caps / accent / outline / grouping / background pill), so
// picking one FULLY defines the look — switching presets resets every knob (e.g. Hormozi's
// 10px outline clears when you pick Clean). Applied via set_caption_style + set_caption_options;
// all values are already renderable by render/src/components/Captions.tsx (bg pill = schema v6).
struct CaptionPreset: Identifiable, Hashable {
    let id: String
    let label: String
    let style: String            // clean | bold-word | karaoke
    let font: String             // inter | archivo | baloo | montserrat | anton
    let uppercase: Bool
    let accent: String?          // hex, or nil = the style's default (white)
    let strokePx: Double         // outline width
    let grouping: String         // word | phrase | line
    let bg: String               // background pill hex, or "" = none
    /// A tiny swatch color for the picker chip (accent, else box, else white).
    var swatch: Color {
        let s = (accent ?? (bg.isEmpty ? "#FFFFFF" : bg)).trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        return UInt(s.prefix(6), radix: 16).map { Color(hex: $0) } ?? .white
    }

    static let all: [CaptionPreset] = [
        .init(id: "clean",     label: "Clean",     style: "clean",     font: "inter",     uppercase: false, accent: nil,        strokePx: 0,  grouping: "phrase", bg: ""),
        .init(id: "hormozi",   label: "Hormozi",   style: "bold-word", font: "anton",     uppercase: true,  accent: "#FFD93D", strokePx: 10, grouping: "word",   bg: ""),
        .init(id: "karaoke",   label: "Karaoke",   style: "karaoke",   font: "baloo",     uppercase: false, accent: "#22D3EE", strokePx: 0,  grouping: "phrase", bg: ""),
        .init(id: "beast",     label: "Beast",     style: "bold-word", font: "montserrat",uppercase: true,  accent: "#FFFFFF", strokePx: 8,  grouping: "word",   bg: ""),
        .init(id: "boxed",     label: "Boxed",     style: "clean",     font: "montserrat",uppercase: true,  accent: nil,        strokePx: 0,  grouping: "phrase", bg: "#000000"),
        .init(id: "pop",       label: "Pop",       style: "clean",     font: "inter",     uppercase: false, accent: "#A855F7", strokePx: 0,  grouping: "phrase", bg: ""),
        .init(id: "neon",      label: "Neon",      style: "bold-word", font: "anton",     uppercase: true,  accent: "#39FF14", strokePx: 6,  grouping: "word",   bg: ""),
        .init(id: "bubble",    label: "Bubble",    style: "bold-word", font: "baloo",     uppercase: true,  accent: "#FFFFFF", strokePx: 0,  grouping: "word",   bg: "#7C3AED"),
        .init(id: "boldcaps",  label: "Bold Caps", style: "clean",     font: "montserrat",uppercase: true,  accent: nil,        strokePx: 2,  grouping: "phrase", bg: ""),
        .init(id: "editorial", label: "Editorial", style: "clean",     font: "inter",     uppercase: false, accent: nil,        strokePx: 0,  grouping: "line",   bg: ""),
    ]
}
