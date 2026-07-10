import SwiftUI

// MARK: - Branded modal surfaces — replace Apple-native `.alert` / `.confirmationDialog` /
// `Toggle` / segmented `Picker`, which look like stock iOS and break the editorial aesthetic.
// A MarqueDialog is a centered card over a dimmed scrim: serif title, quiet message, and
// PrimaryButton/GhostButton actions — matching the app's card + Playfair language everywhere.

/// One action in a branded dialog. `.cancel` renders as a ghost button; `.destructive` tints
/// the confirm red; `.default` is the ink primary.
struct MarqueDialogAction: Identifiable {
    enum Kind { case primary, destructive, cancel }
    let id = UUID()
    let label: String
    let kind: Kind
    let action: () -> Void
    init(_ label: String, kind: Kind = .primary, action: @escaping () -> Void = {}) {
        self.label = label; self.kind = kind; self.action = action
    }
}

/// The visual card. Actions stack vertically (a11y-friendly, matches iOS spacing but branded).
struct MarqueDialogCard: View {
    let title: String
    var message: String? = nil
    let actions: [MarqueDialogAction]
    var dismiss: () -> Void
    /// Optional editorial content (e.g. a text field for input dialogs) between message + actions.
    var content: AnyView? = nil

    var body: some View {
        ZStack {
            Color.black.opacity(0.45).ignoresSafeArea()
                .onTapGesture { if actions.contains(where: { $0.kind == .cancel }) { dismiss() } }
            VStack(alignment: .leading, spacing: Space.md) {
                Text(title)
                    .font(Typeface.display(22, .semibold)).tracking(Track.title)
                    .foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                if let message {
                    Text(message)
                        .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                        .lineSpacing(3).fixedSize(horizontal: false, vertical: true)
                }
                if let content { content }
                VStack(spacing: Space.sm) {
                    ForEach(actions) { a in
                        Button {
                            dismiss(); a.action()
                        } label: { actionLabel(a) }
                        .buttonStyle(PressableStyle(dim: a.kind == .cancel ? 0.7 : 1))
                        .accessibilityIdentifier("dialog.\(a.label.lowercased().replacingOccurrences(of: " ", with: ""))")
                    }
                }
                .padding(.top, Space.xs)
            }
            .padding(Space.xl)
            .frame(maxWidth: 340)
            .background(Palette.surface)
            .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1))
            .shadow(color: .black.opacity(0.18), radius: 30, x: 0, y: 12)
            .padding(Space.xl)
        }
        .transition(.opacity)
    }

    @ViewBuilder private func actionLabel(_ a: MarqueDialogAction) -> some View {
        switch a.kind {
        case .primary:
            Text(a.label).font(AppFont.headline).foregroundStyle(Palette.onInk)
                .frame(maxWidth: .infinity).frame(height: 50)
                .background(Palette.ink).clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        case .destructive:
            Text(a.label).font(AppFont.headline).foregroundStyle(.white)
                .frame(maxWidth: .infinity).frame(height: 50)
                .background(Palette.critical).clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        case .cancel:
            Text(a.label).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                .frame(maxWidth: .infinity).frame(height: 50)
                .background(Palette.surfaceRaised).clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1))
        }
    }
}

extension View {
    /// Branded replacement for a confirm-style `.alert` / `.confirmationDialog` (one action + Cancel).
    func marqueConfirm(_ isPresented: Binding<Bool>, title: String, message: String? = nil,
                       confirm: String, destructive: Bool = false, cancel: String = "Cancel",
                       onConfirm: @escaping () -> Void) -> some View {
        overlay {
            if isPresented.wrappedValue {
                MarqueDialogCard(title: title, message: message, actions: [
                    MarqueDialogAction(confirm, kind: destructive ? .destructive : .primary, action: onConfirm),
                    MarqueDialogAction(cancel, kind: .cancel),
                ], dismiss: { withAnimation(.easeOut(duration: 0.18)) { isPresented.wrappedValue = false } })
                .zIndex(999)
            }
        }
        .animation(.easeOut(duration: 0.18), value: isPresented.wrappedValue)
    }

