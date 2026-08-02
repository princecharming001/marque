import Foundation

// Wire → model for the two taste routes (GET /v1/cta-styles, GET /v1/style-deck), pulled
// out of BackendClient so the mapping can be compiled and exercised against a REAL captured
// payload without standing up the client, the app, or the network. BackendClient still owns
// the request; this owns only the shape.
//
// Both decoders are total: an unreadable or partial payload degrades to "nothing"
// (empty / nil) rather than throwing, matching the rest of the client's convention —
// a missing taste deck must never be able to take a screen down.

enum DeckDecoding {

    // MARK: CTA styles

    private struct CTAStylesResp: Decodable { let styles: [CTAStyleDTO] }
    private struct CTAStyleDTO: Decodable {
        let id: String; let label: String; let blurb: String?
        let cluster: String?; let ui_class: String?; let params: [String]?
        let video_url: String?; let thumbnail_url: String?
    }

    /// Server order is meaningful — "none" leads, then the restrained templates — so it is
    /// preserved verbatim rather than re-sorted client-side.
    static func ctaStyles(from data: Data) -> [CTAStyleOption] {
        guard let r = try? JSONDecoder().decode(CTAStylesResp.self, from: data) else { return [] }
        return r.styles.map {
            CTAStyleOption(id: $0.id, label: $0.label, blurb: $0.blurb ?? "",
                           cluster: $0.cluster ?? "minimal", uiClass: $0.ui_class ?? "",
                           params: $0.params ?? [], videoURL: $0.video_url ?? "",
                           thumbnailURL: $0.thumbnail_url ?? "")
        }
    }

    // MARK: Style deck

    private struct StyleDeckResp: Decodable {
        let deck_version: Int?; let dims: [String]?; let cold_start: [String: Double]?
        let min_swipes: Int?
        let reels: [DeckReelDTO]?; let archetypes: [ArchetypeDTO]?; let samples: [SampleDTO]?
    }
    private struct DeckReelDTO: Decodable {
        let reel_id: String; let video_url: String?; let thumbnail_url: String?
        let niche: String?; let author: String?; let views: Int?; let duration_s: Double?
        let vector: [String: Double]?; let display_attrs: [String]?
    }
    // Hand-mapped rather than decoded straight into StyleArchetype: the wire calls the
    // human name `label`, the mapper's struct calls it `name`. Decoding directly would
    // silently yield an empty name (its Codable init is tolerant by design).
    private struct ArchetypeDTO: Decodable {
        let id: String; let label: String?; let theme_id: String?
        let vector: [String: Double]?
    }
    private struct SampleDTO: Decodable { let archetype_id: String; let video_url: String? }

    static func styleDeck(from data: Data) -> StyleDeckPayload? {
        guard let r = try? JSONDecoder().decode(StyleDeckResp.self, from: data) else { return nil }
        return StyleDeckPayload(
            deckVersion: r.deck_version ?? 0,
            dims: r.dims ?? StyleProfileMapper.dims,
            coldStart: StyleProfileMapper.normalize(r.cold_start),
            minSwipes: r.min_swipes ?? StyleProfileMapper.minSwipes,
            reels: (r.reels ?? []).map {
                StyleDeckReel(id: $0.reel_id, videoURL: $0.video_url ?? "",
                              thumbnailURL: $0.thumbnail_url ?? "", niche: $0.niche ?? "",
                              author: $0.author ?? "", views: $0.views ?? 0,
                              durationS: $0.duration_s ?? 0,
                              // Normalize on the way in: a deck reel missing a dim would
                              // otherwise drag that axis toward 0 in the Rocchio mean.
                              vector: StyleProfileMapper.normalize($0.vector),
                              displayAttrs: $0.display_attrs ?? [])
            },
            archetypes: (r.archetypes ?? []).map {
                StyleArchetype(id: $0.id, name: $0.label ?? $0.id, themeId: $0.theme_id,
                               previewURL: nil,
                               vector: StyleProfileMapper.normalize($0.vector))
            },
            samples: (r.samples ?? []).map {
                StyleSampleClip(archetypeId: $0.archetype_id, videoURL: $0.video_url ?? "")
            })
    }
}
