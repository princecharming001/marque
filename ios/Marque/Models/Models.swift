import Foundation

// MARK: - Core domain models (mirrors 06-brand-graph.md, 08-format-virality.md, 12-backend-data-security.md)

enum Goal: String, CaseIterable, Codable, Identifiable {
    case audience = "Grow my audience"
    case clients = "Get clients"
    case authority = "Build authority"
    case monetize = "Monetize"
    var id: String { rawValue }
}

struct VoiceFingerprint: Codable, Hashable {
    var funnyToSerious: Double = 0.5     // 0 funny … 1 serious
    var polishedToRaw: Double = 0.5      // 0 polished … 1 raw
    var teacherToPeer: Double = 0.5      // 0 teacher … 1 peer
    var bannedWords: [String] = []
    var catchphrases: [String] = []
}

struct BrandGraph: Codable, Hashable {
    var niche: String = ""
    var whatYouDo: String = ""
    var audience: String = ""
    var knownFor: String = ""
    var goal: Goal = .audience
    var voice = VoiceFingerprint()
    var nonNegotiables: [String] = []
    var pageHandle: String = ""
    var analyzed: Bool = false
    var topThemes: [String] = []
    var preferredStyles: [VideoStyle] = []   // the video styles the creator wants to make
    var connectedAccounts: [ConnectedAccount] = []
}

/// A linked Instagram/TikTok account, verified by fetching the real public profile.
struct ConnectedAccount: Codable, Hashable, Identifiable {
    var id = UUID()
    var platform: String        // "instagram" | "tiktok"
    var handle: String
    var displayName: String = ""
    var followers: Int = 0
    var avatarUrl: String = ""
    var bio: String = ""
    var linkedAt: Date = Date()
    var platformIcon: String { platform == "instagram" ? "camera.circle.fill" : "music.note" }
    var platformLabel: String { platform == "instagram" ? "Instagram" : "TikTok" }
}

struct Pillar: Codable, Hashable, Identifiable {
    var id = UUID()
    var name: String
    var summary: String = ""            // one-line what-this-pillar-is
    var angle: String = ""             // the creator's specific take / why it's theirs
    var exampleTopics: [String] = []   // 3 concrete video ideas
    var weight: Double                 // share of the content mix 0…1
    var colorHex: UInt
}

// 8 hook signal types (08-format-virality.md)
enum HookSignal: String, CaseIterable, Codable {
    case stakes, authority, curiosity, patternInterrupt, specificity, contrarian, narrative, callOut
    var label: String {
        switch self {
        case .stakes: return "Stakes"
        case .authority: return "Authority"
        case .curiosity: return "Curiosity gap"
        case .patternInterrupt: return "Pattern interrupt"
        case .specificity: return "Specificity"
        case .contrarian: return "Contrarian"
        case .narrative: return "Narrative"
        case .callOut: return "Call-out"
        }
    }
}

struct Hook: Codable, Hashable, Identifiable {
    var id = UUID()
    var text: String
    var signal: HookSignal
    var strength: Int           // virality predictor 0…100
}

// Format Library entry = a structured render-recipe
struct VideoFormat: Codable, Hashable, Identifiable {
    var id: String              // slug
    var name: String
    var blurb: String
    var faceMode: FaceMode
    var targetSeconds: Int
    var bestHooks: [HookSignal]
    enum FaceMode: String, Codable { case face, faceless, split, greenScreen }
}

/// The coarse video style the creator picks; each produces a structurally different script.
enum VideoStyle: String, CaseIterable, Codable, Identifiable {
    case talkingHead = "talking_head"
    case splitThree = "split_three"
    case faceless
    case fastCuts = "fast_cuts"
    case greenScreen = "green_screen"
    var id: String { rawValue }
    var label: String {
        switch self {
        case .talkingHead: return "Talking-head"
        case .splitThree: return "3-way split"
        case .faceless: return "Faceless voiceover"
        case .fastCuts: return "Fast cuts"
        case .greenScreen: return "Green-screen react"
        }
    }
    var blurb: String {
        switch self {
        case .talkingHead: return "You, to camera, with captions."
        case .splitThree: return "3 panels — a different point in each, one after another."
        case .faceless: return "Voiceover over b-roll — no on-camera."
        case .fastCuts: return "Rapid one-line cuts, high energy."
        case .greenScreen: return "You reacting over a post or screenshot."
        }
    }
    var icon: String {
        switch self {
        case .talkingHead: return "person.fill"
        case .splitThree: return "rectangle.split.1x2"
        case .faceless: return "film"
        case .fastCuts: return "scissors"
        case .greenScreen: return "person.crop.rectangle"
        }
    }
    /// Fine-grained format recipes allowed within this style (mirrors the backend).
    var formats: [String] {
        switch self {
        case .talkingHead: return ["myth-buster", "listicle", "pov-story"]
        case .splitThree: return ["listicle", "do-this-not-that", "before-after"]
        case .faceless: return ["faceless", "broll-hook"]
        case .fastCuts: return ["listicle", "broll-hook", "myth-buster"]
        case .greenScreen: return ["green-screen"]
        }
    }
}

