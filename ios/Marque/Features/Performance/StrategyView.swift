import SwiftUI

// P7.4 "Your Strategy" — the compiled brain, visible. Renders the strategy document's
// `## Section` blocks as cards plus the recent "what changed" updates, so the creator
// SEES the AI compounding on their content instead of taking it on faith.
struct StrategyView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var doc: BackendClient.StrategyDoc?
    @State private var loading = true

    private struct Section: Identifiable {
        let id = UUID()
        let title: String
        let body: String
    }

    /// Split "## Title\nbody…" markdown into (title, body) cards; anything before the
    /// first header renders as an untitled lead-in.
    private func sections(_ md: String) -> [Section] {
        var out: [Section] = []
        var title = ""
        var buf: [String] = []
        func flush() {
            let body = buf.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !body.isEmpty || !title.isEmpty { out.append(Section(title: title, body: body)) }
            buf = []
        }
        for line in md.components(separatedBy: "\n") {
            if line.hasPrefix("## ") {
                flush()
                title = String(line.dropFirst(3)).trimmingCharacters(in: .whitespaces)
            } else {
                buf.append(line)
            }
        }
        flush()
        return out
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.lg) {
                    if loading {
                        ProgressView().frame(maxWidth: .infinity).padding(.top, 80)
                    } else if let doc, !doc.isTemplate {
                        HStack {
                            Text("REVISION \(doc.revision)")
                                .font(AppFont.micro).tracking(Track.label)
                                .foregroundStyle(Palette.textTertiary)
                            Spacer()
                        }
                        ForEach(sections(doc.markdown)) { s in
                            VStack(alignment: .leading, spacing: Space.sm) {
                                if !s.title.isEmpty {
                                    Text(s.title).font(AppFont.headline)
                                        .foregroundStyle(Palette.textPrimary)
                                }
                                Text(s.body).font(AppFont.body)
                                    .foregroundStyle(Palette.textSecondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(Space.md)
                            .background(Palette.surfaceRaised)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                        }
                        if !doc.updates.isEmpty {
                            SectionLabel(text: "What changed recently", accent: Palette.accent)
                            VStack(alignment: .leading, spacing: Space.sm) {
                                ForEach(doc.updates.prefix(6), id: \.self) { u in
                                    HStack(alignment: .top, spacing: Space.sm) {
                                        Circle().fill(Palette.accent).frame(width: 5, height: 5)
                                            .padding(.top, 7)
                                        Text(u).font(AppFont.body)
                                            .foregroundStyle(Palette.textSecondary)
                                    }
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(Space.md)
                            .background(Palette.surfaceRaised)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                        }
                    } else {
                        // Both a nil doc and the deterministic placeholder (doc.isTemplate)
                        // land here — one short "not ready yet" line, matching the app's
                        // other terse empty states, instead of rendering the generic
                        // template as if it were a real compiled strategy.
                        EmptyStateView(icon: "brain",
                                       title: "Not ready yet",
                                       message: "Film and analyze a few clips — your strategy builds from them.")
                            .padding(.top, 60)
                    }
                }
                .padding(Space.xl)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Your Strategy")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .task {
            doc = await store.backend.fetchStrategy()
            loading = false
        }
    }
}
