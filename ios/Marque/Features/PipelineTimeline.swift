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
        case .upload:  return "Uploading your take — it resumes automatically if you leave."
        case .analyze: return "Reading your take — transcript, hook, and pacing."
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

/// The compact horizontal stepper shown on in-pipeline clip cards. Aesthetic: four
/// segmented rails in the ink/gold palette; completed rails fill solid, the active rail
/// fills to `fraction` (or shimmers when indeterminate), upcoming rails stay hairline.
/// A pulsing dot + icon marks the active phase.
struct PipelineTimeline: View {
    let progress: PipelineProgress
    var compact: Bool = false
    var showLine: Bool = true

    @State private var shimmer = false
    @State private var pulse = false

    private var accent: Color { progress.isFailed ? Palette.critical : Palette.accent }

    var body: some View {
        VStack(alignment: .leading, spacing: compact ? 5 : 8) {
            HStack(spacing: compact ? 4 : 6) {
                ForEach(PipelinePhase.allCases, id: \.rawValue) { phase in
                    rail(for: phase)
                }
            }
            if showLine {
                HStack(spacing: 5) {
                    Image(systemName: progress.isFailed ? "exclamationmark.triangle.fill"
                                                        : progress.active.icon)
                        .font(.system(size: compact ? 9 : 10, weight: .semibold))
                        .foregroundStyle(accent)
                        .opacity(progress.isFailed ? 1 : (pulse ? 1 : 0.5))
                    Text(progress.isFailed ? "Interrupted — tap to retry." : progress.active.activeLine)
                        .font(.system(size: compact ? 9.5 : 11))
                        .foregroundStyle(Palette.textSecondary)
                        .lineLimit(1)
                }
            }
        }
        .onAppear {
            withAnimation(.easeInOut(duration: 1.1).repeatForever(autoreverses: true)) { pulse = true }
            withAnimation(.linear(duration: 1.3).repeatForever(autoreverses: false)) { shimmer = true }
        }
    }

    @ViewBuilder private func rail(for phase: PipelinePhase) -> some View {
        let isDone = phase.rawValue < progress.active.rawValue && !progress.isFailed
        let isActive = phase.rawValue == progress.active.rawValue
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Palette.hairline.opacity(0.5))            // track
                if isDone {
                    Capsule().fill(accent)
                } else if isActive {
                    if let f = progress.fraction, !progress.isFailed {
                        Capsule().fill(accent)
                            .frame(width: max(4, geo.size.width * CGFloat(min(1, max(0.04, f)))))
                            .animation(.spring(response: 0.5, dampingFraction: 0.85), value: f)
                    } else if !progress.isFailed {
                        // Indeterminate shimmer sweep — the "it's working" signal when there's
                        // no byte %; a moving highlight over a partial fill.
                        Capsule().fill(accent.opacity(0.35))
                        Capsule().fill(accent)
                            .frame(width: geo.size.width * 0.4)
                            .offset(x: shimmer ? geo.size.width * 0.6 : -geo.size.width * 0.4)
                            .mask(Capsule())
                    } else {
                        Capsule().fill(accent.opacity(0.5))
                    }
                }
            }
        }
        .frame(height: compact ? 3 : 4)
    }
}