struct Script: Codable, Hashable, Identifiable {
    var id = UUID()
    var pillarName: String
    var title: String = ""      // short human title (≤6 words) for the card heading
    var summary: String = ""    // one-line "what this video is about"
    var style: String = ""      // the video style it was written for (talking_head/faceless/split_screen)
    var formatId: String
    var hook: Hook
    var altHooks: [Hook]
    var body: String
    var cta: String
    var shotPlan: [String]
    var targetSeconds: Int
    var predictedScore: Int
    var approved: Bool = false
    var createdAt: Date = Date()
}

enum ClipStatus: String, Codable { case rendering, ready, scheduled, posted, failed }

struct Clip: Codable, Hashable, Identifiable {
    var id = UUID()
    var scriptId: UUID
    var formatId: String
    var formatName: String
    var title: String = ""              // mirrors the script title for display
    var caption: String                 // social caption text
    var captionLines: [String] = []     // burned-in / auto-caption lines (timed display)
    var predictedScore: Int
    var status: ClipStatus
    var seconds: Int
    var localVideoPath: String? = nil   // captured/rendered file in the app container
    var remoteURL: String? = nil        // public R2/Stream URL once rendered server-side
    var thumbnailPath: String? = nil    // poster frame in the app container
    var captioned: Bool = false         // whether auto-captions were burned in
    var jobId: String? = nil            // backend clip-job ID for polling render status
    var createdAt: Date = Date()
}

enum SocialPlatform: String, CaseIterable, Codable, Identifiable {
    case instagram, tiktok
    var id: String { rawValue }
    var label: String { self == .instagram ? "Instagram" : "TikTok" }
}

struct PostMetrics: Codable, Hashable {
    var views: Int = 0
    var likes: Int = 0
    var comments: Int = 0
    var shares: Int = 0
    var followsGained: Int = 0
    var saves: Int = 0
    var reach: Int = 0
    var avgWatchPct: Double = 0         // 0.0–1.0
    var linkClicks: Int = 0
    var settled: Bool = false           // true once metrics have "settled" (T+7d)
    var capturedAt: Date = Date()
    var engagementRate: Double {        // (likes+comments+shares) / views
        views > 0 ? Double(likes + comments + shares) / Double(views) : 0
    }
}

struct ScheduledPost: Codable, Hashable, Identifiable {
    var id = UUID()
    var clipId: UUID
    var caption: String
    var platforms: [SocialPlatform]
    var date: Date
    var autoCaptions: Bool = true       // burn captions before publishing
    var mediaURL: String? = nil         // public render URL attached to the post (Ayrshare mediaUrls)
    var posted: Bool = false
    var metrics: PostMetrics? = nil     // populated post-publish from Insights
}

// MARK: - Personal media corpus (the AI references this when writing/cutting reels)

enum AnalysisStatus: String, Codable {
    case none, analyzing, done, failed
}

enum MediaKind: String, Codable, CaseIterable, Identifiable {
    case selfie, bRoll, clip, screenshot, other
    var id: String { rawValue }
    var label: String {
        switch self {
        case .selfie: return "You"
        case .bRoll: return "B-roll"
        case .clip: return "Clip"
        case .screenshot: return "Screenshot"
        case .other: return "Other"
        }
    }
    var icon: String {
        switch self {
        case .selfie: return "person.fill"
        case .bRoll: return "film"
        case .clip: return "play.rectangle.fill"
        case .screenshot: return "rectangle.on.rectangle"
        case .other: return "photo"
        }
    }
}

