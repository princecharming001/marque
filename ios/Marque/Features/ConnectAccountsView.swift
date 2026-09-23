import SwiftUI
import AuthenticationServices

// Link Instagram + TikTok accounts for real posting via OAuth through Post for Me — the
// linked account carries an spc_ id we publish to.
struct ConnectAccountsView: View {
    @Environment(AppStore.self) private var store
    @State private var linking: String?          // platform mid-OAuth (spinner)
    @State private var error: String?
    // SECURITY (2026-08-06): the "which account is yours?" picker is GONE. It offered
    // the app-global Post for Me pool — i.e. OTHER creators' accounts — to anyone whose
    // OAuth failed, and picking one granted real posting authority. The shared-page
    // case it existed for is handled server-side now: /v1/social/finish detects the
    // token refresh a genuine re-authorization causes and claims that account without
    // ever asking the user to pick from a list they mostly don't own.

    var body: some View {
        VStack(alignment: .leading, spacing: Space.stack) {
            // Linked accounts: one grouped card of avatar rows.
            if !store.brand.connectedAccounts.isEmpty {
                DSGroup {
                    ForEach(Array(store.brand.connectedAccounts.enumerated()), id: \.element.id) { i, acct in
                        if i > 0 { DSRowDivider(inset: Space.rowPad + 40 + Space.md) }
                        LinkedAccountCard(account: acct) { store.removeConnectedAccount(acct) }
                    }
                }
            }

            // The two platforms as grouped rows (Stoic list card): mark, name, the
            // reason to tap it, and a chevron (or a spinner while OAuth runs).
            DSGroup {
                connectCard(platform: "instagram", label: "Instagram",
                            benefit: "I'll learn your voice from your reels and captions")
                DSRowDivider(inset: Space.rowPad + 40 + Space.md)
                connectCard(platform: "tiktok", label: "TikTok",
                            benefit: "I'll learn your voice from your posts and hooks")
            }

            if let error {
                // Monochrome error: the glyph carries the meaning, not a red hue.
                HStack(alignment: .firstTextBaseline, spacing: Space.sm) {
                    Image(systemName: "exclamationmark.circle")
                        .font(.system(size: 13, weight: .semibold))
                    Text(error).font(AppFont.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .foregroundStyle(Palette.textPrimary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Space.rowPad)
            }
        }
    }

    // MARK: OAuth connect (real posting authority)

    private func connectCard(platform: String, label: String, benefit: String) -> some View {
        let busy = linking == platform
        return Button { Task { await linkViaOAuth(platform) } } label: {
            HStack(spacing: Space.md) {
                PlatformBadge(platform: platform)
                VStack(alignment: .leading, spacing: 2) {
                    Text(busy ? "Connecting…" : label)
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                    Text(benefit)
                        .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: Space.sm)
                if busy {
                    ProgressView().controlSize(.small).tint(Palette.textPrimary)
                } else {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(Palette.textPrimary)
                }
            }
            .padding(.horizontal, Space.rowPad)
            .padding(.vertical, Space.sm + Space.xs)
            .frame(maxWidth: .infinity, minHeight: 64, alignment: .leading)
            .contentShape(Rectangle())
            .opacity(linking != nil ? 0.6 : 1)
        }
        .buttonStyle(DSRowPressStyle())
        .disabled(linking != nil)
        .accessibilityIdentifier("connect.\(platform)")
    }

    @MainActor
    private func linkViaOAuth(_ platform: String) async {
        error = nil; linking = platform
        defer { linking = nil }
        guard let url = await store.socialAuthURL(platform: platform) else {
            error = "Account connecting isn't available in demo mode yet."
            return
        }
        // Present the OAuth page. Post for Me Quickstart ends on its own success page (no
        // custom-scheme callback), so we don't depend on the callback firing — when the
        // sheet closes for any reason we poll for the linked account.
        _ = await WebAuth.present(url: url, callbackScheme: "marque")
        switch await store.finishLinkingAccount(platform: platform) {
        case .linked:
            break
        case .none:
            error = "Didn't finish connecting \(platform.capitalized). Tap Connect to try again."
        }
    }

}

// MARK: - ASWebAuthenticationSession wrapper (async)

/// Presents an OAuth URL in a system web-auth sheet and resolves when it closes. We don't
/// rely on the callback URL (Post for Me Quickstart uses a fixed https success page), so a
/// user "Done"/cancel resolves too and the caller confirms the link via the API.
enum WebAuth {
    @MainActor
    static func present(url: URL, callbackScheme: String) async -> Bool {
        await withCheckedContinuation { (cont: CheckedContinuation<Bool, Never>) in
            let session = ASWebAuthenticationSession(url: url, callbackURLScheme: callbackScheme) { cb, _ in
                cont.resume(returning: cb != nil)
            }
            session.presentationContextProvider = AuthPresenter.shared
            session.prefersEphemeralWebBrowserSession = false   // reuse Safari login cookies
            if !session.start() { cont.resume(returning: false) }
        }
    }
}

/// Anchors the web-auth sheet to the key window.
private final class AuthPresenter: NSObject, ASWebAuthenticationPresentationContextProviding {
    static let shared = AuthPresenter()
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap { $0.windows }
            .first { $0.isKeyWindow } ?? ASPresentationAnchor()
    }
}

/// 40pt rounded-square platform mark, monochrome: an ink tile with the platform's
/// glyph in onInk. Instagram's camera mark is drawn in code (rounded square, lens,
/// corner dot); no third-party logo asset ships in the bundle.
private struct PlatformBadge: View {
    let platform: String

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Palette.ink)
            glyph
        }
        .frame(width: 40, height: 40)
        .accessibilityHidden(true)
    }

    @ViewBuilder private var glyph: some View {
        if platform == "instagram" {
            ZStack {
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .strokeBorder(Palette.onInk, lineWidth: 2)
                    .frame(width: 21, height: 21)
                Circle().strokeBorder(Palette.onInk, lineWidth: 2)
                    .frame(width: 9, height: 9)
                Circle().fill(Palette.onInk)
                    .frame(width: 3, height: 3)
                    .offset(x: 6, y: -6)
            }
        } else {
            Image(systemName: "music.note")
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(Palette.onInk)
        }
    }
}

