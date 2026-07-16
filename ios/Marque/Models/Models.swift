import Foundation

// MARK: - Core domain models (mirrors 06-brand-graph.md, 08-format-virality.md, 12-backend-data-security.md)

enum Goal: String, CaseIterable, Codable, Identifiable {
    case audience = "Grow my audience"
    case clients = "Get clients"
    case authority = "Build authority"
    case monetize = "Monetize"
    var id: String { rawValue }
}

enum CreatorStage: String, CaseIterable, Codable, Identifiable {
    case nano = "0–1K followers"
    case micro = "1K–10K followers"
    case established = "10K–100K followers"
    case pro = "100K+ followers"
    var id: String { rawValue }
    var label: String { rawValue }

    /// Derive the stage from a connected account's real follower count, so the
    /// quiz can skip asking what we already know.
    static func from(followers: Int) -> CreatorStage {
        switch followers {
        case ..<1_000: .nano
        case ..<10_000: .micro
        case ..<100_000: .established
        default: .pro
        }
    }
}

/// The creator's stated trigger for starting now — the emotional "why now" that
/// anchors the paywall and biases script strategy (backend `why_now` hint).
enum WhyNow: String, CaseIterable, Codable, Identifiable {
    case serious = "I'm ready to take content seriously"
    case launch = "I'm launching something that needs an audience"
    case inspired = "I keep watching people like me blow up"
    case income = "I want content to become income"
    var id: String { rawValue }
    var label: String { rawValue }
    /// Stable short key sent to the backend (prompt hint maps key on this).
    var key: String {
        switch self {
        case .serious: "serious"
        case .launch: "launch"
        case .inspired: "inspired"
        case .income: "income"
        }
    }
}

enum PostingFrequency: String, CaseIterable, Codable, Identifiable {
    case rarely = "0–1x a week"
    case sometimes = "2–3x a week"
    case often = "4–5x a week"
    case daily = "Daily or more"
    var id: String { rawValue }
    var label: String { rawValue }
}

enum CreatorBlocker: String, CaseIterable, Codable, Identifiable {
    case ideas = "Running out of ideas"
    case time = "Not enough time"
    case editing = "Editing takes forever"
    case confidence = "Camera confidence"
    var id: String { rawValue }
    var label: String { rawValue }
    var emoji: String {
        switch self {
        case .ideas: return "💡"
        case .time: return "📆"
        case .editing: return "✂️"
        case .confidence: return "😬"
        }
    }
    /// Stable short key sent to the backend — the prompt's blocker strategy map
    /// is keyed on these, not the display label.
    var key: String {
        switch self {
        case .ideas: return "ideas"
        case .time: return "time"
        case .editing: return "editing"
        case .confidence: return "confidence"
        }
    }
}

enum CameraComfort: String, CaseIterable, Codable, Identifiable {
    case natural = "Natural — I'm comfortable on camera"
    case gettingThere = "Getting there — still working on it"
    case preferOff = "Prefer off-camera or voiceover"
    var id: String { rawValue }
    var label: String { rawValue }
    /// Stable short key sent to the backend — the prompt's comfort strategy map
    /// is keyed on these, not the display label.
    var key: String {
        switch self {
        case .natural: return "natural"
        case .gettingThere: return "getting_there"
        case .preferOff: return "prefer_off"
        }
    }
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
    var primaryPlatform: SocialPlatform? = nil
    var stage: CreatorStage? = nil
    var postingFrequency: PostingFrequency? = nil
    var biggestBlocker: CreatorBlocker? = nil
    var cameraComfort: CameraComfort? = nil
    var weeklyTarget: Int? = nil
    var watchedCreators: [WatchedCreator]? = nil   // ≤2 "creators to watch" (Profile)
    var creatorName: String? = nil                 // collected in the mascot-intro onboarding step
    var emulationTargets: [EmulationTarget]? = nil // whose style the creator wants scripts to borrow
    var whyNow: WhyNow? = nil                      // the emotional trigger for starting (quiz)
}

