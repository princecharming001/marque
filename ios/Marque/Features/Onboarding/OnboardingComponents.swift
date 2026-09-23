import SwiftUI

// Shared onboarding pieces (docs/ONBOARDING-DESIGN.md §3).

// MARK: - OptionCard — the stacked answer capsule (DESIGN.md §5 option buttons)

/// Full-width tappable answer: glyph + title + optional subtitle on a capsule.
/// Unselected = surface + hairline; selected = inverted (ink fill, onInk text).
/// No scale, no hue: selection is the tone flip. `icon` is an OnbIcon-* asset
/// name; until the clay icon set lands it falls back to the paired SF Symbol.
struct OptionCard: View {
    let icon: String
    var sfFallback: String = "circle"
    let title: String
    var subtitle: String? = nil
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.sm + Space.xs) {
                iconBadge
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(AppFont.bodyText.weight(.semibold))
                        .foregroundStyle(selected ? Palette.onInk : Palette.textPrimary)
                        .multilineTextAlignment(.leading)
                        .lineLimit(2).minimumScaleFactor(0.85)
                    if let subtitle {
                        Text(subtitle)
                            .font(AppFont.caption)
                            .foregroundStyle(selected ? Palette.onInk.opacity(0.75) : Palette.textSecondary)
                            .multilineTextAlignment(.leading)
                    }
                }
                Spacer(minLength: 0)
                if selected {
                    Image(systemName: "checkmark")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(Palette.onInk)
                        .transition(.opacity)
                }
            }
            .padding(.leading, Space.lg)
            .padding(.trailing, Space.lg + Space.xs)
            .padding(.vertical, Space.sm)
            .frame(maxWidth: .infinity, minHeight: subtitle == nil ? 56 : 64, alignment: .leading)
            .background(Capsule().fill(selected ? Palette.ink : Palette.surface))
            .overlay(Capsule().strokeBorder(selected ? .clear : Palette.hairline, lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(PressableStyle(dim: 0.85))
        .animation(Motion.quick, value: selected)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    @ViewBuilder private var iconBadge: some View {
        if UIImage(named: icon) != nil {
            Image(icon)
                .resizable().scaledToFit()
                .frame(width: 28, height: 28)
        } else {
            // Monochrome SF fallback until the clay icon set is generated.
            // Never-blank guard: an invalid/misspelled SF Symbol name silently
            // renders nothing, which read as a missing icon in review — fall
            // back to a plain circle glyph so a bad symbol name is never invisible.
            Image(systemName: UIImage(systemName: sfFallback) != nil ? sfFallback : "circle")
                .font(.system(size: 18, weight: .regular))
                .foregroundStyle(selected ? Palette.onInk : Palette.textPrimary)
                .frame(width: 28, height: 28)
        }
    }
}

// MARK: - Primary capsule CTA (DESIGN.md §5: content-sized, centered, 56pt)

/// The onboarding CTA. Same id/enabled contract as before; drawn as the DS
/// primary capsule (ink fill, onInk label; disabled = sunken fill, tertiary label).
struct OnbPill: View {
    let title: String
    var enabled: Bool = true
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Text(title)
        }
        .buttonStyle(.dsPrimary)
        .disabled(!enabled)
    }
}
