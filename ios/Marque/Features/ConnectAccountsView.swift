import SwiftUI
import AuthenticationServices

// Link Instagram + TikTok accounts for real posting. Primary path is OAuth through Post
// for Me (grants posting authority — the linked account carries an spc_ id we publish to).
// A secondary "handle only" path stays for voice-learning (scrapes the public profile to
// study how the creator talks) when they don't want to grant posting access yet.
struct ConnectAccountsView: View {
    @Environment(AppStore.self) private var store
    @State private var linking: String?          // platform mid-OAuth (spinner)
    @State private var showHandleEntry = false    // reveal the voice-learning handle path
    @State private var openPlatform: String?      // "instagram" | "tiktok" while typing a handle
    @State private var handle = ""
    @State private var verifying = false
    @State private var error: String?

    var body: some View {
        VStack(spacing: Space.md) {
            ForEach(store.brand.connectedAccounts) { acct in
                LinkedAccountCard(account: acct) { store.removeConnectedAccount(acct) }
            }

            // Primary: OAuth connect (real posting).
            if let openPlatform {
                inputRow(openPlatform)
            } else {
                HStack(spacing: Space.sm) {
                    connectButton(platform: "instagram", label: "Instagram", icon: "camera.circle.fill")
                    connectButton(platform: "tiktok", label: "TikTok", icon: "music.note")
                }
                // Secondary: handle-only (voice learning, no posting).
                Button {
                    withAnimation(Motion.quick) { showHandleEntry.toggle() }
                } label: {
                    Text(showHandleEntry ? "Hide" : "Just analyze my voice (no posting)")
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        .underline()
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("connect.handleToggle")
                if showHandleEntry {
                    HStack(spacing: Space.sm) {
                        addHandleButton(platform: "instagram", label: "Instagram")
                        addHandleButton(platform: "tiktok", label: "TikTok")
                    }
                    .transition(.opacity)
                }
            }

            if let error {
                Text(error).font(AppFont.caption).foregroundStyle(Palette.critical)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    // MARK: OAuth connect (real posting authority)

    private func connectButton(platform: String, label: String, icon: String) -> some View {
        Button { Task { await linkViaOAuth(platform) } } label: {
            HStack(spacing: Space.sm) {
                if linking == platform {
                    ProgressView().controlSize(.small).tint(Palette.onInk)
                } else {
                    Image(systemName: icon)
                }
                Text(linking == platform ? "Connecting…" : "Connect \(label)").font(AppFont.callout)
            }
            .foregroundStyle(Palette.onInk)
            .frame(maxWidth: .infinity).frame(height: 50)
            .background(Palette.ink)
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
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
        let linked = await store.refreshLinkedAccount(platform: platform)
        if !linked {
            error = "Didn't finish connecting \(platform.capitalized). Tap Connect to try again."
        }
    }

    // MARK: Handle-only (voice learning, no posting)

    private func addHandleButton(platform: String, label: String) -> some View {
        Button { openPlatform = platform; handle = ""; error = nil } label: {
            Text(label).font(AppFont.callout).foregroundStyle(Palette.textPrimary)
                .frame(maxWidth: .infinity).frame(height: 44)
                .background(Palette.surfaceRaised)
                .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1))
        }
        .buttonStyle(PressableStyle())
        .accessibilityIdentifier("connect.handle.\(platform)")
    }

    private func inputRow(_ platform: String) -> some View {
        VStack(spacing: Space.sm) {
            HStack(spacing: 4) {
                Text("@").foregroundStyle(Palette.textTertiary)
                TextField("\(platform) handle", text: $handle)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("connect.handleField")
            }
            .font(AppFont.bodyL)
            .padding(.horizontal, Space.md).frame(height: 50)
            .background(Palette.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1))
            HStack {
                Button("Cancel") { openPlatform = nil; error = nil }
                    .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                Spacer()
                Button { link(platform) } label: {
                    HStack(spacing: 6) {
                        if verifying { ProgressView().controlSize(.small).tint(Palette.onInk) }
                        Text(verifying ? "Linking…" : "Link").font(AppFont.callout)
                    }
                    .foregroundStyle(Palette.onInk)
                    .padding(.horizontal, Space.lg).frame(height: 40)
                    .background(Palette.ink).clipShape(Capsule())
                }
                .buttonStyle(PressableStyle())
                .disabled(verifying || handle.trimmingCharacters(in: .whitespaces).isEmpty)
                .accessibilityIdentifier("connect.link")
            }
        }
    }

    private func link(_ platform: String) {
        verifying = true; error = nil
        let h = handle.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: "@", with: "")
        Task { @MainActor in
            if let acct = await store.connectPreview(handle: h, platform: platform) {
                store.addConnectedAccount(acct)
                openPlatform = nil; handle = ""
            } else {
                error = "Couldn't find @\(h) on \(platform.capitalized). Check the handle and try again."
            }
            verifying = false
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