/// A creator whose style the AI should study (preset or a custom linked page).
/// Optional array on BrandGraph — a non-optional field would fail to decode
/// existing installs' persisted snapshots (see watchedCreators precedent above).
struct EmulationTarget: Codable, Hashable, Identifiable {
    enum Source: String, Codable { case preset, custom }
    var id = UUID()
    var name: String
    var handle: String = ""
    var platform: String = ""      // "instagram" | "tiktok"; empty for presets without a linked page
    var source: Source
    var avatarUrl: String = ""     // set once a .custom target is verified against the real page
    var followers: Int = 0
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
    // Post for Me account id ("spc_…"). Empty for handle-only links (voice-learning,
    // scraped public profile) — only accounts with a non-empty accountId can be posted to.
    var accountId: String = ""
    var platformIcon: String { platform == "instagram" ? "camera.circle.fill" : "music.note" }
    var platformLabel: String { platform == "instagram" ? "Instagram" : "TikTok" }
    /// True once this account is OAuth-linked through Post for Me and can publish.
    var canPublish: Bool { !accountId.isEmpty }
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
    case greenScreen = "green_screen"
    case brollCutaway = "broll_cutaway"
    case splitThree = "split_three"
    case duetSplit = "duet_split"
    case faceless
    case fastCuts = "fast_cuts"   // held back from the offered set (see `offered`); kept for decode-safety
    var id: String { rawValue }

    /// The render styles offered in-app right now (mirrors backend prompts.ACTIVE_STYLES).
    /// `fastCuts` stays a valid case so old persisted data still decodes, but isn't offered.
    static let offered: [VideoStyle] = [.talkingHead, .greenScreen, .brollCutaway, .splitThree, .duetSplit, .faceless]

    var label: String {
        switch self {
        case .talkingHead: return "Talking-head"
        case .greenScreen: return "Screenshot react"
        case .brollCutaway: return "B-roll cutaway"
        case .splitThree: return "3-way split"
        case .duetSplit: return "Duet / react split"
        case .faceless: return "Faceless voiceover"
        case .fastCuts: return "Fast cuts"
        }
    }
    var blurb: String {
        switch self {
        case .talkingHead: return "You, to camera, with captions."
        case .greenScreen: return "You reacting over a post or screenshot."
        case .brollCutaway: return "You to camera, with b-roll cutting in on your key words."
        case .splitThree: return "3 panels — a different point in each, one after another."
        case .duetSplit: return "React to another clip, split above your talking head."
        case .faceless: return "Voiceover over b-roll — no on-camera."
        case .fastCuts: return "Rapid one-line cuts, high energy."
        }
    }
    var icon: String {
        switch self {
        case .talkingHead: return "person.fill"
        case .greenScreen: return "person.crop.rectangle"
        case .brollCutaway: return "film.stack"
        case .splitThree: return "rectangle.split.1x2"
        case .duetSplit: return "rectangle.split.1x2.fill"
        case .faceless: return "film"
        case .fastCuts: return "scissors"
        }
    }
    /// Fine-grained format recipes allowed within this style (mirrors the backend).
    var formats: [String] {
        switch self {
        case .talkingHead: return ["myth-buster", "listicle", "pov-story"]
        case .greenScreen: return ["green-screen"]
        case .brollCutaway: return ["myth-buster", "listicle", "do-this-not-that"]
        case .splitThree: return ["listicle", "do-this-not-that", "before-after"]
        case .duetSplit: return ["green-screen", "do-this-not-that"]
        case .faceless: return ["faceless", "broll-hook"]
        case .fastCuts: return ["listicle", "broll-hook", "myth-buster"]
        }
    }
}

// The submit-time CUT TREATMENT — how the editor should cut the take, distinct from
// the script style (what was filmed). Mirrors backend prompts.EDIT_FORMATS: the choice
// pins the engine style end to end (inference never overrides it).
// Marque is a TALKING-HEAD editor. Only the two talking-head treatments are offered;
// the recap (music/voiceover) formats were removed from the product. The backend still
// accepts recap_music/recap_voiceover for wire compatibility with older clients, but
// this app never sends them.
enum EditFormat: String, CaseIterable, Codable, Identifiable {
    case talkingHead = "talking_head"
    case talkingHeadBroll = "talking_head_broll"
    var id: String { rawValue }

