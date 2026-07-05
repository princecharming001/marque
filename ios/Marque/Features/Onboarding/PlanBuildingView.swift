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
            UnicornMascot(pose: .thinking, size: 150)

            VStack(alignment: .leading, spacing: Space.md) {
                ForEach(Array(Self.stages.enumerated()), id: \.offset) { i, label in
                    HStack(spacing: Space.md) {
                        if i < currentStage {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.system(size: 20))
                                .foregroundStyle(Palette.textPrimary)
                        } else if i == currentStage {
                            ProgressView().tint(Palette.ink)
                                .frame(width: 20, height: 20)
                        } else {
                            Circle().strokeBorder(Palette.hairline, lineWidth: 1.5)
                                .frame(width: 20, height: 20)
                        }
                        Text(label)
                            .font(AppFont.body)
                            .foregroundStyle(i <= currentStage ? Palette.textPrimary : Palette.textTertiary)
                    }
                    .animation(Motion.quick, value: currentStage)
                }
            }
            .frame(maxWidth: 300, alignment: .leading)

            if case .failed = store.starterScriptsState {
                Button {
                    store.retryStarterScripts()
                } label: {
                    Text("Something hiccuped — tap to retry")
                        .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                        .underline()
                }
                .accessibilityIdentifier("onboard.buildRetry")
            } else {
                Text("Feel free to close the app — I'll notify you when it's ready.")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    .multilineTextAlignment(.center)
            }
        }
        .onAppear { store.resumeStarterDigestIfNeeded() }
    }
}

// The plan-ready celebration content (the aha payoff).
struct PlanReadyView: View {
    @Environment(AppStore.self) private var store
    let onFinish: () -> Void

    var body: some View {
        VStack(spacing: Space.xl) {
            UnicornMascot(pose: .celebrate, size: 150)

            VStack(alignment: .leading, spacing: Space.md) {
                ForEach(store.scripts.prefix(3)) { script in
                    HStack(spacing: Space.md) {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 20))
                            .foregroundStyle(Palette.textPrimary)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(script.hook.text)
                                .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                                .lineLimit(2)
                            Text(script.pillarName)
                                .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            OnbPill(title: "Enter Marque") { onFinish() }
                .accessibilityIdentifier("onboard.finish")
        }
    }
}
