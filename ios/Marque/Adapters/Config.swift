import Foundation

// Centralized key/secret resolution. Order: process env -> on-device UserDefaults -> Info.plist.
// DEV convenience so "paste a key -> service goes live". In production these live in the FastAPI
// backend and the app only points each adapter's base URL at it (see DECISIONS.md).

enum AppConfig {
    static func value(env: String, defaults: String, plist: String? = nil) -> String {
        if !env.isEmpty, let v = ProcessInfo.processInfo.environment[env], !v.isEmpty { return v }
        if let v = UserDefaults.standard.string(forKey: defaults), !v.isEmpty { return v }
        if let p = plist, let v = Bundle.main.object(forInfoDictionaryKey: p) as? String, !v.isEmpty { return v }
        return ""
    }
    static func set(_ value: String, defaults: String) {
        let t = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { UserDefaults.standard.removeObject(forKey: defaults) }
        else { UserDefaults.standard.set(t, forKey: defaults) }
    }

    // Anthropic
    static var anthropicKey: String { value(env: "ANTHROPIC_API_KEY", defaults: "anthropic.key", plist: "ANTHROPIC_API_KEY") }
    static var anthropicBaseURL: String {
        let v = value(env: "ANTHROPIC_BASE_URL", defaults: "anthropic.baseURL")
        return v.isEmpty ? "https://api.anthropic.com" : v
    }
    static var useLiveAI: Bool { !anthropicKey.isEmpty }

    // Other services — read by their adapters as they come online.
    static var supabaseURL: String { value(env: "SUPABASE_URL", defaults: "supabase.url") }
    static var supabaseAnonKey: String { value(env: "SUPABASE_ANON_KEY", defaults: "supabase.anonKey") }
    static var ayrshareKey: String { value(env: "AYRSHARE_KEY", defaults: "ayrshare.key") }
    static var assemblyAIKey: String { value(env: "ASSEMBLYAI_KEY", defaults: "assemblyai.key") }
    static var shotstackKey: String { value(env: "SHOTSTACK_KEY", defaults: "shotstack.key") }
    static var revenueCatKey: String { value(env: "REVENUECAT_KEY", defaults: "revenuecat.key") }
}

// The keys a user can paste in Settings.
struct ServiceKeyField: Identifiable {
    let id: String          // UserDefaults key
    let title: String
    let placeholder: String
    var isSet: Bool { !AppConfig.value(env: "", defaults: id).isEmpty }
}

enum ServiceCatalog {
    static let fields: [ServiceKeyField] = [
        .init(id: "anthropic.key", title: "Anthropic (Claude)", placeholder: "sk-ant-…"),
        .init(id: "supabase.url", title: "Supabase URL", placeholder: "https://xxx.supabase.co"),
        .init(id: "supabase.anonKey", title: "Supabase anon key", placeholder: "eyJ…"),
        .init(id: "ayrshare.key", title: "Ayrshare (publishing)", placeholder: "API key"),
        .init(id: "assemblyai.key", title: "AssemblyAI (transcription)", placeholder: "API key"),
        .init(id: "shotstack.key", title: "Shotstack (render)", placeholder: "API key"),
        .init(id: "revenuecat.key", title: "RevenueCat (subscriptions)", placeholder: "API key"),
    ]
}