    var label: String {
        switch self {
        case .talkingHead: return "Talking head"
        case .talkingHeadBroll: return "Talking head + B-roll"
        }
    }
    var blurb: String {
        switch self {
        case .talkingHead: return "Tight cuts, punch-ins, captions."
        case .talkingHeadBroll: return "Cutaways on your key words."
        }
    }
    var icon: String {
        switch self {
        case .talkingHead: return "person.wave.2"
        case .talkingHeadBroll: return "photo.on.rectangle.angled"
        }
    }
    /// The engine style this treatment renders with (mirrors the backend mapping).
    var engineStyle: String {
        switch self {
        case .talkingHead: return "talking_head"
        case .talkingHeadBroll: return "broll_cutaway"
        }
    }
    /// Best default given the script's style lane. Recap/faceless/fast-cuts scripts map to
    /// the closest talking-head treatment (b-roll-forward for faceless), since recap
    /// formats are no longer offered. Freestyle/empty → talking head.
    static func inferred(fromScriptStyle style: String) -> EditFormat {
        switch style {
        case "broll_cutaway", "faceless": return .talkingHeadBroll
        default: return .talkingHead
        }
    }

    /// UX-B1b: this treatment's default toggles — MUST MIRROR backend
    /// prompts.EDIT_FORMATS[*]["toggles"] exactly. The client SENDS these on submit and
    /// the backend honors explicit toggles over its own defaults, so drift here DOES
    /// change the edit.
    var defaultToggles: EditToggles {
        switch self {
        case .talkingHead:      return EditToggles(broll: false, punchIns: true,  music: false)
        case .talkingHeadBroll: return EditToggles(broll: true,  punchIns: true,  music: false)
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
    // UX-G2: WHY the feed picked this (the bandit arm's honest reason / pillar line).
    // Optional-with-default → Snapshot-safe both directions.
    var whyPicked: String = ""
    var createdAt: Date = Date()
}

enum ClipStatus: String, Codable { case draft, rendering, ready, scheduled, posted, failed }

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
    var thumbnailURL: String? = nil     // server-generated poster (thumbnail_url) for the render
    var captioned: Bool = false         // whether auto-captions were burned in
    var jobId: String? = nil            // backend clip-job ID for polling render status
    var lastError: String? = nil        // structured render error code when status == .failed
    // H5: the backend's more specific error_detail (e.g. the actual exception
    // text), paired with lastError's structured code. Optional-with-default —
    // safe for the Snapshot round-trip on existing installs. Lets
    // friendlyRenderError's fallback show something more useful than a fully
    // generic message for an error code it doesn't have copy for yet.
    var lastErrorDetail: String? = nil
    // WS4: the take's footage is still uploading from the device — the card shows an
    // "Uploading…" state so the creator returns to Library instantly instead of watching
    // a submit spinner. Cleared when the server job is created (status → .rendering) or
    // failed. Optional-with-default → Snapshot-safe on existing installs.
    var uploading: Bool = false
    // H10: non-fatal degradations the backend already tracked (F6 unresolved
    // b-roll, F13 safe-default-cut fallback) but nothing in iOS ever read —
    // the clip could be "ready" and playable while quietly missing a feature
    // the creator asked for, with zero visibility. Optional — Snapshot-safe.
    var warnings: [String]? = nil
    // I-6: "imported" for clips brought in from Photos (schedulable without filming on
    // Yunicorn). Optional-with-default → Snapshot-safe both directions.
    var source: String? = nil
    // UX-C2: locally cached copy of the SERVER render (downloaded on .ready), distinct
    // from localVideoPath which is ALWAYS the raw take. Optional-with-default →
    // Snapshot-safe both directions. Invalidated whenever remoteURL changes.
    var renderLocalPath: String? = nil
    // UX-D2: TRANSIENT low-res tweak-preview URL (the staged, uncommitted candidate
    // edit). Cleared on apply/discard/sheet-dismiss/remoteURL change; never written
    // with the snapshot save (set without save()). Optional-with-default → decode-safe.
    var previewURL: String? = nil
    // Server's remaining-time estimate ("Ready in ~N min" in the Library). Refreshed
    // by the poll loops; the countdown anchors at etaSetAt (when the estimate was
    // taken), NOT createdAt — the server value is already remaining-from-now.
    var etaSeconds: Int? = nil
    var etaSetAt: Date? = nil
    var createdAt: Date = Date()
}

// UX-C1: playback gating. The library bug: LocalVideoPlayer prefers `path` over
// `remoteURL`, and localVideoPath is ALWAYS the raw take — so a server-rendered clip
// (with captions/cuts/b-roll) silently played the unedited footage. These accessors are
// the single source of truth for "what file does this clip play/share/poster".
extension Clip {
    /// True when the backend produced a render for this clip: it went through a clip
    /// job (not imported from Photos) and the server handed back a render URL.
    var isServerRendered: Bool {
        jobId != nil && source != "imported" && !(remoteURL ?? "").isEmpty
    }

