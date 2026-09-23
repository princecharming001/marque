import SwiftUI

extension ClipGroup {
    /// SwiftUI color for this group's marker. Lives here rather than on the model because
    /// Models.swift is deliberately Foundation-only (it's decoded off the main actor).
    /// The mono redesign retires per-group hues: every group marker is primary ink, and a
    /// group is identified by its name and a folder glyph instead (`displayColorHex` stays
    /// on the model untouched).
    var displayColor: Color { Palette.textPrimary }
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
                VStack(alignment: .leading, spacing: Space.lg) {
                    Text("^[\(clipIDs.count) clip](inflect: true) selected. A clip can sit in as many groups as you like.")
                        .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, Space.rowPad)

                    if store.clipGroups.isEmpty {
                        emptyState
                    } else {
                        // Stoic checklist rows in one grouped card.
                        DSGroup {
                            ForEach(store.clipGroups) { g in
                                groupRow(g)
                                if g.id != store.clipGroups.last?.id {
                                    DSRowDivider(inset: 56)
                                }
                            }
                        }
                    }

                    createRow

                    if anyGrouped {
                        // Destructive as a text link: black glyph + wording, no red.
                        Button {
                            store.clearGroups(clipIDs)
                        } label: {
                            Label("Remove from all groups", systemImage: "folder.badge.minus")
                                .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                                .frame(maxWidth: .infinity).frame(minHeight: 44)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(PressableStyle(dim: 0.5))
                        .accessibilityIdentifier("group.removeAll")
                    }
                }
                .padding(.horizontal, Space.screenH).padding(.vertical, Space.md)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Groups")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Palette.canvas, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text(dsTitle("Groups"))
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                        .accessibilityAddTraits(.isHeader)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                }
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
                // Groups are told apart by name + folder glyph (no per-group hue).
                Image(systemName: state == .none ? "folder" : "folder.fill")
                    .font(.system(size: 18, weight: .regular))
                    .foregroundStyle(g.displayColor)
                    .frame(width: 24)
                    .accessibilityHidden(true)
                Text(g.name)
                    .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary).lineLimit(1)
                Spacer(minLength: Space.sm)
                triStateMark(state)
                    .accessibilityHidden(true)
            }
            .padding(.horizontal, Space.rowPad)
            .frame(minHeight: 52)
            .contentShape(Rectangle())
        }
        .buttonStyle(DSRowPressStyle())
        .accessibilityValue(state == .all ? "In group" : (state == .some ? "Some selected clips in group" : "Not in group"))
        .accessibilityIdentifier("group.row")
    }

    private func glyph(_ state: Membership) -> String {
        switch state {
        case .all:  return "checkmark"
        case .some: return "minus"
        case .none: return ""
        }
    }

    /// Stoic checklist mark, tri-state: outline circle (none), outline circle + minus
    /// (partial), ink circle + check (all). Inversion carries "on", not color.
    private func triStateMark(_ state: Membership) -> some View {
        ZStack {
            Circle().fill(state == .all ? Palette.ink : .clear)
            Circle().strokeBorder(state == .all ? .clear
                                  : (state == .some ? Palette.textPrimary : Palette.hairline),
                                  lineWidth: 1.5)
            if state != .none {
                Image(systemName: glyph(state))
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(state == .all ? Palette.onInk : Palette.textPrimary)
            }
        }
        .frame(width: 22, height: 22)
        .animation(Motion.quick, value: state == .all)
    }

    private var emptyState: some View {
        // Stoic outline (empty) card.
        VStack(spacing: Space.xs) {
            Image(systemName: "folder")
                .font(.system(size: 22, weight: .regular))
                .foregroundStyle(Palette.textSecondary)
                .padding(.bottom, Space.xs)
            Text("No groups yet")
                .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
            Text("Groups are just folders for your library, a client, a campaign, a month. Name one below and these clips go straight in.")
                .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity)
        .dsCard(.outline, radius: Radius.group)
    }

    private var createRow: some View {
        // Inline "New group" text-field row (Stoic form row: surface, 52pt).
        HStack(spacing: Space.md) {
            Image(systemName: "folder.badge.plus")
                .font(.system(size: 18, weight: .regular))
                .foregroundStyle(Palette.textPrimary)
                .frame(width: 24)
                .accessibilityHidden(true)
            TextField("New group", text: $newGroupName)
                .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                .tint(Palette.textPrimary)
                .textInputAutocapitalization(.words)
                .focused($newGroupFocused)
                .submitLabel(.done)
                .onSubmit(create)
                .accessibilityIdentifier("group.newName")
            Button("Create", action: create)
                .buttonStyle(DSCapsuleStyle(kind: .primary, height: 36))
                .disabled(!canCreate)
                .accessibilityIdentifier("group.create")
        }
        .padding(.leading, Space.rowPad).padding(.trailing, Space.sm)
        .frame(minHeight: 52)
        .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous).fill(Palette.surface))
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