    /// Branded multi-option action sheet (e.g. "Add to chat"): a list of choices + Cancel.
    func marqueActions(_ isPresented: Binding<Bool>, title: String,
                       actions: [MarqueDialogAction]) -> some View {
        overlay {
            if isPresented.wrappedValue {
                MarqueDialogCard(title: title, actions: actions + [MarqueDialogAction("Cancel", kind: .cancel)],
                                 dismiss: { withAnimation(.easeOut(duration: 0.18)) { isPresented.wrappedValue = false } })
                .zIndex(999)
            }
        }
        .animation(.easeOut(duration: 0.18), value: isPresented.wrappedValue)
    }

    /// Branded single-line text-input dialog (replaces `TextField`-inside-`.alert`).
    func marqueInput(_ isPresented: Binding<Bool>, title: String, placeholder: String,
                     text: Binding<String>, confirm: String = "Save",
                     onConfirm: @escaping () -> Void) -> some View {
        overlay {
            if isPresented.wrappedValue {
                MarqueDialogCard(
                    title: title,
                    actions: [
                        MarqueDialogAction(confirm, kind: .primary, action: onConfirm),
                        MarqueDialogAction("Cancel", kind: .cancel),
                    ],
                    dismiss: { withAnimation(.easeOut(duration: 0.18)) { isPresented.wrappedValue = false } },
                    content: AnyView(
                        TextField(placeholder, text: text)
                            .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                            .padding(.horizontal, Space.md).frame(height: 50)
                            .background(Palette.surfaceRaised)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                                .strokeBorder(Palette.hairline, lineWidth: 1))
                            .accessibilityIdentifier("dialog.input")
                    )
                )
                .zIndex(999)
            }
        }
        .animation(.easeOut(duration: 0.18), value: isPresented.wrappedValue)
    }
}

// MARK: - MarqueToggle — branded switch (Capsule track + knob), replaces native Toggle.

struct MarqueToggle: View {
    @Binding var isOn: Bool
    var body: some View {
        Button { withAnimation(.spring(response: 0.28, dampingFraction: 0.7)) { isOn.toggle() } } label: {
            Capsule()
                .fill(isOn ? Palette.accent : Palette.textTertiary.opacity(0.35))
                .frame(width: 46, height: 28)
                .overlay(alignment: isOn ? .trailing : .leading) {
                    Circle().fill(.white).frame(width: 22, height: 22)
                        .shadow(color: .black.opacity(0.15), radius: 2, x: 0, y: 1)
                        .padding(3)
                }
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isOn ? [.isSelected] : [])
    }
}

/// A settings/prefs row: label + subtitle on the left, a MarqueToggle on the right.
struct MarqueToggleRow: View {
    let title: String
    var subtitle: String? = nil
    @Binding var isOn: Bool
    var body: some View {
        HStack(spacing: Space.md) {
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                if let subtitle {
                    Text(subtitle).font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: Space.md)
            MarqueToggle(isOn: $isOn)
        }
    }
}

// MARK: - MarqueSegmented — branded pill segmented control, replaces `.pickerStyle(.segmented)`.

struct MarqueSegmented: View {
    let options: [String]
    @Binding var index: Int
    var body: some View {
        HStack(spacing: 0) {
            ForEach(Array(options.enumerated()), id: \.offset) { i, opt in
                let active = i == index
                Button { withAnimation(Motion.quick) { index = i } } label: {
                    Text(opt)
                        .font(active ? AppFont.callout.weight(.semibold) : AppFont.callout)
                        .foregroundStyle(active ? Palette.onInk : Palette.textSecondary)
                        .frame(maxWidth: .infinity).frame(height: 34)
                        .background(active ? Palette.ink : Color.clear)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("segment.\(opt.lowercased().replacingOccurrences(of: " ", with: ""))")
            }
        }
        .padding(3)
        .background(Palette.surfaceSunken)
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
    }
}