    /// Local file to play: for server-rendered clips ONLY the cached render qualifies
    /// (never the raw take); drafts/imported clips stay local-first on the raw file.
    var playbackLocalPath: String? {
        isServerRendered ? renderLocalPath : localVideoPath
    }

    /// Remote URL to stream when no acceptable local file exists.
    var playbackRemoteURL: String? {
        isServerRendered ? remoteURL : (localVideoPath == nil ? remoteURL : nil)
    }
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
    var mediaURL: String? = nil         // public render URL attached to the post (Post for Me media)
    var posted: Bool = false
    var metrics: PostMetrics? = nil     // populated post-publish from Insights
    // C-02/C-03: the honest last publish result (Optional-with-default → Snapshot decode-safe).
    var outcome: PublishOutcome? = nil
}

/// C-02: the truthful result of a publish attempt — replaces the `Bool` that let a silent
/// mock or a transport failure show the creator "Posted". Codable so it persists + drives UI.
enum PublishOutcome: Codable, Equatable, Hashable {
    case posted                     // genuinely published upstream
    case savedLocalNoAccounts       // nothing to post to — user must connect an account
    case queuedTransportFailure     // couldn't reach the backend — retry later
    case failed(String)             // upstream rejected it

    var didPost: Bool { self == .posted }
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

// MARK: - V3: Conversation (voice bubble + chat share one brain)

enum ChatRole: String, Codable { case user, assistant }

/// Rich message kinds: plain text, or a card payload attached by an intent.
enum ChatMessageKind: String, Codable {
    case text, scriptCard, videoAnalysis, dayPlan, clipEdit
}

/// W5: progress state for a "edit my clips" chat turn — the user attached
/// video(s) + an instruction and we run stitch → upload → analyze → edit → ready.
/// Persisted with the message so a relaunch mid-edit shows the last known stage.
struct ClipEditState: Codable, Hashable {
    enum Stage: String, Codable { case stitching, uploading, analyzing, editing, ready, failed }
    var stage: Stage = .stitching
    var clipCount: Int = 1
    var resultClipId: UUID? = nil           // set when stage == .ready
    var detail: String = ""                 // honest failure reason when stage == .failed
    // Recovery payload so a failed chat edit is retryable WITHOUT re-picking the videos:
    // stored once the footage is stitched, then a retry re-runs the pipeline from it.
    var footagePath: String = ""
    var instruction: String = ""
    var editFormat: String = ""
    var reactSourceURL: String = ""
    var config: [String: String]? = nil
    var toggles: EditToggles? = nil
    var retryable: Bool { stage == .failed && !footagePath.isEmpty }
}

struct ChatMessage: Codable, Hashable, Identifiable {
    var id = UUID()
    var role: ChatRole
    var content: String
    var kind: ChatMessageKind = .text
    var scripts: [Script]? = nil            // kind == .scriptCard
    var analysis: VideoAnalysis? = nil      // kind == .videoAnalysis
    var plan: DayPlan? = nil                // kind == .dayPlan
    var clipEdit: ClipEditState? = nil      // kind == .clipEdit
    var createdAt: Date = Date()
}

struct Conversation: Codable, Hashable, Identifiable {
    var id = UUID()
    var title: String = "New chat"
    var messages: [ChatMessage] = []
    var isVoiceNotes: Bool = false          // the pinned "Voice notes" thread from the Home bubble
    var updatedAt: Date = Date()
}

/// The client-held creator memory the AI builds from every conversation.
struct CreatorMemory: Codable, Hashable {
    var facts: [String] = []
    var perspective: [String] = []
    var angle: String = ""
    var ideas: [String] = []
    var preferences: [String] = []
    var updatedAt: Date = Date()

