import SwiftUI
import UIKit
import AuthenticationServices

// Sign-in, ported from maxapp's CreateAccountScreen.tsx — the DARK cinematic
// treatment, not the light LoginScreen one. Full-bleed hero plate drifting under
// a 4-stop near-black scrim, a centered serif headline with one real italic
// word, a tracked capsule pill, glass fields, a solid white CTA pill, an OR
// divider, then two IDENTICAL glass social buttons.
//
// The auth behavior is unchanged from the light version: same state machine,
// same validation, same AuthManager calls, same accessibility identifiers.
//
// The Apple button is deliberately NOT SignInWithAppleButton. That control
// renders its own label with its own (larger, uncontrollable) font, so it never
// matched the Google pill sitting directly above it — the owner's complaint.
// AppleSignInCoordinator below drives ASAuthorizationController by hand so the
// button is just a view we style like any other.

/// maxapp's dark palette (CreateAccountScreen.tsx :32-39).
private enum DarkParity {
    static let ink = Color(hex: 0x0B0B0D)
    static let hair = Color.white.opacity(0.14)
    static let hairSoft = Color.white.opacity(0.08)
    static let muted = Color.white.opacity(0.58)
    static let mutedSoft = Color.white.opacity(0.42)
    static let field = Color.white.opacity(0.06)
    static let err = Color(hex: 0xFF6B5E)
    static let glassFill = Color.white.opacity(0.14)
    static let glassBorder = Color.white.opacity(0.45)
}

