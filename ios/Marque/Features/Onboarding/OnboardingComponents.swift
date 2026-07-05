import SwiftUI

// Shared onboarding pieces (docs/ONBOARDING-DESIGN.md §3).

// MARK: - OptionCard — the Alma/Cal-AI answer card

/// Large tappable answer card: icon badge + title + optional subtitle.
/// Selected = ink border + slight scale. NO green ring, NO tinted circles.
/// `icon` is an OnbIcon-* asset name; until the clay icon set lands it falls
/// back to the paired SF Symbol rendered monochrome ink.
struct OptionCard: View {
    let icon: String
    var sfFallback: String = "circle"
    let title: String
    var subtitle: String? = nil
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.md) {
                iconBadge
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                        .multilineTextAlignment(.leading)
                    if let subtitle {
                        Text(subtitle)
                            .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                            .multilineTextAlignment(.leading)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(Space.md)
            .frame(maxWidth: .infinity, minHeight: 72, alignment: .leading)
            .background(Palette.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous)
                .strokeBorder(selected ? Palette.ink : Palette.hairline,
                              lineWidth: selected ? 1.5 : 1))
            .shadow(color: .black.opacity(0.05), radius: 8, y: 4)
            .scaleEffect(selected ? 1.02 : 1)
            .contentShape(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous))
        }
        .buttonStyle(PressableStyle(dim: 0.85))
        .animation(Motion.spring, value: selected)
    }

    @ViewBuilder private var iconBadge: some View {
        if UIImage(named: icon) != nil {
            Image(icon)
                .resizable().scaledToFit()
                .frame(width: 44, height: 44)
        } else {
            // Interim monochrome fallback until the clay icon set is generated.
            Image(systemName: sfFallback)
                .font(.system(size: 19, weight: .medium))
                .foregroundStyle(Palette.textPrimary)
                .frame(width: 44, height: 44)
                .background(Circle().fill(Palette.surfaceSunken))
        }
    }
}

// MARK: - Ink pill CTA (56pt, the only CTA style in the flow)

struct OnbPill: View {
    let title: String
    var enabled: Bool = true
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Text(title)
                .font(AppFont.headline)
                .foregroundStyle(enabled ? Palette.onInk : Color(hex: 0xA4A29D))
                .frame(maxWidth: .infinity).frame(height: 56)
                .background(enabled ? Palette.ink : Color(hex: 0xDAD9D6))
                .clipShape(Capsule())
                .shadow(color: .black.opacity(enabled ? 0.15 : 0), radius: 14, y: 6)
        }
        .buttonStyle(PressableStyle())
        .disabled(!enabled)
    }
}

// MARK: - Freeform text step content

/// Centered display-size text entry used by name/niche/about/knownFor.
struct FreeformField: View {
    let placeholder: String
    @Binding var text: String
    var fontSize: CGFloat = 34
    var capitalization: TextInputAutocapitalization = .sentences
    var focused: FocusState<Bool>.Binding? = nil
    var accessibilityID: String

    var body: some View {
        HStack(spacing: Space.sm) {
            Group {
                if let focused {
                    TextField(placeholder, text: $text, axis: .vertical)
                        .focused(focused)
                } else {
                    TextField(placeholder, text: $text, axis: .vertical)
                }
            }
            .font(Typeface.display(fontSize)).tracking(-0.6)
            .foregroundStyle(Palette.textPrimary)
            .tint(Palette.accent)
            .textInputAutocapitalization(capitalization)
            .lineLimit(1...3)
            .multilineTextAlignment(.center)
            .accessibilityIdentifier(accessibilityID)

            if !text.isEmpty {
                Button {
                    text = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 18))
                        .foregroundStyle(Palette.textTertiary)
                }
                .transition(.opacity)
            }
        }
        .animation(Motion.quick, value: text.isEmpty)
    }
}