    var isEmpty: Bool {
        facts.isEmpty && perspective.isEmpty && angle.isEmpty && ideas.isEmpty && preferences.isEmpty
    }

    /// Apply server-emitted update ops with per-field caps (oldest evicted first).
    mutating func apply(_ updates: [MemoryUpdate]) {
        for u in updates {
            switch (u.op, u.field) {
            case ("set", "angle"): angle = u.value
            case ("add", "facts"): append(&facts, u.value, cap: 20)
            case ("add", "perspective"): append(&perspective, u.value, cap: 15)
            case ("add", "ideas"): append(&ideas, u.value, cap: 30)
            case ("add", "preferences"): append(&preferences, u.value, cap: 15)
            case ("remove", "facts"): facts.removeAll { $0 == u.value }
            case ("remove", "perspective"): perspective.removeAll { $0 == u.value }
            case ("remove", "ideas"): ideas.removeAll { $0 == u.value }
            case ("remove", "preferences"): preferences.removeAll { $0 == u.value }
            default: break
            }
        }
        if !updates.isEmpty { updatedAt = Date() }
    }

    private func append(_ list: inout [String], _ value: String, cap: Int) {
        guard !list.contains(value) else { return }
        list.append(value)
        if list.count > cap { list.removeFirst(list.count - cap) }
    }

    var asDictionary: [String: Any] {
        ["facts": facts, "perspective": perspective, "angle": angle,
         "ideas": ideas, "preferences": preferences]
    }
}

struct MemoryUpdate: Codable, Hashable {
    var op: String      // add | remove | set
    var field: String   // facts | perspective | ideas | preferences | angle
    var value: String
}

struct DayPlanBlock: Codable, Hashable, Identifiable {
    var id = UUID()
    var time: String
    var action: String
    var detail: String
    private enum CodingKeys: String, CodingKey { case time, action, detail }
}

struct DayPlan: Codable, Hashable {
    var blocks: [DayPlanBlock] = []
}

/// Result of pasting a video link into chat.
struct VideoAnalysis: Codable, Hashable {
    var url: String = ""
    var platform: String = ""
    var transcript: String = ""
    var hookAnalysis: String = ""
    var structureBeats: [String] = []
    var whyItWorks: String = ""
    var suggestions: [String] = []
    var yourVersion: Script? = nil
}

// MARK: - V3: Home feed (daily scripts + influencer reels to mimic)

struct ReelItem: Codable, Hashable, Identifiable {
    var id: String
    var creatorHandle: String
    var platform: String            // instagram | tiktok
    var title: String
    var hookText: String
    var transcript: String
    var thumbnailURL: String = ""
    var videoURL: String = ""
    var views: Int = 0
    var likes: Int = 0
    var whyTrending: String = ""
    var formatId: String = "myth-buster"
    var style: String = "talking_head"
    var fromWatched: Bool = false
    var transcribed: Bool = false   // real spoken transcript vs caption fallback
    // UX-A3 (all optional-with-default → Snapshot/decode-safe both directions):
    var editFormat: String = ""     // the edit TREATMENT this reel matches (EDIT_FORMATS key)
    var whyMatch: String = ""       // human "why this matches the treatment" line
    var sample: Bool = false        // true = curated exemplar, not a live scraped reel
    // Creator-helpful stats + linkout (honest public metrics — retention isn't available for
    // others' posts). comments/durationS/postedAt/profileURL come with the reel; pfpURL +
    // followerCount are fetched lazily per creator via GET /v1/reels/creator.
    var comments: Int = 0
    var durationS: Int = 0
    var postedAt: String = ""       // ISO-ish timestamp string
    var profileURL: String = ""     // instagram.com/handle or tiktok.com/@handle
    var pfpURL: String = ""
    var followerCount: Int = 0

