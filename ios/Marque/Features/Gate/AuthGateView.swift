import SwiftUI
import AuthenticationServices

// The account wall — now AFTER the paywall (commit first; "Save your brand" is
// literally saving the plan the digest built). Apple + Google + email/password
// via Supabase when configured; a one-tap demo account otherwise (Maestro path).
struct AuthGateView: View {
    @Environment(AppStore.self) private var store
    @State private var mode: Mode = .create
    @State private var email = ""
    @State private var password = ""
    @FocusState private var focus: Field?

    enum Mode { case create, signIn }
    enum Field { case email, password }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                VStack(alignment: .leading, spacing: Space.sm) {
                    SectionLabel(text: "YOUR ACCOUNT", accent: Palette.accent)
                    Text(mode == .create ? "Save your brand." : "Welcome back.")
                        .font(AppFont.displayXL).tracking(-1)
                        .foregroundStyle(Palette.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(mode == .create
                         ? "Everything Marque learns about you — your voice, your angle, your scripts — lives on your account."
                         : "Sign in to pick up your brand where you left it.")
                        .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                        .lineSpacing(3)
                }
                .padding(.top, Space.huge)

                // Social sign-in first (the fast path), email below.
                VStack(spacing: Space.md) {
                    SignInWithAppleButton(.continue) { request in
                        store.auth.prepareAppleRequest(request)
                    } onCompletion: { result in
                        Task { await store.auth.handleAppleCompletion(result) }
                    }
                    .signInWithAppleButtonStyle(.black)
                    .frame(height: 52)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                    .accessibilityIdentifier("auth.apple")

                    Button {
                        Task { await store.auth.signInWithGoogle() }
                    } label: {
                        HStack(spacing: Space.sm) {
                            Image(systemName: "globe")
                                .font(.system(size: 16, weight: .medium))
                            Text("Continue with Google").font(AppFont.headline)
                        }
                        .foregroundStyle(Palette.textPrimary)
                        .frame(maxWidth: .infinity).frame(height: 52)
                        .background(Palette.surfaceRaised)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                            .strokeBorder(Palette.hairline, lineWidth: 1))
                    }
                    .buttonStyle(PressableStyle())
                    .accessibilityIdentifier("auth.google")

                    HStack(spacing: Space.md) {
                        MarqueHairline()
                        Text("or").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        MarqueHairline()
                    }
                }

                VStack(spacing: Space.md) {
                    TextField("Email", text: $email)
                        .textContentType(.emailAddress)
                        .keyboardType(.emailAddress)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .marqueField()
                        .focused($focus, equals: .email)
                        .accessibilityIdentifier("auth.email")
                    SecureField("Password", text: $password)
                        .textContentType(mode == .create ? .newPassword : .password)
                        .marqueField()
                        .focused($focus, equals: .password)
                        .accessibilityIdentifier("auth.password")
                }

                if !store.auth.lastError.isEmpty {
                    Text(store.auth.lastError)
                        .font(AppFont.caption).foregroundStyle(Palette.critical)
                        .fixedSize(horizontal: false, vertical: true)
                }

                VStack(spacing: Space.md) {
                    PrimaryButton(title: store.auth.isWorking ? "One moment…"
                                  : (mode == .create ? "Create account" : "Sign in"),
                                  shine: mode == .create) {
                        focus = nil
                        Task {
                            if mode == .create { await store.auth.createAccount(email: email, password: password) }
                            else { await store.auth.signIn(email: email, password: password) }
                        }
                    }
                    .disabled(store.auth.isWorking)
                    .accessibilityIdentifier(mode == .create ? "auth.createAccount" : "auth.signIn")

                    Button(mode == .create ? "I already have an account" : "I need an account") {
                        withAnimation(Motion.quick) { mode = mode == .create ? .signIn : .create }
                    }
                    .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                    .accessibilityIdentifier("auth.toggleMode")

                    // Demo path: keyless installs, plus DEBUG builds always (Supabase
                    // config now ships in Info.plist, but Maestro flows sign in via
                    // the demo account — never against the real auth backend).
                    #if DEBUG
                    let showDemo = true
                    #else
                    let showDemo = AppConfig.supabaseAnonKey.isEmpty
                    #endif
                    if showDemo {
                        Button("Continue with a demo account") { store.auth.continueAsDemo() }
                            .font(AppFont.callout).foregroundStyle(Palette.textTertiary)
                            .padding(.top, Space.sm)
                            .accessibilityIdentifier("auth.demoContinue")
                    }
                }

                Spacer(minLength: Space.huge)
            }
            .screenPadding().padding(.vertical, Space.lg)
        }
        .scrollDismissesKeyboard(.interactively)
        .background(Palette.canvas.ignoresSafeArea())
    }
}
