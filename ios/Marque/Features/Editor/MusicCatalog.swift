import Foundation

// The Sound-mode music catalog for ProEditorView. `current` is what the picker shows:
// the backend catalog (GET /v1/music — the SAME beds the render/selection uses) once
// hydrated, else the built-in fallback below.
enum MusicCatalog {
    struct Track: Hashable { let name: String; let url: String }

    // #14: AVPlayer cannot decode Ogg Vorbis — .ogg tracks played as dead silence (and
    // rendered silent). Every track MUST be an AVPlayer-native container (mp3/m4a/aac)
    // served with range support. These mirror the backend _BUILTIN_MUSIC_TRACKS so the
    // editor picker and the auto-selected bed come from one source of truth.
    static let fallback: [Track] = [
        Track(name: "Momentum",    url: "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"),
        Track(name: "Groundwork",  url: "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3"),
        Track(name: "Still Air",   url: "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3"),
        Track(name: "Uplift",      url: "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-5.mp3"),
        Track(name: "Reflect",     url: "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-6.mp3"),
        Track(name: "Assured",     url: "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-8.mp3"),
        Track(name: "Throughline", url: "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-9.mp3"),
        Track(name: "Neverwritten", url: "https://commondatastorage.googleapis.com/codeskulptor-demos/DDR_assets/Kangaroo_MusiQue_-_The_Neverwritten_Role_Playing_Game.mp3"),
    ]

    /// Backend catalog once hydrated (nil until GET /v1/music returns). Set on the main actor.
    static var remote: [Track]? = nil

    /// What the picker renders: backend catalog when available, else the built-in fallback.
    static var current: [Track] { (remote?.isEmpty == false) ? remote! : fallback }

    // Back-compat alias for existing call sites.
    static var tracks: [Track] { current }

    /// Fetch the backend catalog once so the editor shows the exact beds the render uses.
    /// Fire-and-forget; failure leaves the fallback in place.
    @MainActor
    static func hydrate(using backend: BackendClient) async {
        if remote != nil { return }
        let fetched = await backend.musicCatalog()
        if !fetched.isEmpty { remote = fetched }
    }
}
