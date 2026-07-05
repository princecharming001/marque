import Foundation
import Observation
import AuthenticationServices
import CryptoKit
import UIKit

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

    // MARK: Sign in with Apple (native id-token grant)

    /// The raw nonce for the in-flight SIWA request. Apple gets SHA256(nonce);
    /// Supabase must receive the RAW nonce — mixing them = silent auth failure.
    private(set) var siwaNonce: String = ""

    func prepareAppleRequest(_ request: ASAuthorizationAppleIDRequest) {
        siwaNonce = Self.randomNonce()
        request.requestedScopes = [.email]
        request.nonce = SHA256.hash(data: Data(siwaNonce.utf8))
            .map { String(format: "%02x", $0) }.joined()
    }

    func handleAppleCompletion(_ result: Result<ASAuthorization, Error>) async {
        switch result {
        case .failure(let err):
            // User-cancelled isn't an error worth surfacing.
            if (err as? ASAuthorizationError)?.code != .canceled {
                lastError = "Apple sign-in failed. Try again or use email."
            }
        case .success(let auth):
            guard let cred = auth.credential as? ASAuthorizationAppleIDCredential,
                  let tokenData = cred.identityToken,
                  let idToken = String(data: tokenData, encoding: .utf8) else {
                lastError = "Apple sign-in failed. Try again or use email."
                return
            }
            guard supabaseConfigured else {
                // Demo fallback: deterministic id from the stable Apple user id.
                persist(AuthState(userId: "apple-" + cred.user.suffix(16).lowercased(),
                                  email: cred.email ?? "apple@marque.app", mode: "demo"))
                return
            }
            await idTokenGrant(provider: "apple", idToken: idToken, nonce: siwaNonce,
                               fallbackEmail: cred.email)
        }
    }

    private func idTokenGrant(provider: String, idToken: String, nonce: String?,
                              fallbackEmail: String?) async {
        lastError = ""
        isWorking = true
        defer { isWorking = false }
        guard let url = URL(string: AppConfig.supabaseURL + "/auth/v1/token?grant_type=id_token") else {
            lastError = "Bad Supabase URL."; return
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 20
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(AppConfig.supabaseAnonKey, forHTTPHeaderField: "apikey")
        var body: [String: Any] = ["provider": provider, "id_token": idToken]
        if let nonce, !nonce.isEmpty { body["nonce"] = nonce }
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse else { lastError = "Network error."; return }
            let json = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
            guard (200..<300).contains(http.statusCode) else {
                lastError = (json["msg"] as? String) ?? (json["error_description"] as? String)
                    ?? "Sign-in failed (\(http.statusCode))."
                return
            }
            let user = (json["user"] as? [String: Any]) ?? [:]
            let userId = (user["id"] as? String) ?? UUID().uuidString
            let email = (user["email"] as? String) ?? fallbackEmail ?? ""
            persist(AuthState(userId: userId, email: email, mode: "supabase",
                              accessToken: json["access_token"] as? String ?? "",
                              refreshToken: json["refresh_token"] as? String ?? ""))
        } catch {
            lastError = "Couldn't reach the server. Check your connection."
        }
    }

    // MARK: Sign in with Google (Supabase OAuth web flow — no SDK)

    private var webAuthHolder: WebAuthContextHolder?

    func signInWithGoogle() async {
        lastError = ""
        guard supabaseConfigured else {
            persist(AuthState(userId: "google-" + UUID().uuidString.prefix(8).lowercased(),
                              email: "google@marque.app", mode: "demo"))
            return
        }
        guard let authURL = URL(string: AppConfig.supabaseURL
            + "/auth/v1/authorize?provider=google&redirect_to=marque://auth-callback") else {
            lastError = "Bad Supabase URL."; return
        }
        isWorking = true
        defer { isWorking = false }
        let holder = WebAuthContextHolder()
        webAuthHolder = holder
        do {
            let callback: URL = try await withCheckedThrowingContinuation { cont in
                let session = ASWebAuthenticationSession(url: authURL,
                                                         callbackURLScheme: "marque") { url, err in
                    if let url { cont.resume(returning: url) }
                    else { cont.resume(throwing: err ?? URLError(.userCancelledAuthentication)) }
                }
                session.presentationContextProvider = holder
                session.prefersEphemeralWebBrowserSession = false
                session.start()
            }
            // GoTrue implicit flow: tokens arrive in the URL fragment.
            guard let fragment = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.fragment else {
                lastError = "Google sign-in failed."; return
            }
            var params: [String: String] = [:]
            for pair in fragment.components(separatedBy: "&") {
                let kv = pair.components(separatedBy: "=")
                if kv.count == 2 { params[kv[0]] = kv[1].removingPercentEncoding ?? kv[1] }
            }
            guard let access = params["access_token"], !access.isEmpty else {
                lastError = (params["error_description"] ?? "Google sign-in failed.")
                    .replacingOccurrences(of: "+", with: " ")
                return
            }
            // Resolve the user id/email behind the token.
            var userReq = URLRequest(url: URL(string: AppConfig.supabaseURL + "/auth/v1/user")!)
            userReq.setValue(AppConfig.supabaseAnonKey, forHTTPHeaderField: "apikey")
            userReq.setValue("Bearer \(access)", forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: userReq)
            let user = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
            persist(AuthState(userId: (user["id"] as? String) ?? UUID().uuidString,
                              email: (user["email"] as? String) ?? "",
                              mode: "supabase",
                              accessToken: access,
                              refreshToken: params["refresh_token"] ?? ""))
        } catch {
            if (error as? ASWebAuthenticationSessionError)?.code != .canceledLogin {
                lastError = "Google sign-in failed. Try again or use email."
            }
        }
    }

    private static func randomNonce(length: Int = 32) -> String {
        let charset = Array("0123456789ABCDEFGHIJKLMNOPQRSTUVXYZabcdefghijklmnopqrstuvwxyz-._")
        var bytes = [UInt8](repeating: 0, count: length)
        _ = SecRandomCopyBytes(kSecRandomDefault, length, &bytes)
        return String(bytes.map { charset[Int($0) % charset.count] })
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

/// Presentation anchor for ASWebAuthenticationSession (Google OAuth web flow).
final class WebAuthContextHolder: NSObject, ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes
            .compactMap { ($0 as? UIWindowScene)?.keyWindow }
            .first ?? ASPresentationAnchor()
    }
}
