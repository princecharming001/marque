import Foundation

// The Sound-mode music catalog for ProEditorView. These are the deterministic
// fallback tracks the editor shows when the backend GET /v1/music returns nothing
// (keyless) or fails. Extracted from the retired EditorView.swift (E-24).
enum MusicCatalog {
    struct Track { let name: String; let url: String }
    // #14: AVPlayer cannot decode Ogg Vorbis — the two `.ogg` demo tracks played as
    // dead silence (and rendered silent previews). Every fallback track must be an
    // AVPlayer-native container (mp3/m4a/aac). These three are royalty-free and serve
    // `audio/mpeg` with range support.
    static let tracks: [Track] = [
        Track(name: "Neverwritten (upbeat)",
              url: "https://commondatastorage.googleapis.com/codeskulptor-demos/DDR_assets/Kangaroo_MusiQue_-_The_Neverwritten_Role_Playing_Game.mp3"),
        Track(name: "Reverie (chill)",
              url: "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3"),
        Track(name: "Momentum (driving)",
              url: "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"),
    ]
}
