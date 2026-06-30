import SwiftUI

// Link Instagram + TikTok accounts (multiple). Each link is VERIFIED by fetching the real
// public profile through the backend — we show the actual avatar + follower count as proof.
struct ConnectAccountsView: View {
    @Environment(AppStore.self) private var store
    @State private var openPlatform: String?     // "instagram" | "tiktok" while entering a handle
    @State private var handle = ""
    @State private var verifying = false
    @State private var error: String?

    var body: some View {
        VStack(spacing: Space.md) {
            ForEach(store.brand.connectedAccounts) { acct in
                LinkedAccountCard(account: acct) { store.removeConnectedAccount(acct) }
            }
            if let openPlatform {
                inputRow(openPlatform)
            } else {
                HStack(spacing: Space.sm) {
                    addButton(platform: "instagram", label: "Instagram", icon: "camera.circle.fill")
                    addButton(platform: "tiktok", label: "TikTok", icon: "music.note")
                }
            }
            if let error {
                Text(error).font(AppFont.caption).foregroundStyle(Palette.critical)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func addButton(platform: String, label: String, icon: String) -> some View {
        Button { openPlatform = platform; handle = ""; error = nil } label: {
            HStack(spacing: Space.sm) {
                Image(systemName: icon)
                Text("Connect \(label)").font(AppFont.callout)
            }
            .foregroundStyle(Palette.textPrimary)
            .frame(maxWidth: .infinity).frame(height: 50)
            .background(Palette.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1))
        }
        .buttonStyle(PressableStyle())
        .accessibilityIdentifier("connect.\(platform)")
    }

    private func inputRow(_ platform: String) -> some View {
        VStack(spacing: Space.sm) {
            HStack(spacing: 4) {
                Text("@").foregroundStyle(Palette.textTertiary)
                TextField("\(platform) handle", text: $handle)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("connect.handle")
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
                Text("\(compactNumber(account.followers)) followers · @\(account.handle)")
                    .font(AppFont.caption).foregroundStyle(Palette.textSecondary).lineLimit(1)
            }
            Spacer(minLength: 0)
            Image(systemName: "checkmark.circle.fill").foregroundStyle(Palette.positive)
            Button { onRemove() } label: {
                Image(systemName: "xmark").font(.system(size: 12)).foregroundStyle(Palette.textTertiary)
            }
            .padding(.leading, 4)
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
