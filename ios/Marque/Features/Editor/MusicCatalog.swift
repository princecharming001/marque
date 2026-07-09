import Foundation

// The Sound-mode music catalog for ProEditorView. These are the deterministic
// fallback tracks the editor shows when the backend GET /v1/music returns nothing
// (keyless) or fails. Extracted from the retired EditorView.swift (E-24).
enum MusicCatalog {
    struct Track { let name: String; let url: String }
    static let tracks: [Track] = [
        Track(name: "Neverwritten (upbeat)",
              url: "https://commondatastorage.googleapis.com/codeskulptor-demos/DDR_assets/Kangaroo_MusiQue_-_The_Neverwritten_Role_Playing_Game.mp3"),
        Track(name: "Epoq (chill)",
              url: "https://commondatastorage.googleapis.com/codeskulptor-assets/Epoq-Lepidoptera.ogg"),
        Track(name: "Race Menu (driving)",
              url: "https://commondatastorage.googleapis.com/codeskulptor-demos/riceracer_assets/music/menu.ogg"),
    ]
}
