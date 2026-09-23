import SwiftUI

// The non-blocking aha: staged check-off lines while the digest job runs, with an
// explicit "you can close the app" note (a local notification fires when done).
// Replaces the old blocking spinner.
struct PlanBuildingView: View {
    @Environment(AppStore.self) private var store

    static let stages = [
        "Reading your answers",
        "Studying your reels",
        "Designing your pillars",
        "Writing your first 3 scripts",
    ]

    private var currentStage: Int {
        if case .running(let s) = store.starterScriptsState { return s }
        return Self.stages.count
    }

    var body: some View {
        VStack(spacing: Space.xl) {
            UnicornMascot(pose: .thinking, size: 110)

            // Stoic "preparing" rows: earlier stages checked, current spinning,
            // later ones muted.
            VStack(spacing: Space.sm) {
                ForEach(Array(Self.stages.enumerated()), id: \.offset) { i, label in
                    DSChecklistRow(title: label,
                                   state: i < currentStage ? .done : (i == currentStage ? .active : .pending))
                }
            }
            .frame(maxWidth: .infinity)
            .animation(Motion.quick, value: currentStage)

            if case .failed = store.starterScriptsState {
                Button {
                    store.retryStarterScripts()
                } label: {
                    Text("Something hiccuped, tap to retry")
                }
                .buttonStyle(.dsLink)
                .accessibilityIdentifier("onboard.buildRetry")
            } else {
                Text("Feel free to close the app. I'll notify you when it's ready.")
                    .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .onAppear { store.resumeStarterDigestIfNeeded() }
    }
}

// The plan-ready celebration content (the aha payoff).
struct PlanReadyView: View {
    @Environment(AppStore.self) private var store
    let onFinish: () -> Void

    /// ≤6-word heading; fall back to the hook (one line) only if the model gave no title.
    private func conciseTitle(_ s: Script) -> String {
        s.title.isEmpty ? s.hook.text : s.title
    }

    /// A descriptor that actually differs per script — the one-line summary if present,
    /// otherwise a "Format · 30s" label. Never the pillar name (identical across the three).
    private func subtitle(_ s: Script) -> String {
        if !s.summary.isEmpty { return s.summary }
        return "\(Catalog.format(s.formatId).name) · \(s.targetSeconds)s"
    }

    var body: some View {
        VStack(spacing: Space.lg) {
            UnicornMascot(pose: .celebrate, size: 100)
                .staggerReveal(0)

            // The building checklist, all checked: the transition reads as completion.
            VStack(spacing: Space.sm) {
                ForEach(Array(store.scripts.prefix(3).enumerated()), id: \.element.id) { i, script in
                    HStack(spacing: Space.md) {
                        VStack(alignment: .leading, spacing: 2) {
                            // Concise heading (script.title is the ≤6-word label); fall back to
                            // the hook only if the model didn't supply one, capped to one line.
                            Text(conciseTitle(script))
                                .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                                .lineLimit(1)
                            // Per-script descriptor, NOT the pillar name (which is identical for
                            // all three) — summary if present, else a format · length label.
                            Text(subtitle(script))
                                .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                                .lineLimit(1)
                        }
                        Spacer(minLength: Space.sm)
                        DSCheckmark(isOn: true)
                    }
                    .padding(.horizontal, Space.rowPad)
                    .padding(.vertical, Space.sm)
                    .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                        .fill(Palette.surface))
                    .accessibilityElement(children: .combine)
                    .staggerReveal(i + 1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            OnbPill(title: "Enter Yunicorn") { onFinish() }
                .accessibilityIdentifier("onboard.finish")
                .staggerReveal(4)
        }
    }
}
