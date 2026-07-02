import Foundation
import Observation

// Account layer for the post-onboarding auth wall. Two modes:
// - supabase: real email/password via GoTrue REST (when SUPABASE_URL + anon key configured)
// - demo:     keyless local account so dev/Maestro/demo flows pass without any backend
// Auth state persists separately from app state (surviving state resets) at marque.auth.v1.

struct AuthState: Codable, Equatable {
    var userId: String
    var email: String
    var mode: String            // "supabase" | "demo"
    var accessToken: String = ""
    var refreshToken: String = ""
}

@MainActor
@Observable
final class AuthManager {
    private(set) var state: AuthState? = nil
    var isAuthed: Bool { state != nil }
    var lastError: String = ""
    var isWorking = false

    private let saveKey = "marque.auth.v1"
    private var supabaseConfigured: Bool {
        !AppConfig.supabaseURL.isEmpty && !AppConfig.supabaseAnonKey.isEmpty
    }

    init() {
        if CommandLine.arguments.contains("-reset") {
            UserDefaults.standard.removeObject(forKey: saveKey)
        }
        if let data = UserDefaults.standard.data(forKey: saveKey),
           let s = try? JSONDecoder().decode(AuthState.self, from: data) {
            state = s
        }
        propagate()
    }

    // MARK: Email / password

    func createAccount(email: String, password: String) async {
        await authenticate(email: email, password: password, path: "/auth/v1/signup")
    }

    func signIn(email: String, password: String) async {
        await authenticate(email: email, password: password, path: "/auth/v1/token?grant_type=password")
    }

    private func authenticate(email: String, password: String, path: String) async {
        lastError = ""
        let trimmed = email.trimmingCharacters(in: .whitespaces).lowercased()
        guard trimmed.contains("@"), trimmed.contains(".") else { lastError = "Enter a valid email."; return }
        guard password.count >= 6 else { lastError = "Password must be at least 6 characters."; return }
        isWorking = true
        defer { isWorking = false }

        guard supabaseConfigured else {
            // Demo account: deterministic per-email id so re-sign-in restores the same creator.
            let id = "demo-" + (trimmed.data(using: .utf8)?.base64EncodedString().prefix(20).lowercased() ?? UUID().uuidString)
            persist(AuthState(userId: String(id), email: trimmed, mode: "demo"))
            return
        }
        guard let url = URL(string: AppConfig.supabaseURL + path) else { lastError = "Bad Supabase URL."; return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 20
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(AppConfig.supabaseAnonKey, forHTTPHeaderField: "apikey")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["email": trimmed, "password": password])
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse else { lastError = "Network error."; return }
            let json = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
            guard (200..<300).contains(http.statusCode) else {
                lastError = (json["msg"] as? String) ?? (json["error_description"] as? String) ?? "Sign-in failed (\(http.statusCode))."
                return
            }
            let user = (json["user"] as? [String: Any]) ?? json
            let userId = (user["id"] as? String) ?? UUID().uuidString
            persist(AuthState(userId: userId, email: trimmed, mode: "supabase",
                              accessToken: json["access_token"] as? String ?? "",
                              refreshToken: json["refresh_token"] as? String ?? ""))
        } catch {
            lastError = "Couldn't reach the server. Check your connection."
        }
    }

    /// Keyless demo path — one tap, no credentials (also the Maestro path).
    func continueAsDemo() {
        persist(AuthState(userId: "demo-" + UUID().uuidString.prefix(8).lowercased(), email: "demo@marque.app", mode: "demo"))
    }

    func signOut() {
        state = nil
        UserDefaults.standard.removeObject(forKey: saveKey)
        propagate()
    }

    // MARK: Wiring

    private func persist(_ s: AuthState) {
        state = s
        if let data = try? JSONEncoder().encode(s) {
            UserDefaults.standard.set(data, forKey: saveKey)
        }
        propagate()
    }

    /// Push identity into the shared backend client so every AI call is creator-scoped.
    private func propagate() {
        BackendClient.shared.creatorId = state?.userId ?? "default"
        BackendClient.shared.token = (state?.accessToken.isEmpty == false) ? state?.accessToken : nil
    }
}
