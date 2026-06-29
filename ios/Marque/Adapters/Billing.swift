import Foundation
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
