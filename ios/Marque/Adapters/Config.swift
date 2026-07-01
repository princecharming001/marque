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
    static var supabaseURL: String { value(env: "SUPABASE_URL", defaults: "supabase.url", plist: "SUPABASE_URL") }
    static var supabaseAnonKey: String { value(env: "SUPABASE_ANON_KEY", defaults: "supabase.anonKey", plist: "SUPABASE_ANON_KEY") }

    // Phase-2 publish/clip adapters still read these from env/Info.plist only (no in-app entry);
    // they move fully server-side when those pipelines land.
    static var ayrshareKey: String { value(env: "AYRSHARE_KEY", defaults: "ayrshare.key") }
    static var assemblyAIKey: String { value(env: "ASSEMBLYAI_KEY", defaults: "assemblyai.key") }
    static var shotstackKey: String { value(env: "SHOTSTACK_KEY", defaults: "shotstack.key") }
}

// Hosted legal + support pages. These URLs MUST resolve to real pages before App Store
// submission (Privacy Policy is required; Terms/Support are expected for a subscription app).
enum LegalURLs {
    static let privacy = URL(string: "https://marque.app/privacy")!
    static let terms = URL(string: "https://marque.app/terms")!
    static let support = URL(string: "https://marque.app/support")!
}