    /// Interaction rate from public counts — the honest stand-in for the retention we can't see.
    var engagementRate: Double { views > 0 ? Double(likes + comments) / Double(views) : 0 }
}

/// A "match a vibe" style option: an editing style (backend theme bundle) illustrated by
/// a real, playable talking-head demo reel. Picking one sends themeId to the edit pipeline,
/// which actually drives the cut (apply_theme + retention passes) — the demo reel is
/// illustrative only, never mimicked.
struct StyleOption: Codable, Hashable, Identifiable {
    var id: String { themeId }
    let themeId: String
    let label: String
    let blurb: String
    var videoURL: String = ""
    var thumbnailURL: String = ""
    var handle: String = ""
    var sample: Bool = false
}

/// A B-ROLL STYLE option (full / balanced / minimal / none): how much cutaway coverage
/// the creator wants, illustrated by a real example reel demonstrating that style.
/// Picking one sends config.broll_coverage (+ the b-roll toggle for "none") to the edit
/// pipeline, which actually drives the cut. The demo is illustrative, never mimicked.
struct BrollStyleOption: Codable, Hashable, Identifiable {
    var id: String
    let label: String
    let blurb: String
    var videoURL: String = ""
    var thumbnailURL: String = ""
    var handle: String = ""
    var sample: Bool = false
}

enum SavedScriptSource: String, Codable {
    case daily, mimic, chat, custom, onboarding
    var label: String {
        switch self {
        case .daily: return "Daily pick"
        case .mimic: return "Mimic"
        case .chat: return "From chat"
        case .custom: return "Yours"
        case .onboarding: return "Starter"
        }
    }
}

/// A script the creator readied for filming (the Film-flow queue).
struct SavedScript: Codable, Hashable, Identifiable {
    var id = UUID()
    var script: Script
    var source: SavedScriptSource = .daily
    var mimickedFrom: String = ""   // "@handle" provenance when source == .mimic
    var addedAt: Date = Date()
    // W4: nil = active (in the Queue); non-nil = archived. Optional-with-default → old
    // Snapshot blobs decode fine (Codable does NOT default-fill missing keys).
    var archivedAt: Date? = nil
}

// MARK: - V3: Editing preferences (Settings → threaded into every AI edit)

enum CaptionStyle: String, CaseIterable, Codable, Identifiable {
    case clean, boldWord = "bold-word", karaoke
    var id: String { rawValue }
    var label: String {
        switch self {
        case .clean: return "Clean"
        case .boldWord: return "Bold word"
        case .karaoke: return "Karaoke"
        }
    }
}

enum FillerTrim: String, CaseIterable, Codable, Identifiable {
    case off, standard, aggressive
    var id: String { rawValue }
    var label: String {
        switch self {
        case .off: return "Off"
        case .standard: return "Standard"
        case .aggressive: return "Aggressive"
        }
    }
}

struct EditPrefs: Codable, Hashable {
    var autoCaptions: Bool = true
    var captionStyle: CaptionStyle = .clean
    var fillerTrim: FillerTrim = .standard

    var asDictionary: [String: Any] {
        ["auto_captions": autoCaptions, "caption_style": captionStyle.rawValue,
         "filler_trim": fillerTrim.rawValue]
    }
}

// MARK: - Chat coach persona + response length

/// Coach voice for the conversation engine — original archetypes (not real people),
/// in the same high-energy/blunt-hustle/tough-discipline vein the user asked for.
// Coach personas — the three modes a creator actually reaches for: a calm
// game-planner, an energy source, and someone who just tells them the truth.
// Case rawValues are FROZEN (persisted in Snapshot + sent to the backend as
// prompt-voice keys) — only the presentation evolves.
enum ChatPersona: String, CaseIterable, Codable, Identifiable {
    case machine, closer, sergeant
    var id: String { rawValue }
    var label: String {
        switch self {
        case .machine: return "The Strategist"
        case .closer: return "The Hype Coach"
        case .sergeant: return "The Straight Shooter"
        }
    }
    var tagline: String {
        switch self {
        case .machine: return "calm, data-first game plans"
        case .closer: return "all gas — momentum and wins"
        case .sergeant: return "the blunt truth, zero fluff"
        }
    }
    var icon: String {
        switch self {
        case .machine: return "target"
        case .closer: return "flame.fill"
        case .sergeant: return "scope"
        }
    }
    var glow: UInt {
        switch self {
        case .machine: return 0x3B82F6
        case .closer: return 0xFF6B35
        case .sergeant: return 0xB08D57
        }
    }
}

enum ChatResponseLength: String, CaseIterable, Codable, Identifiable {
    case concise, medium, detailed
    var id: String { rawValue }
    var label: String {
        switch self {
        case .concise: return "Concise"
        case .medium: return "Medium"
        case .detailed: return "Detailed"
        }
    }
    var hint: String {
        switch self {
        case .concise: return "one short sentence"
        case .medium: return "two or three sentences"
        case .detailed: return "long, specific, numbered"
        }
    }
}

/// One of the two "creators to watch" slots on the Profile.
struct WatchedCreator: Codable, Hashable, Identifiable {
    var id = UUID()
    var platform: SocialPlatform = .instagram
    var handle: String = ""
}

/// The AI-written Profile hero card ("what Marque knows about you").
struct BrandSummaryCard: Codable, Hashable {
    var summary: String = ""
    var traits: [String] = []
    var workingOn: String = ""
    var updatedAt: Date = Date()
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

// MARK: - Analyze-first edit brief (Loop H)
// Tolerant Codable mirrors of the backend's edit_brief + toggles. Every field is
// optional-with-default so a newer backend can add keys without breaking decode.

struct BriefHookCandidate: Codable, Hashable {
    var quote: String = ""
    var reason: String = ""
    var signal: String = ""
    var startFrame: Int = 0
    var endFrame: Int = 0