struct SignInScreen: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    /// `.create` mirrors maxapp's CreateAccountScreen (adds the legal footer);
    /// `.signIn` is the returning-user pass. One screen, two modes.
    var mode: Mode = .signIn
    var showsBack: Bool = true

    enum Mode { case signIn, create }
    enum Field { case identifier, password }

    @State private var identifier = ""
    @State private var password = ""
    @State private var showPassword = false
    @State private var currentMode: Mode = .signIn
    @State private var entered = false        // maxapp's fade + 18pt slide-up on mount
    @State private var bgScale: CGFloat = 1.0
    @State private var bgOffset: CGFloat = 0
    @FocusState private var focus: Field?
    @State private var apple = AppleSignInCoordinator()

    private var busy: Bool { store.auth.isWorking }
    private var apiError: String { store.auth.lastError }

    var body: some View {
        ZStack {
            DarkParity.ink.ignoresSafeArea()

            // Hero plate with the paywall's Ken Burns drift (scale 1→1.07 / 9s,
            // x 0→9pt / 12s, auto-reversing). MUST be geometry-bound and clipped:
            // an unclipped scaledToFill sizes the whole ZStack to the image's
            // intrinsic width and shoves every sibling off both edges.
            GeometryReader { geo in
                Image("AuthHero")
                    .resizable().scaledToFill()
                    .frame(width: geo.size.width, height: geo.size.height)
                    .scaleEffect(bgScale)
                    .offset(x: bgOffset)
                    .clipped()
            }
            .ignoresSafeArea()
            .allowsHitTesting(false)

            LinearGradient(stops: [
                .init(color: DarkParity.ink.opacity(0.90), location: 0),
                .init(color: DarkParity.ink.opacity(0.42), location: 0.34),
                .init(color: DarkParity.ink.opacity(0.52), location: 0.62),
                .init(color: DarkParity.ink.opacity(0.96), location: 1),
            ], startPoint: .top, endPoint: .bottom)
            .ignoresSafeArea()
            .allowsHitTesting(false)

            VStack(spacing: 0) {
                if showsBack {
                    HStack {
                        Button { dismiss() } label: {
                            Image(systemName: "chevron.left")
                                .font(.system(size: 18, weight: .semibold))
                                .foregroundStyle(.white)
                                .frame(width: 34, height: 34)
                                .background(Circle().fill(Color.white.opacity(0.12)))
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("auth.back")
                        Spacer()
                    }
                    .padding(.horizontal, 20)
                    .padding(.bottom, 8)
                }

                // maxapp vertically CENTERS the form in the space below the nav
                // (contentContainer: justifyContent 'center') rather than
                // top-aligning it — the min-height frame reproduces that while
                // staying scrollable under the keyboard.
                GeometryReader { proxy in
                    ScrollView {
                        formColumn
                            .padding(.horizontal, 24)
                            .padding(.vertical, 40)
                            .frame(maxWidth: .infinity)
                            .frame(minHeight: proxy.size.height)   // centers like maxapp's flex
                            .opacity(entered ? 1 : 0)
                            .offset(y: entered ? 0 : 18)           // 500ms fade + slide, 80ms delay
                    }
                    .scrollDismissesKeyboard(.interactively)
                }
            }
        }
        .onAppear {
            currentMode = mode
            withAnimation(.easeOut(duration: 0.5).delay(0.08)) { entered = true }
            withAnimation(.easeInOut(duration: 9).repeatForever(autoreverses: true)) { bgScale = 1.07 }
            withAnimation(.easeInOut(duration: 12).repeatForever(autoreverses: true)) { bgOffset = 9 }
        }
    }

    // MARK: content

    private var formColumn: some View {
        VStack(spacing: 0) {
            // The italic word is a REAL italic cut, not a synthesized slant:
            // Fraunces-Italic.ttf ships under the PostScript name
            // Fraunces-9ptBlackItalic (verified against the bundled file).
            title
                .multilineTextAlignment(.center)

            Text(currentMode == .signIn ? "GOOD TO SEE YOU" : "ALMOST THERE")
                .font(Typeface.sans(11, .semibold)).tracking(1.4)
                .foregroundStyle(.white)
                .padding(.horizontal, 14).padding(.vertical, 5)
                .overlay(Capsule().strokeBorder(DarkParity.hair, lineWidth: 1))
                .padding(.top, 12)

            Text(currentMode == .signIn
                 ? "Pick up right where you left off."
                 : "Your voice and your scripts, saved to your account.")
                .font(Typeface.sans(15))
                .foregroundStyle(DarkParity.muted)
                .multilineTextAlignment(.center)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 16)
                .padding(.bottom, 26)

            VStack(spacing: 10) {
                // "Email or username", default keyboard (usernames are valid).
                TextField("", text: $identifier,
                          prompt: Text("Email or username").foregroundColor(DarkParity.mutedSoft))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textContentType(.username)
                    .focused($focus, equals: .identifier)
                    .submitLabel(.next)
                    .onSubmit { focus = .password }
                    .modifier(DarkFieldStyle(focused: focus == .identifier,
                                             error: !apiError.isEmpty))
                    .accessibilityIdentifier("auth.email")

                HStack(spacing: 0) {
                    Group {
                        if showPassword {
                            TextField("", text: $password,
                                      prompt: Text("Password").foregroundColor(DarkParity.mutedSoft))
                        } else {
                            SecureField("", text: $password,
                                        prompt: Text("Password").foregroundColor(DarkParity.mutedSoft))
                        }
                    }
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textContentType(currentMode == .signIn ? .password : .newPassword)
                    .focused($focus, equals: .password)
                    .submitLabel(.go)
                    .onSubmit { Task { await submit() } }
                    .font(Typeface.sans(16))
                    .foregroundStyle(.white)
                    .tint(.white)
                    .padding(.horizontal, 16)
                    .accessibilityIdentifier("auth.password")

                    Button { showPassword.toggle() } label: {
                        Image(systemName: showPassword ? "eye.slash" : "eye")
                            .font(.system(size: 18))
                            .foregroundStyle(DarkParity.muted)
                            .padding(.horizontal, 14)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("auth.togglePassword")
                }
                .frame(height: 54)
                .background(RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(DarkParity.field))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .strokeBorder(fieldStroke(focused: focus == .password,
                                              error: !apiError.isEmpty), lineWidth: 1))
            }

            if currentMode == .signIn {
                HStack {
                    Spacer()
                    Button("Forgot password?") { }
                        .font(Typeface.sans(13, .medium))
                        .foregroundStyle(DarkParity.muted)
                        .buttonStyle(.plain)
                }
                .padding(.top, 10)
            }

            // Plain centered line, not a box: on a dark plate a tinted error card
            // reads as a second surface fighting the glass.
            if !apiError.isEmpty {
                Text(apiError)
                    .font(Typeface.sans(13.5))
                    .foregroundStyle(DarkParity.err)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 12)
            }

            Button { Task { await submit() } } label: {
                Text(busy ? (currentMode == .signIn ? "Signing in…" : "Creating…") : "Continue")
                    .font(Typeface.sans(16, .semibold)).tracking(0.1)
                    .foregroundStyle(DarkParity.ink)
                    .frame(maxWidth: .infinity).frame(height: 56)
                    .background(Capsule().fill(.white))
            }
            .buttonStyle(.plain)
            .disabled(busy)
            .opacity(busy ? 0.45 : 1)
            .shadow(color: .black.opacity(0.30), radius: 16, y: 6)
            .padding(.top, 22)
            .accessibilityIdentifier("auth.continue")

            HStack(spacing: 12) {
                Rectangle().fill(DarkParity.hair).frame(height: 1)
                Text("OR").font(Typeface.sans(11)).tracking(1.2)
                    .foregroundStyle(DarkParity.mutedSoft)
                Rectangle().fill(DarkParity.hair).frame(height: 1)
            }
            .padding(.top, 22).padding(.bottom, 14)

            // Google + Apple, IDENTICAL chrome. Apple is required for App Store
            // submission (guideline 4.8 — offering Google alone requires an
            // equivalent privacy-focused option). The AuthManager plumbing
            // (prepareAppleRequest / handleAppleCompletion → Supabase id_token
            // grant) shipped in builds ≤70 and is known-good; only the button
            // that feeds it changed.
            VStack(spacing: 10) {
                GlassAuthButton(icon: AnyView(GoogleGMark(size: 18, color: .white)),
                                label: "Continue with Google") {
                    Task { await store.auth.signInWithGoogle() }
                }
                .disabled(busy)
                .accessibilityIdentifier("auth.google")

                GlassAuthButton(icon: AnyView(Image(systemName: "apple.logo")
                                                .font(.system(size: 18))
                                                .foregroundStyle(.white)),
                                label: "Continue with Apple") {
                    Task {
                        let result = await apple.signIn { store.auth.prepareAppleRequest($0) }
                        await store.auth.handleAppleCompletion(result)
                    }
                }
                .disabled(busy)
                .accessibilityIdentifier("auth.apple")
            }

            Button {
                withAnimation(.easeOut(duration: 0.15)) {
                    currentMode = currentMode == .signIn ? .create : .signIn
                }
                store.auth.lastError = ""
            } label: {
                (Text(currentMode == .signIn ? "New here? " : "Already have an account? ")
                    .foregroundStyle(DarkParity.muted)
                 + Text(currentMode == .signIn ? "Create account" : "Sign in")
                    .font(Typeface.sans(14, .semibold))
                    .foregroundStyle(.white)
                    .underline())
                    .font(Typeface.sans(14))
            }
            .buttonStyle(.plain)
            .padding(.top, 20)
            .accessibilityIdentifier("auth.toggleMode")

            if currentMode == .create {
                Text("By tapping Continue, you agree to our Terms and Privacy Policy.")
                    .font(Typeface.sans(11.5))
                    .foregroundStyle(DarkParity.mutedSoft)
                    .multilineTextAlignment(.center)
                    .padding(.top, 18)
            }
        }
    }

    private var title: Text {
        let head = currentMode == .signIn ? "Welcome " : "Save your "
        let tail = currentMode == .signIn ? "back" : "brand"
        return (Text(head) + Text(tail).font(.custom("Fraunces-9ptBlackItalic", size: 34)))
            .font(Typeface.display(34))
            .tracking(-0.8)
            .foregroundColor(.white)
    }

    private func fieldStroke(focused: Bool, error: Bool) -> Color {
        error ? DarkParity.err : (focused ? DarkParity.glassBorder : DarkParity.hairSoft)
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

/// maxapp's dark field chrome: 54pt tall, radius 14 continuous, 6% white fill,
/// an 8% hairline that goes 45% white on focus and red on error.
private struct DarkFieldStyle: ViewModifier {
    let focused: Bool
    let error: Bool
    func body(content: Content) -> some View {
        content
            .font(Typeface.sans(16))
            .foregroundStyle(.white)
            .tint(.white)
            .padding(.horizontal, 16)
            .frame(height: 54)
            .background(RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(DarkParity.field))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(error ? DarkParity.err
                                    : (focused ? DarkParity.glassBorder : DarkParity.hairSoft),
                              lineWidth: 1))
    }
}

