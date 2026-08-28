import SwiftUI

/// Lists every snapshot a script has picked up (refines, manual edits, hook swaps) so a
/// creator who doesn't like the latest rewrite can go back — tapping a row reverts to it
/// (see AppStore.revertScript). The version being left is itself snapshotted, so reverting
/// is never a one-way trip.
struct ScriptVersionHistorySheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let scriptId: UUID

    private var script: Script? { store.scripts.first { $0.id == scriptId } }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.md) {
                    if let script {
                        currentRow(script)
                        ForEach(script.versionHistory) { version in
                            versionRow(version, scriptId: script.id)
                        }
                        if script.versionHistory.isEmpty {
                            Text("No earlier versions yet — refine or edit this script and they'll show up here.")
                                .font(AppFont.body).foregroundStyle(Palette.textTertiary)
                                .padding(.top, Space.sm)
                        }
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Version history")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
    }

    private func currentRow(_ script: Script) -> some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            HStack(spacing: Space.sm) {
                Text("CURRENT").font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.accent)
                Spacer()
            }
            Text(script.title.isEmpty ? script.hook.text : script.title)
                .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
            Text(script.body).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                .lineLimit(3)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .marqueCard()
        .overlay(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous)
            .strokeBorder(Palette.accent.opacity(0.4), lineWidth: 1.5))
    }

    private func versionRow(_ version: ScriptVersion, scriptId: UUID) -> some View {
        Button {
            store.revertScript(scriptId, to: version)
            dismiss()
        } label: {
            VStack(alignment: .leading, spacing: Space.xs) {
                HStack(spacing: Space.sm) {
                    Text(version.label).font(AppFont.micro).tracking(Track.label)
                        .foregroundStyle(Palette.textTertiary)
                    Spacer()
                    Text(version.savedAt, style: .relative)
                        .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                    Image(systemName: "arrow.uturn.backward")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Palette.accent)
                }
                Text(version.title.isEmpty ? version.hook.text : version.title)
                    .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                Text(version.body).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    .lineLimit(2)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .marqueCard()
        }
        .buttonStyle(PressableStyle())
        .accessibilityIdentifier("script.revertVersion")
    }
}
