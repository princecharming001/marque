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
                            // Action BEFORE dismiss — dismissal clears the caller's presented
                            // state (e.g. `editingPhrase = nil`), so a commit handler that runs
                            // after it reads nil and silently no-ops.
                            a.action(); dismiss()
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
    /// Off-state track color. Defaults suit a light surface; pass a lighter value on dark backgrounds.
    var offTrack: Color = Palette.textTertiary.opacity(0.35)
    var body: some View {
        Button { withAnimation(.spring(response: 0.28, dampingFraction: 0.7)) { isOn.toggle() } } label: {
            Capsule()
                .fill(isOn ? Palette.accent : offTrack)
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
        // Every call site wraps a MarqueSegmented instance with its own
        // .accessibilityIdentifier(...) (e.g. "settings.captionStyle"). Without this
        // .accessibilityElement(children: .contain), that external identifier leaks onto
        // every segment Button's own "segment.<opt>" identifier below — same root cause
        // as the cleanupPanel/mediaPanel fix. Fixing it once here covers every call site.
        .accessibilityElement(children: .contain)
    }
}

// MARK: - MarqueWheel — a single snapping value column, the building block of MarqueTimePicker.
// Fully custom (no UIKit wheel): a snap-scrolling LazyVStack with a center highlight band.

struct MarqueWheel<T: Hashable>: View {
    let items: [T]
    @Binding var selection: T
    let label: (T) -> String
    var width: CGFloat = 64

    private let rowH: CGFloat = 40
    // Starts nil (not seeded to `selection`): scrollPosition only scrolls on a *change*, so we
    // set it to `selection` in .onAppear to force the initial centering scroll.
    @State private var scrollID: T?

    var body: some View {
        ScrollView(.vertical, showsIndicators: false) {
            // Non-lazy so every row is measured before scrollPosition seeds — a LazyVStack
            // leaves far-down rows unmeasured, so the initial scroll to `selection` lands short.
            VStack(spacing: 0) {
                ForEach(items, id: \.self) { item in
                    Text(label(item))
                        .font(item == selection ? Typeface.display(22, .semibold) : AppFont.bodyL)
                        .foregroundStyle(item == selection ? Palette.textPrimary : Palette.textTertiary)
                        .frame(width: width, height: rowH)
                        .id(item)
                }
            }
            .scrollTargetLayout()
        }
        // Content margins let the first/last item scroll to the vertical center under the band.
        .contentMargins(.vertical, rowH * 1.5, for: .scrollContent)
        .frame(width: width, height: rowH * 4)
        .scrollPosition(id: $scrollID, anchor: .center)
        .scrollTargetBehavior(.viewAligned)
        .onAppear { scrollID = selection }
        .onChange(of: scrollID) { _, new in
            if let new, new != selection { selection = new }
        }
        .onChange(of: selection) { _, new in
            if scrollID != new { withAnimation(Motion.quick) { scrollID = new } }
        }
    }
}

// MARK: - MarqueTimePicker — branded replacement for `DatePicker(...).datePickerStyle(.wheel)`
// and the compact date+time picker. Hour / minute / AM-PM columns, optional date rail.

struct MarqueTimePicker: View {
    @Binding var time: Date
    /// When true, a horizontal day rail (today + next 13 days) sits above the time wheels.
    var includeDate: Bool = false

    private let cal = Calendar.current

    var body: some View {
        VStack(spacing: Space.md) {
            if includeDate { dateRail }

            ZStack {
                // Center highlight band the selected values sit inside.
                RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                    .fill(Palette.surfaceSunken)
                    .frame(height: 40)

                HStack(spacing: Space.xs) {
                    MarqueWheel(items: Array(1...12), selection: hourBinding) { String($0) }
                    Text(":").font(Typeface.display(22, .semibold)).foregroundStyle(Palette.textTertiary)
                    MarqueWheel(items: Array(0...59), selection: minuteBinding) { String(format: "%02d", $0) }
                    MarqueWheel(items: ["AM", "PM"], selection: periodBinding, label: { $0 }, width: 52)
                }
            }
            .frame(maxWidth: .infinity)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("marque.timePicker")
    }

    // MARK: Date rail

    private var dateRail: some View {
        let start = cal.startOfDay(for: time)
        let base = cal.startOfDay(for: Date())
        return ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Space.sm) {
                ForEach(0..<14, id: \.self) { offset in
                    let day = cal.date(byAdding: .day, value: offset, to: base) ?? base
                    let active = cal.isDate(day, inSameDayAs: start)
                    Button { setDate(day) } label: {
                        VStack(spacing: 2) {
                            Text(weekday(day)).font(AppFont.micro).tracking(Track.label)
                            Text(dayNum(day)).font(Typeface.display(18, .semibold))
                        }
                        .foregroundStyle(active ? Palette.onInk : Palette.textSecondary)
                        .frame(width: 48, height: 56)
                        .background(active ? Palette.ink : Palette.surfaceSunken)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 2)
        }
    }

    // MARK: Component bindings — read/write the shared `time` Date.

    private var hourBinding: Binding<Int> {
        Binding(get: {
            let h = cal.component(.hour, from: time) % 12
            return h == 0 ? 12 : h
        }, set: { newHour12 in
            let pm = cal.component(.hour, from: time) >= 12
            let h24 = (newHour12 % 12) + (pm ? 12 : 0)
            setComponents(hour: h24)
        })
    }

    private var minuteBinding: Binding<Int> {
        Binding(get: { cal.component(.minute, from: time) },
                set: { setComponents(minute: $0) })
    }

    private var periodBinding: Binding<String> {
        Binding(get: { cal.component(.hour, from: time) >= 12 ? "PM" : "AM" },
                set: { newPeriod in
            let h = cal.component(.hour, from: time)
            let wantPM = newPeriod == "PM"
            let isPM = h >= 12
            guard wantPM != isPM else { return }
            setComponents(hour: wantPM ? h + 12 : h - 12)
        })
    }

    private func setComponents(hour: Int? = nil, minute: Int? = nil) {
        var c = cal.dateComponents([.year, .month, .day, .hour, .minute], from: time)
        if let hour { c.hour = hour }
        if let minute { c.minute = minute }
        if let d = cal.date(from: c) { time = d }
    }

    private func setDate(_ day: Date) {
        var c = cal.dateComponents([.hour, .minute], from: time)
        let d = cal.dateComponents([.year, .month, .day], from: day)
        c.year = d.year; c.month = d.month; c.day = d.day
        if let combined = cal.date(from: c) { time = combined }
    }

    private func weekday(_ d: Date) -> String {
        let f = DateFormatter(); f.dateFormat = "EEE"; return f.string(from: d).uppercased()
    }
    private func dayNum(_ d: Date) -> String { String(cal.component(.day, from: d)) }
}
