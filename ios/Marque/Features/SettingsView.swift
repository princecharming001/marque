import SwiftUI

struct SettingsView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var values: [String: String] = [:]
    @State private var saved = false
    @State private var showPaywall = false

    var body: some View {
        NavigationStack {
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
                            Text("Upgrade to Pro").font(AppFont.headline).foregroundStyle(Palette.night)
                            Spacer()
                            Image(systemName: "sparkles").foregroundStyle(Palette.night)
                        }
                        .padding(Space.md)
                        .background(Palette.gold)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("settings.upgrade")

                    // API keys
                    VStack(alignment: .leading, spacing: Space.md) {
                        SectionTitle(text: "API keys")
                        Text("Paste a key to activate that service. Stored on-device only; in production these live in the backend.")
                            .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        ForEach(ServiceCatalog.fields) { f in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(f.title).font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                                    Spacer()
                                    if isSet(f) {
                                        Image(systemName: "checkmark.circle.fill")
                                            .foregroundStyle(Palette.positive).font(.system(size: 13))
                                    }
                                }
                                SecureField(f.placeholder, text: binding(f.id))
                                    .font(AppFont.body).foregroundStyle(Palette.textPrimary)
                                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                                    .padding(.vertical, Space.sm)
                                    .overlay(alignment: .bottom) { Rectangle().fill(Palette.hairline).frame(height: 1) }
                                    .accessibilityIdentifier("settings.\(f.id)")
                            }
                        }
                        PrimaryButton(title: saved ? "Saved ✓" : "Save keys") { saveKeys() }
                            .accessibilityIdentifier("settings.save")
                    }

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

    private func isSet(_ f: ServiceKeyField) -> Bool {
        if let v = values[f.id] { return !v.isEmpty }
        return f.isSet
    }
    private func binding(_ id: String) -> Binding<String> {
        Binding(get: { values[id] ?? "" }, set: { values[id] = $0; saved = false })
    }
    private func saveKeys() {
        for (k, v) in values { AppConfig.set(v, defaults: k) }
        saved = true
    }
}
