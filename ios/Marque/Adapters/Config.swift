import Foundation

// Consumer app: the device holds NO vendor API keys. The FastAPI backend owns every secret;
// the app only knows where the backend + Supabase live (from Info.plist / env, never user-entered).
enum AppConfig {
    static func value(env: String, defaults: String, plist: String? = nil) -> String {
        if !env.isEmpty, let v = ProcessInfo.processInfo.environment[env], !v.isEmpty { return v }
        if let v = UserDefaults.standard.string(forKey: defaults), !v.isEmpty { return v }
        if let p = plist, let v = Bundle.main.object(forInfoDictionaryKey: p) as? String, !v.isEmpty { return v }
        return ""
    }

    // Where the brain lives. Dev default points at a locally-run backend; production via Info.plist.
    static var backendBaseURL: String {
        let v = value(env: "MARQUE_BACKEND_URL", defaults: "backend.url", plist: "MARQUE_BACKEND_URL")
        return v.isEmpty ? "http://127.0.0.1:8000" : v
    }

    // The only Supabase values the untrusted client holds (the anon key is RLS-safe).
    // Every vendor key (Ayrshare, AssemblyAI, ElevenLabs/Cartesia, Remotion, R2, Apify)
    // lives exclusively on the backend — the app never sees or transmits one.
    static var supabaseURL: String { value(env: "SUPABASE_URL", defaults: "supabase.url", plist: "SUPABASE_URL") }
    static var supabaseAnonKey: String { value(env: "SUPABASE_ANON_KEY", defaults: "supabase.anonKey", plist: "SUPABASE_ANON_KEY") }
}

// Hosted legal + support pages. These URLs MUST resolve to real pages before App Store
// submission (Privacy Policy is required; Terms/Support are expected for a subscription app).
enum LegalURLs {
    static let privacy = URL(string: "https://marque.app/privacy")!
    static let terms = URL(string: "https://marque.app/terms")!
    static let support = URL(string: "https://marque.app/support")!
}
