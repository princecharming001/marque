import Foundation
import Observation
import StoreKit

// Subscriptions via StoreKit 2 (no RevenueCat SDK in-app to avoid an external dependency;
// RevenueCat would sit server-side). MockBilling keeps the publish gate open in dev/sim so the
// flow isn't blocked while there's no StoreKit configuration.
protocol Billing {
    var isPro: Bool { get }
}

struct MockBilling: Billing {
    // Dev default: unlocked. Toggle "dev.isPro" in UserDefaults to exercise the gated path.
    var isPro: Bool { UserDefaults.standard.object(forKey: "dev.isPro") as? Bool ?? true }
}

// V3: the hard subscription wall (landing → onboarding → auth → THIS → app).
// Real StoreKit 2 against Marque.storekit in the sim; a DEBUG dev-unlock keeps
// keyless dev + Maestro flowing. Entitlements are StoreKit's source of truth.
@MainActor
@Observable
final class SubscriptionManager {
    private(set) var products: [Product] = []
    private(set) var entitled = false
    private(set) var devUnlocked = UserDefaults.standard.bool(forKey: "dev.subscribed")
    var isWorking = false
    var lastError = ""

    var isSubscribed: Bool { entitled || devUnlocked }
    var monthly: Product? { products.first { $0.id == "com.marque.pro.monthly" } }

    func load() async {
        products = (try? await Product.products(for: StoreKitBilling.productIDs)) ?? []
        await refresh()
    }

    func refresh() async {
        var ok = false
        for await result in Transaction.currentEntitlements {
            if case .verified = result { ok = true }
        }
        entitled = ok
    }

    func purchase() async {
        lastError = ""
        guard let product = monthly ?? products.first else {
            lastError = "Products unavailable right now. Try Restore, or check your connection."
            return
        }
        isWorking = true
        defer { isWorking = false }
        do {
            let result = try await product.purchase()
            if case .success(let verification) = result, case .verified(let transaction) = verification {
                await transaction.finish()
                await refresh()
            }
        } catch {
            lastError = "Purchase didn't complete."
        }
    }

    func restore() async {
        isWorking = true
        try? await StoreKit.AppStore.sync()
        await refresh()
        isWorking = false
    }

    /// DEBUG/Maestro bypass — the wall stays real in release builds.
    func devContinue() {
        UserDefaults.standard.set(true, forKey: "dev.subscribed")
        devUnlocked = true
    }

    func resetDev() {
        UserDefaults.standard.removeObject(forKey: "dev.subscribed")
        devUnlocked = false
    }
}

// Real StoreKit 2 entitlement check + purchase. Used once products are configured (Marque.storekit
// for the sim, App Store Connect for release).
@MainActor
final class StoreKitBilling: ObservableObject {
    static let productIDs = ["com.marque.pro.monthly", "com.marque.studio.monthly"]

    @Published var products: [Product] = []
    @Published private(set) var entitled = false
    var isPro: Bool { entitled }

    func load() async {
        products = (try? await Product.products(for: StoreKitBilling.productIDs)) ?? []
        await refresh()
    }

    func refresh() async {
        var ok = false
        for await result in Transaction.currentEntitlements {
            if case .verified = result { ok = true }
        }
        entitled = ok
    }

    func purchase(_ product: Product) async {
        guard let result = try? await product.purchase() else { return }
        if case .success(let verification) = result, case .verified(let transaction) = verification {
            await transaction.finish()
            await refresh()
        }
    }
}
