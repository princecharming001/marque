import SwiftUI
import Network

final class NetworkMonitor: ObservableObject {
    @Published var isOnline = true
    private let monitor = NWPathMonitor()
    private let q = DispatchQueue(label: "marque.network")

    init() {
        monitor.pathUpdateHandler = { path in
            DispatchQueue.main.async { self.isOnline = path.status == .satisfied }
        }
        monitor.start(queue: q)
    }
}

// Calm, single-line offline banner (never red — the design language stays warm).
struct OfflineBanner: View {
    var body: some View {
        Text("You're offline — we'll sync when you're back.")
            .font(AppFont.caption).foregroundStyle(Palette.night)
            .frame(maxWidth: .infinity)
            .padding(.vertical, Space.sm)
            .background(Palette.gold)
            .accessibilityIdentifier("offline.banner")
    }
}
