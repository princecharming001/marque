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
                // Journey-style timeline: the current version first (marked with a check),
                // then every earlier snapshot as a tappable timeline row.
                VStack(alignment: .leading, spacing: Space.stack) {
                    if let script {
                        currentRow(script)
                        ForEach(script.versionHistory) { version in
                            versionRow(version, scriptId: script.id)
                        }
                        if script.versionHistory.isEmpty {
                            Text("No earlier versions yet — refine or edit this script and they'll show up here.")
                                .font(AppFont.bodyText).foregroundStyle(Palette.textSecondary)
                                .multilineTextAlignment(.center)
                                .fixedSize(horizontal: false, vertical: true)
                                .frame(maxWidth: .infinity)
                                .padding(.top, Space.xl)
                        }
                    }
                }
                .screenPadding().padding(.top, Space.sm).padding(.bottom, Space.xl)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Version history")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Palette.canvas, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("version history.").font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                        .accessibilityAddTraits(.isHeader)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                }
            }
        }
        .tint(Palette.textPrimary)
    }

    private func currentRow(_ script: Script) -> some View {
        HStack(alignment: .top, spacing: Space.md) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 20, weight: .regular))
                .foregroundStyle(Palette.textPrimary)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: Space.xs) {
                DSEyebrow(text: "CURRENT", color: Palette.textPrimary)
                Text(script.title.isEmpty ? script.hook.text : script.title)
                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(script.body).font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                    .lineLimit(3)
            }
        }
        .padding(Space.rowPad)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous).fill(Palette.surface))
        .overlay(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
            .strokeBorder(Palette.textPrimary, lineWidth: 1.5))
        .accessibilityElement(children: .combine)
    }

    private func versionRow(_ version: ScriptVersion, scriptId: UUID) -> some View {
        Button {
            store.revertScript(scriptId, to: version)
            dismiss()
        } label: {
            HStack(alignment: .top, spacing: Space.md) {
                Image(systemName: "arrow.uturn.backward")
                    .font(.system(size: 18, weight: .regular))
                    .foregroundStyle(Palette.textPrimary)
                    .frame(width: 28)
                    .padding(.top, 1)
                VStack(alignment: .leading, spacing: Space.xs) {
                    HStack(alignment: .firstTextBaseline, spacing: Space.sm) {
                        DSEyebrow(text: version.label)
                            .lineLimit(1)
                        Spacer(minLength: Space.sm)
                        Text(version.savedAt, style: .relative)
                            .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                            .lineLimit(1)
                    }
                    Text(version.title.isEmpty ? version.hook.text : version.title)
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(version.body).font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                        .multilineTextAlignment(.leading)
                        .lineLimit(2)
                }
            }
            .padding(Space.rowPad)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous).fill(Palette.surface))
            .contentShape(Rectangle())
        }
        .buttonStyle(PressableStyle())
        .accessibilityIdentifier("script.revertVersion")
    }
}
