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
        VStack(spacing: Space.md) {
            ForEach(store.brand.connectedAccounts) { acct in
                LinkedAccountCard(account: acct) { store.removeConnectedAccount(acct) }
            }

            // Two full-width platform cards, stacked. The old pair of 50pt ink
            // buttons side by side read as a form control; this is the single most
            // valuable action in onboarding, so it gets real cards with the
            // platform's own mark and the reason to tap it.
            connectCard(platform: "instagram", label: "Instagram",
                        benefit: "I'll learn your voice from your reels and captions")
            connectCard(platform: "tiktok", label: "TikTok",
                        benefit: "I'll learn your voice from your posts and hooks")

            if let error {
                Text(error).font(AppFont.caption).foregroundStyle(Palette.critical)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    // MARK: OAuth connect (real posting authority)

    private func connectCard(platform: String, label: String, benefit: String) -> some View {
        let busy = linking == platform
        return Button { Task { await linkViaOAuth(platform) } } label: {
            HStack(spacing: Space.md) {
                PlatformBadge(platform: platform)
                VStack(alignment: .leading, spacing: 3) {
                    Text(busy ? "Connecting…" : label)
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                    Text(benefit)
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: Space.sm)
                if busy {
                    ProgressView().controlSize(.small).tint(Palette.textTertiary)
                } else {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Palette.textTertiary)
                }
            }
            .padding(Space.md)
            .frame(maxWidth: .infinity, minHeight: 72, alignment: .leading)
            .background {
                RoundedRectangle(cornerRadius: Radius.xl, style: .continuous)
                    .fill(Palette.surfaceRaised)
                    .overlay(LiquidGlassFill(radius: Radius.xl, sheen: 0.3))
            }
            .clipShape(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1))
            .shadow(color: Palette.shadowWarm.opacity(0.08), radius: 12, x: 0, y: 5)
            .opacity(linking != nil ? 0.6 : 1)
        }
        .buttonStyle(PressableStyle())
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

/// 44pt rounded-square platform mark. Instagram's is drawn in code (a rounded
/// square, a lens circle and the corner dot) over the brand's warm gradient —
/// no third-party logo asset ships in the bundle.
private struct PlatformBadge: View {
    let platform: String

    private static let igGradient = LinearGradient(
        colors: [Color(hex: 0xF58529), Color(hex: 0xDD2A7B), Color(hex: 0x8134AF)],
        startPoint: .topLeading, endPoint: .bottomTrailing)

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 13, style: .continuous)
                .fill(platform == "instagram"
                      ? AnyShapeStyle(Self.igGradient)
                      : AnyShapeStyle(Palette.ink))
            glyph
        }
        .frame(width: 44, height: 44)
    }

    @ViewBuilder private var glyph: some View {
        if platform == "instagram" {
            ZStack {
                RoundedRectangle(cornerRadius: 7, style: .continuous)
                    .strokeBorder(Color.white, lineWidth: 2)
                    .frame(width: 23, height: 23)
                Circle().strokeBorder(Color.white, lineWidth: 2)
                    .frame(width: 10, height: 10)
                Circle().fill(Color.white)
                    .frame(width: 3.5, height: 3.5)
                    .offset(x: 6.5, y: -6.5)
            }
        } else {
            Image(systemName: "music.note")
                .font(.system(size: 20, weight: .semibold))
                .foregroundStyle(Color.white)
        }
    }
}

private struct LinkedAccountCard: View {
    let account: ConnectedAccount
    let onRemove: () -> Void
    var body: some View {
        HStack(spacing: Space.md) {
            AsyncImage(url: URL(string: account.avatarUrl)) { img in
                img.resizable().scaledToFill()
            } placeholder: {
                Palette.surfaceSunken.overlay(Image(systemName: "person.fill").foregroundStyle(Palette.textTertiary))
            }
            .frame(width: 48, height: 48).clipShape(Circle())
            .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 1))

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 5) {
                    Text(account.displayName.isEmpty ? "@\(account.handle)" : account.displayName)
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary).lineLimit(1)
                    Image(systemName: account.platformIcon).font(.system(size: 12)).foregroundStyle(Palette.textTertiary)
                }
                // Followers when known; the posting badge is the real signal now.
                HStack(spacing: 6) {
                    if account.followers > 0 {
                        Text("\(compactNumber(account.followers)) followers").font(AppFont.caption)
                            .foregroundStyle(Palette.textSecondary)
                    }
                    Text(account.canPublish ? "Can post" : "Voice only")
                        .font(.system(size: 10, weight: .bold)).tracking(0.4)
                        .foregroundStyle(account.canPublish ? Palette.positive : Palette.textTertiary)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background((account.canPublish ? Palette.positive : Palette.textTertiary).opacity(0.12))
                        .clipShape(Capsule())
                }
            }
            Spacer(minLength: 0)
            Image(systemName: "checkmark.circle.fill").foregroundStyle(Palette.positive)
            Button { onRemove() } label: {
                Image(systemName: "xmark").font(.system(size: 12)).foregroundStyle(Palette.textTertiary)
            }
            .padding(.leading, 4)
            .accessibilityIdentifier("connect.remove")
        }
        .padding(Space.md)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
        .shadow(color: .black.opacity(0.04), radius: 8, x: 0, y: 3)
        .accessibilityIdentifier("connect.linked")
    }
}
