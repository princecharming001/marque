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

// Calm, single-line offline banner — neutral hairline bar, never alarming.
struct OfflineBanner: View {
    var body: some View {
        Text("You're offline — we'll sync when you're back.")
            .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
            .frame(maxWidth: .infinity)
            .padding(.vertical, Space.sm)
            .background(Palette.surfaceSunken)
            .overlay(Rectangle().fill(Palette.hairline).frame(height: 1), alignment: .bottom)
            .accessibilityIdentifier("offline.banner")
    }
}
