import SwiftUI

struct SettingsView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var showPaywall = false

    var body: some View {
        @Bindable var store = store
        return NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    // AI engine status
                    HStack {
                        Text("AI engine").font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                        Spacer()
                        Text(store.aiMode).font(AppFont.callout)
                            .foregroundStyle(store.aiMode == "Claude" ? Palette.positive : Palette.textSecondary)
                    }
                    .marqueCard(padding: Space.md)

                    // Upgrade
                    Button { showPaywall = true } label: {
                        HStack {
                            Text("Upgrade to Pro").font(AppFont.headline).foregroundStyle(Palette.onInk)
                            Spacer()
                            Image(systemName: "sparkles").foregroundStyle(Palette.onInk)
                        }
                        .padding(Space.md)
                        .background(Palette.ink)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("settings.upgrade")

                    // Content styles
                    VStack(alignment: .leading, spacing: Space.md) {
                        SectionTitle(text: "Content styles")
                        Text("Which kinds of video should Marque write? Each gets its own script style.")
                            .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        StyleSelectionView(selected: $store.brand.preferredStyles)
                    }
                    .onChange(of: store.brand.preferredStyles) { _, _ in store.save() }

                    // Danger zone
                    VStack(alignment: .leading, spacing: Space.md) {
                        SectionTitle(text: "Account")
                        GhostButton(title: "Reset app to first run", systemImage: "arrow.counterclockwise") {
                            store.resetAll(); dismiss()
                        }
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.surface.ignoresSafeArea())
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
            .sheet(isPresented: $showPaywall) { PaywallView() }
        }
    }

}