private struct LinkedAccountCard: View {
    let account: ConnectedAccount
    let onRemove: () -> Void
    var body: some View {
        HStack(spacing: Space.md) {
            // The avatar is a photo: the one place color may appear in this row.
            AsyncImage(url: URL(string: account.avatarUrl)) { img in
                img.resizable().scaledToFill()
            } placeholder: {
                Palette.surfaceSunken.overlay(Image(systemName: "person.fill").foregroundStyle(Palette.textSecondary))
            }
            .frame(width: 40, height: 40).clipShape(Circle())
            .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 1))

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 5) {
                    Text(account.displayName.isEmpty ? "@\(account.handle)" : account.displayName)
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary).lineLimit(1)
                    Image(systemName: account.platformIcon).font(.system(size: 12))
                        .foregroundStyle(Palette.textSecondary)
                }
                // Followers when known; the posting badge is the real signal now.
                HStack(spacing: 6) {
                    if account.followers > 0 {
                        Text("\(compactNumber(account.followers)) followers").font(AppFont.caption)
                            .foregroundStyle(Palette.textSecondary)
                            .lineLimit(1)
                    }
                    // Monochrome chip: inverted when the account can post, outline when
                    // it only feeds the voice profile.
                    HStack(spacing: 3) {
                        if account.canPublish {
                            Image(systemName: "checkmark").font(.system(size: 9, weight: .bold))
                        }
                        Text(account.canPublish ? "Can post" : "Voice only")
                            .font(AppFont.caption.weight(.semibold))
                            .lineLimit(1)
                    }
                    .foregroundStyle(account.canPublish ? Palette.onInk : Palette.textSecondary)
                    .padding(.horizontal, 8).frame(height: 20)
                    .background(Capsule().fill(account.canPublish ? Palette.ink : .clear))
                    .overlay(Capsule().strokeBorder(account.canPublish ? .clear : Palette.hairline, lineWidth: 1))
                }
            }
            Spacer(minLength: 0)
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 18))
                .foregroundStyle(Palette.textPrimary)
                .accessibilityLabel("Connected")
            Button { onRemove() } label: {
                Image(systemName: "xmark").font(.system(size: 13, weight: .regular))
                    .foregroundStyle(Palette.textSecondary)
                    .frame(width: 36, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(PressableStyle(dim: 0.6))
            .accessibilityLabel("Remove account")
            .accessibilityIdentifier("connect.remove")
        }
        .padding(.leading, Space.rowPad)
        .padding(.trailing, Space.sm)
        .padding(.vertical, Space.sm + Space.xs)
        .frame(maxWidth: .infinity, minHeight: 64, alignment: .leading)
        .accessibilityIdentifier("connect.linked")
    }
}
