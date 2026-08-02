import SwiftUI

extension ClipGroup {
    /// SwiftUI accent for this group's dot. Lives here rather than on the model because
    /// Models.swift is deliberately Foundation-only (it's decoded off the main actor).
    var displayColor: Color { Color(hex: displayColorHex) }
}

/// Build 61 — the Library's "put these clips in a group" surface.
///
/// WHY A SHEET AND NOT THE OLD MENU
/// Grouping shipped in build 59 behind a `Menu` on the bulk bar that listed only the
/// groups that ALREADY existed. On a fresh library that's zero rows, so the menu opened
/// to a lone "New group…" item and the owner reported the feature as missing — the UI
/// never showed what grouping was or that clips could be in more than one. A sheet can
/// hold the whole story at once: every group, this selection's membership in each, and a
/// create row that's visible before the first group exists.
///
/// Membership is tri-state because the selection is a SET: a group can hold all of the
/// selected clips, some of them, or none. Tapping a partial row adds the stragglers
/// (never removes) — the destructive direction always requires a full row first.
struct GroupAssignSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let clipIDs: Set<UUID>

    @State private var newGroupName = ""
    @FocusState private var newGroupFocused: Bool

    private enum Membership { case all, some, none }

    private func membership(_ group: UUID) -> Membership {
        let selected = store.clips.filter { clipIDs.contains($0.id) }
        guard !selected.isEmpty else { return .none }
        let n = selected.filter { $0.memberGroupIds.contains(group) }.count
        return n == 0 ? .none : (n == selected.count ? .all : .some)
    }

    /// Whether "Remove from all groups" has anything to do.
    private var anyGrouped: Bool {
        store.clips.contains { clipIDs.contains($0.id) && !$0.memberGroupIds.isEmpty }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.md) {
                    Text("^[\(clipIDs.count) clip](inflect: true) selected. A clip can sit in as many groups as you like.")
                        .font(AppFont.callout).foregroundStyle(Palette.textSecondary)

                    if store.clipGroups.isEmpty {
                        emptyState
                    } else {
                        VStack(spacing: 0) {
                            ForEach(store.clipGroups) { g in
                                groupRow(g)
                                if g.id != store.clipGroups.last?.id {
                                    Divider().overlay(Palette.hairline).padding(.leading, 40)
                                }
                            }
                        }
                        .background(Palette.surfaceRaised)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                            .strokeBorder(Palette.hairline, lineWidth: 1))
                    }

                    createRow

                    if anyGrouped {
                        Button {
                            store.clearGroups(clipIDs)
                        } label: {
                            Label("Remove from all groups", systemImage: "folder.badge.minus")
                                .font(AppFont.callout).foregroundStyle(Palette.critical)
                        }
                        .buttonStyle(.plain)
                        .padding(.top, Space.sm)
                        .accessibilityIdentifier("group.removeAll")
                    }
                }
                .padding(Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Groups")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
        .presentationDetents([.medium, .large])
        .accessibilityIdentifier("library.groupAssign")
    }

    // MARK: rows

    private func groupRow(_ g: ClipGroup) -> some View {
        let state = membership(g.id)
        return Button {
            // Only a FULLY-checked row un-files clips; a partial row fills in the rest.
            store.setGroupMembership(clipIDs, group: g.id, isMember: state != .all)
        } label: {
            HStack(spacing: Space.md) {
                Circle().fill(g.displayColor).frame(width: 12, height: 12)
                Text(g.name)
                    .font(AppFont.body).foregroundStyle(Palette.textPrimary).lineLimit(1)
                Spacer()
                Image(systemName: glyph(state))
                    .font(.system(size: 19, weight: .semibold))
                    .foregroundStyle(state == .none ? Palette.textTertiary : Palette.accent)
            }
            .padding(.horizontal, Space.md).padding(.vertical, 13)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("group.row")
    }

    private func glyph(_ state: Membership) -> String {
        switch state {
        case .all:  return "checkmark.circle.fill"
        case .some: return "minus.circle"
        case .none: return "circle"
        }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Text("No groups yet")
                .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
            Text("Groups are just folders for your library — a client, a campaign, a month. Name one below and these clips go straight in.")
                .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Space.md)
        .background(Palette.surfaceSunken, in: RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
    }

    private var createRow: some View {
        HStack(spacing: Space.md) {
            Circle()
                .strokeBorder(Palette.textTertiary, style: StrokeStyle(lineWidth: 1.5, dash: [3, 3]))
                .frame(width: 12, height: 12)
            TextField("New group", text: $newGroupName)
                .font(AppFont.body).foregroundStyle(Palette.textPrimary)
                .textInputAutocapitalization(.words)
                .focused($newGroupFocused)
                .submitLabel(.done)
                .onSubmit(create)
                .accessibilityIdentifier("group.newName")
            Button("Create", action: create)
                .font(AppFont.callout.weight(.semibold))
                .foregroundStyle(canCreate ? Palette.accent : Palette.textTertiary)
                .buttonStyle(.plain).disabled(!canCreate)
                .accessibilityIdentifier("group.create")
        }
        .padding(.horizontal, Space.md).padding(.vertical, 13)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
    }

    private var canCreate: Bool {
        !newGroupName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func create() {
        guard canCreate else { return }
        // createClipGroup reuses a same-named group rather than minting a duplicate, so
        // "create" on an existing name reads as "add to that one" — which is what the
        // creator meant.
        let g = store.createClipGroup(newGroupName)
        store.setGroupMembership(clipIDs, group: g.id, isMember: true)
        newGroupName = ""
        newGroupFocused = false
    }
}
