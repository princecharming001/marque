import SwiftUI
import AuthenticationServices

// Sign-in, ported from maxapp's LoginScreen.tsx structure beat-for-beat: soft
// off-white canvas, a circular back chip, a big serif wordmark over a lowercase
// title, two stacked fields (identifier + password with an eye toggle), a
// right-aligned "Forgot password?", an inline error box, an ink CTA pill, an OR
// divider, then Google above Apple, and a "New here? create account" footer.
//
// maxapp's exact metrics are preserved (56pt fields, radius 14, 54pt social
// pills, capsule CTA, 24pt gutter) using Yunicorn's palette + font stack.
struct SignInScreen: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    /// `.create` mirrors maxapp's SignupScreen (adds the legal footer); `.signIn`
    /// is LoginScreen. One screen, two modes — the fields are identical.
    var mode: Mode = .signIn
    var showsBack: Bool = true

    enum Mode { case signIn, create }
    enum Field { case identifier, password }

    @State private var identifier = ""
    @State private var password = ""
    @State private var showPassword = false
    @State private var currentMode: Mode = .signIn
    @FocusState private var focus: Field?

    private var busy: Bool { store.auth.isWorking }
    private var apiError: String { store.auth.lastError }

    var body: some View {
        ZStack(alignment: .top) {
            Palette.canvas.ignoresSafeArea()

            if showsBack {
                HStack {
                    Button { dismiss() } label: {
                        Image(systemName: "arrow.left")
                            .font(.system(size: 17, weight: .medium))
                            .foregroundStyle(Palette.textPrimary)
                            .frame(width: 40, height: 40)
                            .background(Circle().fill(Palette.surfaceSunken))
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("auth.back")
                    Spacer()
                }
                .padding(.horizontal, 20)
            }

            ScrollView {
                VStack(spacing: 0) {
                    Text("yunicorn")
                        .font(Typeface.display(48, .semibold)).tracking(-1.5)
                        .foregroundStyle(Palette.textPrimary)
                        .padding(.bottom, 6)
                    Text(currentMode == .signIn ? "welcome back" : "create account")
                        .font(Typeface.sans(22, .regular)).tracking(-0.3)
                        .foregroundStyle(Palette.textPrimary)
                        .padding(.bottom, 28)

                    VStack(spacing: 12) {
                        TextField("Email", text: $identifier)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .keyboardType(.emailAddress)
                            .textContentType(.username)
                            .focused($focus, equals: .identifier)
                            .submitLabel(.next)
                            .onSubmit { focus = .password }
                            .modifier(AuthFieldStyle(focused: focus == .identifier,
                                                     error: !apiError.isEmpty))
                            .accessibilityIdentifier("auth.email")

                        HStack(spacing: 0) {
                            Group {
                                if showPassword {
                                    TextField("Password", text: $password)
                                } else {
                                    SecureField("Password", text: $password)
                                }
                            }
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .textContentType(currentMode == .signIn ? .password : .newPassword)
                            .focused($focus, equals: .password)
                            .submitLabel(.go)
                            .onSubmit { Task { await submit() } }
                            .font(Typeface.sans(15, .regular))
                            .foregroundStyle(Palette.textPrimary)
                            .padding(.horizontal, 16)
                            .accessibilityIdentifier("auth.password")

                            Button { showPassword.toggle() } label: {
                                Image(systemName: showPassword ? "eye.slash" : "eye")
                                    .font(.system(size: 17))
                                    .foregroundStyle(Palette.textSecondary)
                                    .padding(.horizontal, 14)
                            }
                            .buttonStyle(.plain)
                            .accessibilityIdentifier("auth.togglePassword")
                        }
                        .frame(height: 56)
                        .background(RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .fill(Color.white))
                        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .strokeBorder(fieldStroke(focused: focus == .password,
                                                      error: !apiError.isEmpty), lineWidth: 1))
                    }

                    if currentMode == .signIn {
                        HStack {
                            Spacer()
                            Button("Forgot password?") { }
                                .font(Typeface.sans(13, .medium))
                                .foregroundStyle(Palette.textSecondary)
                                .buttonStyle(.plain)
                        }
                        .padding(.top, 10).padding(.bottom, 4)
                    }

                    if !apiError.isEmpty {
                        Text(apiError)
                            .font(Typeface.sans(13, .regular))
                            .foregroundStyle(Palette.critical)
                            .lineSpacing(5)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(12)
                            .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .fill(Palette.critical.opacity(0.06)))
                            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .strokeBorder(Palette.critical.opacity(0.25), lineWidth: 1))
                            .padding(.top, 10)
                    }

                    Button { Task { await submit() } } label: {
                        Text(busy ? (currentMode == .signIn ? "Signing in…" : "Creating…") : "Continue")
                            .font(Typeface.sans(16, .semibold)).tracking(0.2)
                            .foregroundStyle(Palette.onInk)
                            .frame(maxWidth: .infinity).frame(height: 56)
                            .background(Capsule().fill(Palette.ink))
                    }
                    .buttonStyle(.plain)
                    .disabled(busy)
                    .opacity(busy ? 0.45 : 1)
                    .padding(.top, 16)
                    .accessibilityIdentifier("auth.continue")

                    HStack(spacing: 12) {
                        Rectangle().fill(Palette.hairline).frame(height: 1)
                        Text("OR").font(Typeface.sans(11, .medium)).tracking(1.2)
                            .foregroundStyle(Palette.textTertiary)
                        Rectangle().fill(Palette.hairline).frame(height: 1)
                    }
                    .padding(.vertical, 20)

                    VStack(spacing: 10) {
                        Button { Task { await store.auth.signInWithGoogle() } } label: {
                            HStack(spacing: 10) {
                                Image(systemName: "g.circle.fill")
                                    .font(.system(size: 17))
                                    .foregroundStyle(Color(hex: 0x4285F4))
                                Text("Continue with Google")
                                    .font(Typeface.sans(15, .semibold)).tracking(0.3)
                                    .foregroundStyle(Palette.textPrimary)
                            }
                            .frame(maxWidth: .infinity).frame(height: 54)
                            .background(Capsule().fill(Color.white))
                            .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
                        }
                        .buttonStyle(.plain)
                        .disabled(busy)
                        .accessibilityIdentifier("auth.google")

                        SignInWithAppleButton(.continue) { request in
                            store.auth.prepareAppleRequest(request)
                        } onCompletion: { result in
                            Task { await store.auth.handleAppleCompletion(result) }
                        }
                        .signInWithAppleButtonStyle(.white)
                        .frame(height: 54)
                        .clipShape(Capsule())
                        .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
                        .accessibilityIdentifier("auth.apple")
                    }

                    Button {
                        withAnimation(.easeOut(duration: 0.15)) {
                            currentMode = currentMode == .signIn ? .create : .signIn
                        }
                        store.auth.lastError = ""
                    } label: {
                        (Text(currentMode == .signIn ? "New here? " : "Already have an account? ")
                            .foregroundStyle(Palette.textSecondary)
                         + Text(currentMode == .signIn ? "create account" : "sign in")
                            .foregroundStyle(Palette.textPrimary))
                            .font(Typeface.sans(14, .regular))
                    }
                    .buttonStyle(.plain)
                    .padding(.top, 22)
                    .accessibilityIdentifier("auth.toggleMode")

                    if currentMode == .create {
                        Text("By tapping Continue, you agree to our Terms and Privacy Policy.")
                            .font(Typeface.sans(11.5, .regular))
                            .foregroundStyle(Palette.textTertiary)
                            .multilineTextAlignment(.center)
                            .padding(.top, 18)
                    }
                }
                .padding(.horizontal, 24)
                .padding(.top, showsBack ? 56 : 90)
                .padding(.bottom, 40)
                .frame(maxWidth: .infinity)
            }
            .scrollDismissesKeyboard(.interactively)
        }
        .onAppear { currentMode = mode }
    }

    private func fieldStroke(focused: Bool, error: Bool) -> Color {
        error ? Palette.critical : (focused ? Palette.ink : Palette.hairline)
    }

    private func submit() async {
        guard !busy else { return }
        let mail = identifier.trimmingCharacters(in: .whitespaces)
        guard !mail.isEmpty, !password.isEmpty else {
            store.auth.lastError = "Please fill in all fields."
            return
        }
        if currentMode == .signIn {
            await store.auth.signIn(email: mail, password: password)
        } else {
            await store.auth.createAccount(email: mail, password: password)
        }
    }
}

/// maxapp's field chrome: 56pt tall, radius 14, white fill, 1pt border that goes
/// ink on focus and red on error.
private struct AuthFieldStyle: ViewModifier {
    let focused: Bool
    let error: Bool
    func body(content: Content) -> some View {
        content
            .font(Typeface.sans(15, .regular))
            .foregroundStyle(Palette.textPrimary)
            .padding(.horizontal, 16)
            .frame(height: 56)
            .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.white))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(error ? Palette.critical : (focused ? Palette.ink : Palette.hairline),
                              lineWidth: 1))
    }
}
