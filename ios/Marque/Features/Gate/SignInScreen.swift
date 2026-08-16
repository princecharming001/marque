import SwiftUI

// Sign-in, ported from maxapp's LoginScreen.tsx structure beat-for-beat: soft
// off-white canvas, a circular back chip, a big serif wordmark over a lowercase
// title, two stacked fields (identifier + password with an eye toggle), a
// right-aligned "Forgot password?", an inline error box, an ink CTA pill, an OR
// divider, then Google above Apple, and a "New here? create account" footer.
//
// maxapp's exact metrics are preserved (56pt fields, radius 14, 54pt social
// pills, capsule CTA, 24pt gutter) using Yunicorn's palette + font stack.
// Where maxapp hardcodes a hex that has no Yunicorn token twin (#E2E2E2 field
// border, #A0A0A0 placeholder, #6B6B6B muted, #EBEBEB/#BBBBBB OR row, the
// #FEF2F0 error box), the exact hex is used — the owner asked for a
// near-exact mimic, and "close" grays read as off side-by-side.

// maxapp's exact grays (LoginScreen.tsx palette block).
private enum MaxParity {
    static let border = Color(hex: 0xE2E2E2)        // BORDER
    static let placeholder = Color(hex: 0xA0A0A0)   // PH
    static let muted = Color(hex: 0x6B6B6B)         // MUTED
    static let orLine = Color(hex: 0xEBEBEB)
    static let orText = Color(hex: 0xBBBBBB)
    static let errBg = Color(hex: 0xFEF2F0)
    static let errBorder = Color(hex: 0xF5C6C2)
    static let errText = Color(hex: 0xC0452C)
}
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
    @State private var entered = false        // maxapp's fade + 18pt slide-up on mount
    @FocusState private var focus: Field?

    private var busy: Bool { store.auth.isWorking }
    private var apiError: String { store.auth.lastError }

    var body: some View {
        ZStack {
            Palette.canvas.ignoresSafeArea()

            VStack(spacing: 0) {
            if showsBack {
                HStack {
                    Button { dismiss() } label: {
                        Image(systemName: "arrow.left")
                            .font(.system(size: 20, weight: .medium))
                            .foregroundStyle(Palette.textPrimary)
                            .frame(width: 40, height: 40)
                            .background(Circle().fill(Color(hex: 0xF4F4F4)))
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("auth.back")
                    Spacer()
                }
                .padding(.horizontal, 20)
                .padding(.bottom, 8)
            }

            // maxapp vertically CENTERS the form in the space below the nav
            // (inner: justifyContent 'center') rather than top-aligning it —
            // the min-height frame reproduces that while staying scrollable
            // under the keyboard.
            GeometryReader { proxy in
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
                        // maxapp: "Email or username", default keyboard (usernames
                        // are valid), #A0A0A0 placeholder.
                        TextField("", text: $identifier,
                                  prompt: Text("Email or username").foregroundColor(MaxParity.placeholder))
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
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
                                    TextField("", text: $password,
                                              prompt: Text("Password").foregroundColor(MaxParity.placeholder))
                                } else {
                                    SecureField("", text: $password,
                                                prompt: Text("Password").foregroundColor(MaxParity.placeholder))
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
                                    .font(.system(size: 20))
                                    .foregroundStyle(MaxParity.muted)
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
                                .foregroundStyle(MaxParity.muted)
                                .buttonStyle(.plain)
                        }
                        .padding(.top, 10).padding(.bottom, 4)
                    }

                    if !apiError.isEmpty {
                        Text(apiError)
                            .font(Typeface.sans(13, .regular))
                            .foregroundStyle(MaxParity.errText)
                            .lineSpacing(5)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(12)
                            .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .fill(MaxParity.errBg))
                            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .strokeBorder(MaxParity.errBorder, lineWidth: 1))
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
                        Rectangle().fill(MaxParity.orLine).frame(height: 1)
                        Text("OR").font(Typeface.sans(11, .medium)).tracking(1.2)
                            .foregroundStyle(MaxParity.orText)
                        Rectangle().fill(MaxParity.orLine).frame(height: 1)
                    }
                    .padding(.vertical, 20)

                    // Google only for now (owner, build 71). Apple's button + the
                    // AuthManager plumbing behind it (prepareAppleRequest /
                    // handleAppleCompletion, the applesignin entitlement) are all
                    // intact — re-adding is a SignInWithAppleButton block below this
                    // one. ⚠️ App Review 4.8 requires an equivalent privacy-focused
                    // login option alongside Google, so Apple goes back before submit.
                    VStack(spacing: 10) {
                        Button { Task { await store.auth.signInWithGoogle() } } label: {
                            HStack(spacing: 10) {
                                // maxapp renders Ionicons' logo-google — the true
                                // Google "G" glyph — at 18pt in Google blue, not a
                                // circled G. Drawn below so no asset is needed.
                                GoogleGMark(size: 18)
                                Text("Continue with Google")
                                    .font(Typeface.sans(15, .semibold)).tracking(0.3)
                                    .foregroundStyle(Palette.textPrimary)
                            }
                            .frame(maxWidth: .infinity).frame(height: 54)
                            .background(Capsule().fill(Color.white))
                            .overlay(Capsule().strokeBorder(MaxParity.border, lineWidth: 1))
                        }
                        .buttonStyle(.plain)
                        .disabled(busy)
                        .accessibilityIdentifier("auth.google")
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
                .padding(.vertical, 40)
                .frame(maxWidth: .infinity)
                .frame(minHeight: proxy.size.height)   // centers like maxapp's flex
                .opacity(entered ? 1 : 0)
                .offset(y: entered ? 0 : 18)           // maxapp: 500ms fade + slide, 80ms delay
            }
            .scrollDismissesKeyboard(.interactively)
            }
            }
        }
        .onAppear {
            currentMode = mode
            withAnimation(.easeOut(duration: 0.5).delay(0.08)) { entered = true }
        }
    }

    private func fieldStroke(focused: Bool, error: Bool) -> Color {
        error ? MaxParity.errText : (focused ? Palette.ink : MaxParity.border)
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

/// maxapp's field chrome: 56pt tall, radius 14, white fill, 1pt #E2E2E2 border
/// that goes ink on focus and red on error.
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
                .strokeBorder(error ? MaxParity.errText : (focused ? Palette.ink : MaxParity.border),
                              lineWidth: 1))
    }
}

/// The monochrome Google "G" logo glyph (what Ionicons' logo-google renders in
/// maxapp), drawn in code: a thick circular stroke open at the upper right,
/// with the crossbar running from the center to the right edge. Single color —
/// Google blue — exactly like the reference button.
struct GoogleGMark: View {
    var size: CGFloat = 18
    var color: Color = Color(hex: 0x4285F4)

    var body: some View {
        let stroke = size * 0.21
        ZStack {
            // SwiftUI's Circle path starts at 3 o'clock and sweeps clockwise, so
            // trimming to 0.90 leaves the gap in the upper right — matching the G.
            Circle()
                .trim(from: 0, to: 0.90)
                .stroke(color, style: StrokeStyle(lineWidth: stroke, lineCap: .butt))
                .frame(width: size - stroke, height: size - stroke)
            Rectangle()
                .fill(color)
                .frame(width: size / 2, height: stroke)
                .offset(x: size / 4 - stroke / 4)
        }
        .frame(width: size, height: size)
    }
}