/// A piece of the creator's personal media library, imported in bulk so the AI can
/// reference real footage/photos of them when planning future reels.
struct MediaAsset: Codable, Hashable, Identifiable {
    var id = UUID()
    var localPath: String               // file in the app container
    var kind: MediaKind = .other
    var note: String = ""               // user/AI tag: "gym", "office desk", "on stage"
    var isVideo: Bool = false
    var thumbnailPath: String? = nil
    var addedAt: Date = Date()
    // Analysis (filled async by backend after upload)
    var contentHash: String = ""
    var storageKey: String = ""
    var remoteURL: String = ""
    var analysisStatus: AnalysisStatus = .none
    var aiDescription: String = ""
    var aiTags: [String] = []
    var brollSuitability: Int = 0           // 0-100
    var brollSuitabilityReason: String = ""
    var usableAs: String = "broll"          // broll | take | thumbnail | other
    var hasface: Bool = false
    var onScreenText: String = ""
}

/// A take the creator filmed (or imported) but hasn't decided what to do with yet.
/// Lives in the Library "Footage" tab; you make clips from it later.
struct Footage: Codable, Hashable, Identifiable {
    var id = UUID()
    var localPath: String
    var scriptId: UUID? = nil           // set if it was filmed against a script
    var title: String = ""
    var seconds: Int = 0
    var thumbnailPath: String? = nil
    var addedAt: Date = Date()
}

struct TrendItem: Codable, Hashable, Identifiable {
    var id = UUID()
    var title: String
    var why: String
    var formatId: String
}

struct TeardownCard: Codable, Hashable, Identifiable {
    var id = UUID()
    var clipCaption: String
    var headline: String
    var detail: String
    var liftPercent: Int
}

// MARK: - Static catalogs

enum Catalog {
    static let formats: [VideoFormat] = [
        .init(id: "myth-buster", name: "Myth-Buster", blurb: "“Everyone thinks X… but.” Cognitive-dissonance payoff at 6–8s.", faceMode: .face, targetSeconds: 24, bestHooks: [.contrarian, .curiosity]),
        .init(id: "listicle", name: "3-Step Listicle", blurb: "Numbered breakdown, B-roll switch every ~2.5s.", faceMode: .face, targetSeconds: 30, bestHooks: [.specificity, .authority]),
        .init(id: "do-this-not-that", name: "Do This, Not That", blurb: "Side-by-side wrong vs. right.", faceMode: .split, targetSeconds: 22, bestHooks: [.contrarian, .callOut]),
        .init(id: "before-after", name: "Before / After", blurb: "Transformation reveal — drives rewatches.", faceMode: .split, targetSeconds: 26, bestHooks: [.specificity, .narrative]),
        .init(id: "green-screen", name: "Green-Screen", blurb: "You in front of a post, chart, or screenshot.", faceMode: .greenScreen, targetSeconds: 28, bestHooks: [.curiosity, .authority]),
        .init(id: "faceless", name: "Faceless AI-Visual", blurb: "Voiceover over generated visuals — no camera.", faceMode: .faceless, targetSeconds: 30, bestHooks: [.curiosity, .narrative]),
        .init(id: "pov-story", name: "POV / Story", blurb: "“POV:” or mid-action open, loop-friendly ending.", faceMode: .face, targetSeconds: 28, bestHooks: [.narrative, .stakes]),
        .init(id: "broll-hook", name: "B-roll + Caption Hook", blurb: "5–8s of B-roll with a provocative one-liner.", faceMode: .faceless, targetSeconds: 12, bestHooks: [.patternInterrupt, .contrarian]),
    ]

    static func format(_ id: String) -> VideoFormat {
        formats.first { $0.id == id } ?? formats[0]
    }

    /// Map a fine-grained format slug to the coarse video style it belongs to.
    static func style(for formatId: String) -> VideoStyle {
        for s in VideoStyle.allCases where s.formats.contains(formatId) { return s }
        return .talkingHead
    }

    // Calm, distinct per-pillar hues (used only as small accents + the active-card gradient).
    static let pillarColors: [UInt] = [0x2C6BED, 0x2F9E60, 0x9A6A55, 0x8A6FA0, 0xB5791C, 0x4C6E91]
}
