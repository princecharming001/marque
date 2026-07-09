import Foundation

// Best-effort remote persistence mirror via Supabase REST (PostgREST). Active when both
// supabase.url and supabase.anonKey are set; otherwise a no-op (local UserDefaults stays the
// source of truth). Mirrors the whole app snapshot to an `app_state` row keyed by device.
// Untestable here without a Supabase project. Production: RLS-scoped per authenticated user.
protocol RemotePersistence {
    func push(_ data: Data) async
    func pull() async -> Data?
}

struct SupabaseStore: RemotePersistence {
    private let table = "app_state"
    private var configured: Bool { !AppConfig.supabaseURL.isEmpty && !AppConfig.supabaseAnonKey.isEmpty }

    // C-13: key the mirror by the signed-in creator's userId so a reinstall +
    // sign-in restores their state. The per-install deviceKey (which regenerates
    // on every fresh install, making restore impossible) is only the fallback for
    // a not-yet-authed session.
    private var rowKey: String {
        if let data = UserDefaults.standard.data(forKey: "marque.auth.v1"),
           let auth = try? JSONDecoder().decode(AuthState.self, from: data),
           !auth.userId.isEmpty {
            return auth.userId
        }
        return deviceKey
    }

    private var deviceKey: String {
        if let k = UserDefaults.standard.string(forKey: "device.key") { return k }
        let k = UUID().uuidString
        UserDefaults.standard.set(k, forKey: "device.key")
        return k
    }

    private func request(_ path: String, method: String) -> URLRequest? {
        guard configured, let url = URL(string: AppConfig.supabaseURL + "/rest/v1/" + path) else { return nil }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.timeoutInterval = 20
        req.setValue(AppConfig.supabaseAnonKey, forHTTPHeaderField: "apikey")
        req.setValue("Bearer \(AppConfig.supabaseAnonKey)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        return req
    }

    func push(_ data: Data) async {
        guard var req = request(table, method: "POST") else { return }
        req.setValue("resolution=merge-duplicates", forHTTPHeaderField: "Prefer")
        let snapshot = (try? JSONSerialization.jsonObject(with: data)) ?? [:]
        let row: [String: Any] = ["id": rowKey, "snapshot": snapshot]
        req.httpBody = try? JSONSerialization.data(withJSONObject: [row])
        _ = try? await URLSession.shared.data(for: req)
    }

    func pull() async -> Data? {
        guard let req = request("\(table)?id=eq.\(rowKey)&select=snapshot", method: "GET") else { return nil }
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let http = resp as? HTTPURLResponse, http.statusCode == 200 else { return nil }
        guard let arr = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]],
              let snap = arr.first?["snapshot"] else { return nil }
        return try? JSONSerialization.data(withJSONObject: snap)
    }
}
