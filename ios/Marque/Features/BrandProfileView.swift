import SwiftUI

// "What Marque knows about you" — the editable Brand Graph (06-brand-graph.md).
struct BrandProfileView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var newNonNeg = ""

    var body: some View {
        @Bindable var store = store
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    Text("What Marque knows about you")
                        .font(AppFont.displayM).foregroundStyle(Palette.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)

                    field("Niche", text: $store.brand.niche, id: "profile.niche")
                    field("What you do", text: $store.brand.whatYouDo, id: "profile.whatYouDo")
                    field("Who you serve", text: $store.brand.audience, id: "profile.audience")
                    field("Known for", text: $store.brand.knownFor, id: "profile.knownFor")

                    VStack(alignment: .leading, spacing: Space.md) {
                        SectionTitle(text: "Voice")
                        slider("Funny ⟷ Serious", $store.brand.voice.funnyToSerious)
                        slider("Polished ⟷ Raw", $store.brand.voice.polishedToRaw)
                        slider("Teacher ⟷ Peer", $store.brand.voice.teacherToPeer)
                    }

                    VStack(alignment: .leading, spacing: Space.sm) {
                        SectionTitle(text: "Never say")
                        if !store.brand.nonNegotiables.isEmpty {
                            ScrollView(.horizontal, showsIndicators: false) {
                                HStack(spacing: Space.sm) {
                                    ForEach(store.brand.nonNegotiables, id: \.self) { item in
                                        Button { store.brand.nonNegotiables.removeAll { $0 == item } } label: {
                                            HStack(spacing: 4) {
                                                Text(item); Image(systemName: "xmark").font(.system(size: 9))
                                            }
                                            .font(AppFont.callout).foregroundStyle(Palette.textPrimary)
                                            .padding(.horizontal, Space.md).padding(.vertical, Space.sm)
                                            .background(Palette.surfaceRaised).clipShape(Capsule())
                                            .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
                                        }.buttonStyle(.plain)
                                    }
                                }
                            }
                        }
                        HStack {
                            TextField("Add a no-go word or topic", text: $newNonNeg)
                                .font(AppFont.body).foregroundStyle(Palette.textPrimary)
                                .accessibilityIdentifier("profile.addNonNeg")
                            Button("Add") {
                                let t = newNonNeg.trimmingCharacters(in: .whitespaces)
                                if !t.isEmpty { store.brand.nonNegotiables.append(t); newNonNeg = "" }
                            }.foregroundStyle(Palette.goldDeep)
                        }
                        .padding(.vertical, Space.sm)
                        .overlay(alignment: .bottom) { Rectangle().fill(Palette.hairline).frame(height: 1) }
                    }

                    VStack(alignment: .leading, spacing: Space.sm) {
                        SectionTitle(text: "Pillars")
                        ForEach(store.pillars) { p in
                            HStack(spacing: Space.sm) {
                                Circle().fill(Color(hex: p.colorHex)).frame(width: 10, height: 10)
                                Text(p.name).font(AppFont.body).foregroundStyle(Palette.textPrimary)
                            }
                        }
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.surface.ignoresSafeArea())
            .navigationTitle("Brand")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { store.save(); dismiss() } } }
        }
    }

    private func field(_ label: String, text: Binding<String>, id: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            SectionTitle(text: label)
            TextField(label, text: text)
                .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                .padding(.vertical, Space.sm)
                .overlay(alignment: .bottom) { Rectangle().fill(Palette.hairline).frame(height: 1) }
                .accessibilityIdentifier(id)
        }
    }
    private func slider(_ label: String, _ value: Binding<Double>) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(AppFont.callout).foregroundStyle(Palette.textSecondary)
            Slider(value: value).tint(Palette.gold)
        }
    }
}