    enum CodingKeys: String, CodingKey {
        case quote, reason, signal, startFrame = "start_frame", endFrame = "end_frame"
    }

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        quote = (try? c.decodeIfPresent(String.self, forKey: .quote)) ?? ""
        reason = (try? c.decodeIfPresent(String.self, forKey: .reason)) ?? ""
        signal = (try? c.decodeIfPresent(String.self, forKey: .signal)) ?? ""
        startFrame = (try? c.decodeIfPresent(Int.self, forKey: .startFrame)) ?? 0
        endFrame = (try? c.decodeIfPresent(Int.self, forKey: .endFrame)) ?? 0
    }
}

struct BriefCutRegion: Codable, Hashable {
    var startFrame: Int = 0
    var endFrame: Int = 0
    var reason: String = "filler"
    var severity: String = "low"
    var quote: String = ""

    enum CodingKeys: String, CodingKey {
        case startFrame = "start_frame", endFrame = "end_frame", reason, severity, quote
    }

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        startFrame = (try? c.decodeIfPresent(Int.self, forKey: .startFrame)) ?? 0
        endFrame = (try? c.decodeIfPresent(Int.self, forKey: .endFrame)) ?? 0
        reason = (try? c.decodeIfPresent(String.self, forKey: .reason)) ?? "filler"
        severity = (try? c.decodeIfPresent(String.self, forKey: .severity)) ?? "low"
        quote = (try? c.decodeIfPresent(String.self, forKey: .quote)) ?? ""
    }
}

struct BriefPacing: Codable, Hashable {
    var energy: String = "medium"
    var read: String = ""

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        energy = (try? c.decodeIfPresent(String.self, forKey: .energy)) ?? "medium"
        read = (try? c.decodeIfPresent(String.self, forKey: .read)) ?? ""
    }
}

struct BriefInferred: Codable, Hashable {
    var style: String = ""
    var formatId: String = ""

    enum CodingKeys: String, CodingKey { case style, formatId = "format_id" }

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        style = (try? c.decodeIfPresent(String.self, forKey: .style)) ?? ""
        formatId = (try? c.decodeIfPresent(String.self, forKey: .formatId)) ?? ""
    }
}

struct EditBrief: Codable, Hashable {
    var videoType: String = "other"
    var throughLine: String = ""
    var strategy: String = "trim_only"
    var isScripted: Bool = false
    var hookCandidates: [BriefHookCandidate] = []
    var cutRegions: [BriefCutRegion] = []
    var pacing: BriefPacing? = nil
    var inferred: BriefInferred? = nil

