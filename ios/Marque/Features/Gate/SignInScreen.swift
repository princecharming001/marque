import SwiftUI
import UIKit
import AuthenticationServices

// Sign-in, in the Stoic onboarding language (DESIGN.md §6): canvas page, an eyebrow
// over a centered lowercase title, the two fields as rows in one grouped surface card,
// a primary capsule, an OR rule, then two IDENTICAL outline social capsules.
//
// The auth behavior is unchanged: same state machine, same validation, same
// AuthManager calls, same accessibility identifiers.
//
// The Apple button is deliberately NOT SignInWithAppleButton. That control
// renders its own label with its own (larger, uncontrollable) font, so it never
// matched the Google pill sitting directly above it — the owner's complaint.
// AppleSignInCoordinator below drives ASAuthorizationController by hand so the
// button is just a view we style like any other. It keeps Apple's glyph, the
// "Continue with Apple" wording and a 56pt (>= 44pt HIG) height.

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
    @State private var entered = false        // fade + slide-up on mount
    // Kept from the retired cinematic hero (Ken Burns); nothing reads them now.
    @State private var bgScale: CGFloat = 1.0
    @State private var bgOffset: CGFloat = 0
    @FocusState private var focus: Field?
    @State private var apple = AppleSignInCoordinator()

    private var busy: Bool { store.auth.isWorking }
    private var apiError: String { store.auth.lastError }

    var body: some View {
        VStack(spacing: 0) {
            if showsBack {
                HStack {
                    DSIconButton(systemName: "chevron.left") { dismiss() }
                        .accessibilityLabel("Back")
                        .accessibilityIdentifier("auth.back")
                    Spacer()
                }
                .padding(.horizontal, Space.xs)
            }

            // Vertically CENTER the form in the space below the nav rather than
            // top-aligning it — the min-height frame does that while staying
            // scrollable under the keyboard.
            GeometryReader { proxy in
                ScrollView {
                    formColumn
                        .padding(.horizontal, Space.screenH)
                        .padding(.vertical, Space.xxl)
                        .frame(maxWidth: .infinity)
                        .frame(minHeight: proxy.size.height)
                        .opacity(entered ? 1 : 0)
                        .offset(y: entered ? 0 : 18)
                }
                .scrollDismissesKeyboard(.interactively)
            }
        }
        .background(Palette.canvas.ignoresSafeArea())
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
            DSEyebrow(text: currentMode == .signIn ? "GOOD TO SEE YOU" : "ALMOST THERE")

            title
                .multilineTextAlignment(.center)
                .padding(.top, Space.xs)

            Text(currentMode == .signIn
                 ? "Pick up right where you left off."
                 : "Your voice and your scripts, saved to your account.")
                .font(AppFont.bodyText)
                .foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, Space.sm)
                .padding(.bottom, Space.xl)

            // Both fields as rows in one grouped surface card.
            VStack(spacing: 0) {
                // "Email or username", default keyboard (usernames are valid).
                TextField("", text: $identifier,
                          prompt: Text("Email or username").foregroundColor(Palette.textTertiary))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textContentType(.username)
                    .focused($focus, equals: .identifier)
                    .submitLabel(.next)
                    .onSubmit { focus = .password }
                    .modifier(FieldRowStyle())
                    .accessibilityIdentifier("auth.email")

                DSRowDivider()

                HStack(spacing: 0) {
                    Group {
                        if showPassword {
                            TextField("", text: $password,
                                      prompt: Text("Password").foregroundColor(Palette.textTertiary))
                        } else {
                            SecureField("", text: $password,
                                        prompt: Text("Password").foregroundColor(Palette.textTertiary))
                        }
                    }
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textContentType(currentMode == .signIn ? .password : .newPassword)
                    .focused($focus, equals: .password)
                    .submitLabel(.go)
                    .onSubmit { Task { await submit() } }
                    .modifier(FieldRowStyle())
                    .accessibilityIdentifier("auth.password")

                    Button { showPassword.toggle() } label: {
                        Image(systemName: showPassword ? "eye.slash" : "eye")
                            .font(.system(size: 18))
                            .foregroundStyle(Palette.textSecondary)
                            .frame(width: 44, height: 52)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .padding(.trailing, Space.xs)
                    .accessibilityLabel(showPassword ? "Hide password" : "Show password")
                    .accessibilityIdentifier("auth.togglePassword")
                }
            }
            .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                .fill(Palette.surface))
            .overlay(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                .strokeBorder(fieldStroke(focused: focus != nil, error: !apiError.isEmpty),
                              lineWidth: apiError.isEmpty ? 1 : 1.5))
            .animation(Motion.quick, value: focus)

            if currentMode == .signIn {
                HStack {
                    Spacer()
                    Button("Forgot password?") { }
                        .font(AppFont.supporting)
                        .foregroundStyle(Palette.textSecondary)
                        .buttonStyle(.plain)
                        .frame(minHeight: 44)
                }
                .padding(.top, Space.xs)
            }

            // Monochrome error: a glyph + wording instead of a red line.
            if !apiError.isEmpty {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Image(systemName: "exclamationmark.circle")
                        .font(.system(size: 14, weight: .semibold))
                    Text(apiError)
                        .font(AppFont.supporting)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .foregroundStyle(Palette.textPrimary)
                .accessibilityElement(children: .combine)
                .padding(.top, Space.stack)
            }

            Button { Task { await submit() } } label: {
                Text(busy ? (currentMode == .signIn ? "Signing in…" : "Creating…") : "Continue")
            }
            .buttonStyle(DSCapsuleStyle(kind: .primary, fullWidth: true))
            .disabled(busy)
            .padding(.top, Space.lg)
            .accessibilityIdentifier("auth.continue")

            HStack(spacing: Space.stack) {
                Rectangle().fill(Palette.hairline).frame(height: 1)
                Text("OR").font(AppFont.eyebrow).tracking(Track.eyebrow)
                    .foregroundStyle(Palette.textSecondary)
                Rectangle().fill(Palette.hairline).frame(height: 1)
            }
            .padding(.top, Space.xl).padding(.bottom, Space.md)

            // Google + Apple, IDENTICAL outline capsules. Apple is required for App Store
            // submission (guideline 4.8 — offering Google alone requires an
            // equivalent privacy-focused option). The AuthManager plumbing
            // (prepareAppleRequest / handleAppleCompletion → Supabase id_token
            // grant) shipped in builds ≤70 and is known-good; only the button
            // that feeds it changed.
            VStack(spacing: Space.stack) {
                AuthOutlineButton(icon: AnyView(GoogleGMark(size: 18, color: Palette.textPrimary)),
                                  label: "Continue with Google") {
                    Task { await store.auth.signInWithGoogle() }
                }
                .disabled(busy)
                .accessibilityIdentifier("auth.google")

                AuthOutlineButton(icon: AnyView(Image(systemName: "apple.logo")
                                                    .font(.system(size: 18))),
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
                    .foregroundStyle(Palette.textSecondary)
                 + Text(currentMode == .signIn ? "Create account" : "Sign in")
                    .font(AppFont.supporting.weight(.semibold))
                    .foregroundStyle(Palette.textPrimary)
                    .underline())
                    .font(AppFont.supporting)
                    .multilineTextAlignment(.center)
                    .frame(minHeight: 44)
            }
            .buttonStyle(.plain)
            .padding(.top, Space.md)
            .accessibilityIdentifier("auth.toggleMode")

            if currentMode == .create {
                Text("By tapping Continue, you agree to our Terms and Privacy Policy.")
                    .font(AppFont.caption)
                    .foregroundStyle(Palette.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, Space.sm)
            }
        }
    }

    /// Lowercase-with-period signature title. VoiceOver reads the sentence-case wording.
    private var title: some View {
        Text(currentMode == .signIn ? "welcome back." : "save your brand.")
            .font(AppFont.title1).tracking(-0.3)
            .foregroundColor(Palette.textPrimary)
            .lineLimit(2).minimumScaleFactor(0.8)
            .accessibilityLabel(currentMode == .signIn ? "Welcome back" : "Save your brand")
            .accessibilityAddTraits(.isHeader)
    }

    private func fieldStroke(focused: Bool, error: Bool) -> Color {
        error ? Palette.textPrimary : (focused ? Palette.textSecondary.opacity(0.6) : .clear)
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

/// One 52pt text row inside the grouped field card.
private struct FieldRowStyle: ViewModifier {
    func body(content: Content) -> some View {
        content
            .font(AppFont.bodyText)
            .foregroundStyle(Palette.textPrimary)
            .tint(Palette.textPrimary)
            .padding(.horizontal, Space.rowPad)
            .frame(height: 52)
    }
}

/// The one outline capsule both social buttons wear. Google and Apple MUST share this
/// — the whole point of dropping SignInWithAppleButton was that its private
/// label font made the two rows visibly different sizes.
private struct AuthOutlineButton: View {
    let icon: AnyView
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                icon
                Text(label)
            }
        }
        .buttonStyle(DSCapsuleStyle(kind: .outline, fullWidth: true))
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
/// textPrimary, so it matches the Apple mark beside it.
struct GoogleGMark: View {
    var size: CGFloat = 18
    var color: Color = Palette.textPrimary

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
