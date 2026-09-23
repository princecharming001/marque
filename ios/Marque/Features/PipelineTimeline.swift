import SwiftUI

// MARK: - Pipeline visibility (build 45)
// The backend runs a take through Upload → Analyze → Edit → Render → Ready and reports
// a granular stage on every poll. iOS used to collapse all of it to one static
// "UPLOADING"/"RENDERING" word, so even a normal 40-second job read as a frozen
// spinner ("stuck in uploading"). PipelineTimeline turns that into a live, aesthetic
// stepper: filled past steps, a shimmering active step with a real progress fill, and
// a plain-English "what's happening" line + ETA. Nothing here changes pipeline
// behavior — it just makes the motion the backend already reports VISIBLE.

/// The four visible phases. `Upload` is device-side (export + PUT); the rest are the
/// backend job stages, coalesced from the finer server statuses.
enum PipelinePhase: Int, CaseIterable {
    case upload, analyze, edit, render

    var label: String {
        switch self {
        case .upload:  return "Upload"
        case .analyze: return "Analyze"
        case .edit:    return "Edit"
        case .render:  return "Render"
        }
    }
    var icon: String {
        switch self {
        case .upload:  return "arrow.up.circle.fill"
        case .analyze: return "waveform"
        case .edit:    return "scissors"
        case .render:  return "sparkles"
        }
    }
    /// The "…ing" why-line shown under the bar for the ACTIVE phase.
    var activeLine: String {
        switch self {
        case .upload:  return "Uploading your take. It resumes automatically if you leave."
        case .analyze: return "Reading your take: transcript, hook, and pacing."
        case .edit:    return "Cutting, captions, and b-roll."
        case .render:  return "Rendering the final video."
        }
    }
}

/// A normalized snapshot of where a clip sits in the pipeline, derived from the Clip's
/// status / uploading / pipelineStage / uploadProgress. `fraction` is the active phase's
/// 0–1 progress when known (upload bytes), else nil → the bar shimmers indeterminately.
struct PipelineProgress {
    let active: PipelinePhase
    let fraction: Double?
    let isFailed: Bool

    /// nil when the clip isn't in-pipeline (draft/ready/scheduled/posted) — the card
    /// then shows its normal chrome, no timeline.
    static func from(_ clip: Clip) -> PipelineProgress? {
        if clip.status == .failed {
            // Show the timeline frozen at wherever it died so the failure has context.
            return PipelineProgress(active: phase(forStage: clip.pipelineStage, uploading: clip.uploading),
                                    fraction: nil, isFailed: true)
        }
        guard clip.status == .rendering else { return nil }
        let active = phase(forStage: clip.pipelineStage, uploading: clip.uploading)
        let frac = active == .upload ? clip.uploadProgress : nil
        return PipelineProgress(active: active, fraction: frac, isFailed: false)
    }

    private static func phase(forStage stage: String?, uploading: Bool) -> PipelinePhase {
        // Device-side upload wins whenever the server hasn't taken over yet.
        if uploading || stage == nil { return .upload }
        switch stage {
        case "transcribing", "analyzing", "scraping", "processing": return .analyze
        case "editing":                                             return .edit
        case "rendering":                                           return .render
        default:                                                    return .analyze
        }
    }
}

/// The compact horizontal stepper shown on in-pipeline clip cards. Stoic progress dashes:
/// four thin monochrome rails (done = solid primary, active = filled to `fraction` or a
/// quiet indeterminate sweep, upcoming = hairline), eyebrow phase names under them on the
/// full variant, and one plain-English active line. Failure is carried by the warning
/// glyph + wording and frozen, dimmed rails, never by a hue.
struct PipelineTimeline: View {
    let progress: PipelineProgress
    var compact: Bool = false
    var showLine: Bool = true

    @State private var shimmer = false
    @State private var pulse = false

    /// Rail fill: primary text tone; a failed run freezes its rails at secondary.
    private var accent: Color { progress.isFailed ? Palette.textSecondary : Palette.textPrimary }

    var body: some View {
        VStack(alignment: .leading, spacing: compact ? 4 : 8) {
            HStack(spacing: compact ? 4 : 8) {
                ForEach(PipelinePhase.allCases, id: \.rawValue) { phase in
                    rail(for: phase)
                }
            }
            if showLine && !compact {
                // Eyebrow phase names, one per dash (DESIGN.md eyebrow: 12 semibold, +2.4).
                HStack(spacing: 8) {
                    ForEach(PipelinePhase.allCases, id: \.rawValue) { phase in
                        Text(phase.label.uppercased())
                            .font(AppFont.eyebrow).tracking(Track.eyebrow)
                            .foregroundStyle(phaseLabelColor(phase))
                            .lineLimit(1).minimumScaleFactor(0.6)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .accessibilityHidden(true)
            }
            if showLine {
                HStack(spacing: 6) {
                    Image(systemName: progress.isFailed ? "exclamationmark.triangle.fill"
                                                        : progress.active.icon)
                        .font(.system(size: compact ? 10 : 12, weight: .semibold))
                        .foregroundStyle(Palette.textPrimary)
                        .opacity(progress.isFailed ? 1 : (pulse ? 1 : 0.5))
                    Text(progress.isFailed ? "Interrupted. Tap to retry." : progress.active.activeLine)
                        .font(compact ? AppFont.caption : AppFont.supporting)
                        .foregroundStyle(progress.isFailed ? Palette.textPrimary : Palette.textSecondary)
                        .lineLimit(1).minimumScaleFactor(0.85)
                }
            }
        }
        .onAppear {
            withAnimation(.easeInOut(duration: 1.1).repeatForever(autoreverses: true)) { pulse = true }
            withAnimation(.linear(duration: 1.3).repeatForever(autoreverses: false)) { shimmer = true }
        }
    }

    private func phaseLabelColor(_ phase: PipelinePhase) -> Color {
        if phase.rawValue == progress.active.rawValue { return Palette.textPrimary }
        if phase.rawValue < progress.active.rawValue && !progress.isFailed { return Palette.textSecondary }
        return Palette.textTertiary      // upcoming steps: decorative / not yet reachable
    }

    @ViewBuilder private func rail(for phase: PipelinePhase) -> some View {
        let isDone = phase.rawValue < progress.active.rawValue && !progress.isFailed
        let isActive = phase.rawValue == progress.active.rawValue
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Palette.hairline)                            // track
                if isDone {
                    Capsule().fill(accent)
                } else if isActive {
                    if let f = progress.fraction, !progress.isFailed {
                        Capsule().fill(accent)
                            .frame(width: max(4, geo.size.width * CGFloat(min(1, max(0.04, f)))))
                            .animation(Motion.standard, value: f)
                    } else if !progress.isFailed {
                        // Indeterminate sweep — the "it's working" signal when there's no
                        // byte %; a moving highlight over a partial fill.
                        Capsule().fill(accent.opacity(0.3))
                        Capsule().fill(accent)
                            .frame(width: geo.size.width * 0.4)
                            .offset(x: shimmer ? geo.size.width * 0.6 : -geo.size.width * 0.4)
                            .mask(Capsule())
                    } else {
                        Capsule().fill(accent.opacity(0.6))
                    }
                }
            }
        }
        .frame(height: compact ? 2 : 3)
        .clipShape(Capsule())
    }
}