    enum CodingKeys: String, CodingKey {
        case videoType = "video_type", throughLine = "through_line", strategy
        case isScripted = "is_scripted", hookCandidates = "hook_candidates"
        case cutRegions = "cut_regions", pacing, inferred
    }

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        videoType = (try? c.decodeIfPresent(String.self, forKey: .videoType)) ?? "other"
        throughLine = (try? c.decodeIfPresent(String.self, forKey: .throughLine)) ?? ""
        strategy = (try? c.decodeIfPresent(String.self, forKey: .strategy)) ?? "trim_only"
        isScripted = (try? c.decodeIfPresent(Bool.self, forKey: .isScripted)) ?? false
        hookCandidates = (try? c.decodeIfPresent([BriefHookCandidate].self, forKey: .hookCandidates)) ?? []
        cutRegions = (try? c.decodeIfPresent([BriefCutRegion].self, forKey: .cutRegions)) ?? []
        pacing = try? c.decodeIfPresent(BriefPacing.self, forKey: .pacing)
        inferred = try? c.decodeIfPresent(BriefInferred.self, forKey: .inferred)
    }

    /// Human-readable video type ("scripted_talking_head" → "Scripted talking head").
    var videoTypeLabel: String {
        videoType.replacingOccurrences(of: "_", with: " ").capitalized
    }
}

/// The three creator-editable edit toggles (captions + filler cuts are always-on).
struct EditToggles: Codable, Hashable {
    var broll: Bool = false
    var punchIns: Bool = true
    var music: Bool = false

    enum CodingKeys: String, CodingKey { case broll, punchIns = "punch_ins", music }

    init() {}
    init(broll: Bool, punchIns: Bool, music: Bool) {
        self.broll = broll; self.punchIns = punchIns; self.music = music
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        broll = (try? c.decodeIfPresent(Bool.self, forKey: .broll)) ?? false
        punchIns = (try? c.decodeIfPresent(Bool.self, forKey: .punchIns)) ?? true
        music = (try? c.decodeIfPresent(Bool.self, forKey: .music)) ?? false
    }
}

/// A7: one entry from GET /v1/themes — a style bundle (captions+grade+interrupts+
/// music pinned together so they never clash). Plain struct, hand-built from the
/// raw JSON dict (see BackendClient.fetchThemes) rather than Codable — matches
/// the AnalyzeJobResponse convention of defensive manual decoding elsewhere here.
struct ThemeChoice: Identifiable, Equatable {
    var id: String
    var label: String
    var blurb: String
    var defaultForFormats: [String] = []
}

/// Decoded response for the analyze-first pair (POST /v1/clips + GET /v1/clips/{id}).
struct AnalyzeJobResponse: Codable {
    /// UX-B1b: a clip in an auto_confirm create response — enough to track + poll.
    struct ClipStub: Codable {
        var clipId: String = ""
        var format: String = ""
        var status: String = ""
        enum CodingKeys: String, CodingKey { case clipId = "clip_id", format, status }
        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            clipId = (try? c.decodeIfPresent(String.self, forKey: .clipId)) ?? ""
            format = (try? c.decodeIfPresent(String.self, forKey: .format)) ?? ""
            status = (try? c.decodeIfPresent(String.self, forKey: .status)) ?? ""
        }
    }

    var jobId: String = ""
    var status: String = ""
    var mode: String = "mock"
    var editBrief: EditBrief? = nil
    var toggles: EditToggles? = nil
    var error: String? = nil
    // UX-B1b: present ONLY on auto_confirm responses (old backends omit it — the
    // client then falls back to the brief flow).
    var clips: [ClipStub]? = nil
    var etaSeconds: Int? = nil

    enum CodingKeys: String, CodingKey {
        case jobId = "job_id", status, mode, editBrief = "edit_brief", toggles, error, clips
        case etaSeconds = "eta_seconds"
    }

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        jobId = (try? c.decodeIfPresent(String.self, forKey: .jobId)) ?? ""
        status = (try? c.decodeIfPresent(String.self, forKey: .status)) ?? ""
        mode = (try? c.decodeIfPresent(String.self, forKey: .mode)) ?? "mock"
        editBrief = try? c.decodeIfPresent(EditBrief.self, forKey: .editBrief)
        toggles = try? c.decodeIfPresent(EditToggles.self, forKey: .toggles)
        error = try? c.decodeIfPresent(String.self, forKey: .error)
        clips = try? c.decodeIfPresent([ClipStub].self, forKey: .clips)
        etaSeconds = try? c.decodeIfPresent(Int.self, forKey: .etaSeconds)
    }
}
