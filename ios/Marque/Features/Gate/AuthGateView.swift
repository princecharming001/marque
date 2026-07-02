import SwiftUI

// The account wall — hit right after onboarding, before the subscription gate.
// Email/password via Supabase when configured; a one-tap demo account otherwise
// (which is also the Maestro path). Sign in with Apple lands with signed builds.
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

                    if AppConfig.supabaseAnonKey.isEmpty {
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
