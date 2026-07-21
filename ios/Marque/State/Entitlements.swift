import SwiftUI

// Build 54 — the MOCK entitlement layer behind the Yunicorn paywall. There is no real
// StoreKit purchase yet: the paywall's CTA flips this flag locally, so the entire gated
// experience — watermark on free exports, the Pro surface in Settings, future gate
// points — is testable end to end before IAP lands. When StoreKit arrives, `isPro`
// becomes derived from the transaction listener and everything downstream stays as-is.
@MainActor              // build 55: mutations drive SwiftUI observation — keep them on main
@Observable final class Entitlements {
    static let shared = Entitlements()
    private static let key = "yunicorn.entitlement.pro"

    /// True = Yunicorn Pro (no watermark, pro badge). Persisted across launches.
    var isPro: Bool {
        didSet { UserDefaults.standard.set(isPro, forKey: Self.key) }
    }

    private init() {
        isPro = UserDefaults.standard.bool(forKey: Self.key)
    }

    /// The mock "purchase": a short beat so the CTA's Processing state reads real,
    /// then the flag flips. Replaced by a StoreKit purchase when IAP lands.
    func mockPurchase() async {
        try? await Task.sleep(nanoseconds: 900_000_000)
        isPro = true
    }

    /// Mock restore — same flag, Apple-required affordance on the paywall.
    func mockRestore() async {
        try? await Task.sleep(nanoseconds: 500_000_000)
        isPro = true
    }

    func revoke() { isPro = false }   // DEBUG/dev drawer
}