/// The one glass pill both social buttons wear. Google and Apple MUST share this
/// — the whole point of dropping SignInWithAppleButton was that its private
/// label font made the two rows visibly different sizes.
private struct GlassAuthButton: View {
    let icon: AnyView
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                icon
                Text(label)
                    .font(Typeface.sans(15, .semibold)).tracking(0.3)
                    .foregroundStyle(.white)
            }
            .frame(maxWidth: .infinity).frame(height: 54)
            .background(Capsule().fill(DarkParity.glassFill))
            .overlay(Capsule().strokeBorder(DarkParity.glassBorder, lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

/// Drives Sign in with Apple by hand so the button can be an ordinary styled
/// view. `signIn` bridges ASAuthorizationController's delegate callbacks into a
/// single awaited Result, shaped to feed AuthManager.handleAppleCompletion
/// verbatim. The presentation anchor resolves the key window the same way
/// ConnectAccountsView's AuthPresenter does.
private final class AppleSignInCoordinator: NSObject, ASAuthorizationControllerDelegate,
                                            ASAuthorizationControllerPresentationContextProviding {
    private var cont: CheckedContinuation<Result<ASAuthorization, Error>, Never>?

    func signIn(prepare: (ASAuthorizationAppleIDRequest) -> Void) async -> Result<ASAuthorization, Error> {
        let request = ASAuthorizationAppleIDProvider().createRequest()
        prepare(request)
        return await withCheckedContinuation { continuation in
            // A controller that outlives this scope is required — ASAuthorization
            // keeps only a weak delegate, so the coordinator (held by the view)
            // is what keeps the callback alive.
            cont = continuation
            let controller = ASAuthorizationController(authorizationRequests: [request])
            controller.delegate = self
            controller.presentationContextProvider = self
            controller.performRequests()
        }
    }

    private func finish(_ result: Result<ASAuthorization, Error>) {
        cont?.resume(returning: result)
        cont = nil
    }

    func authorizationController(controller: ASAuthorizationController,
                                 didCompleteWithAuthorization authorization: ASAuthorization) {
        finish(.success(authorization))
    }

    func authorizationController(controller: ASAuthorizationController,
                                 didCompleteWithError error: Error) {
        finish(.failure(error))
    }

    func presentationAnchor(for controller: ASAuthorizationController) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap { $0.windows }
            .first { $0.isKeyWindow } ?? ASPresentationAnchor()
    }
}

/// The monochrome Google "G" logo glyph (what Ionicons' logo-google renders in
/// maxapp), drawn in code: a thick circular stroke open at the upper right,
/// with the crossbar running from the center to the right edge. Single color —
/// white here, so it matches the Apple mark beside it.
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
