import SwiftUI

/// The Yunicorn voice orb: a single drop of warm ink on the paper. Nothing lives inside
/// the disc on purpose (no gradient, no glow, no hue, no image): every bit of state is
/// carried by its EDGE, a contour that wobbles like a hand-drawn circle and deforms in
/// proportion to the voice.
///
///   idle       quiet breathing, faint ripple
///   listening  ripple + swell track the mic; the rim takes the accent blue
///   thinking   the boil freezes, the body leans into a lopsided lump and turns: one
///              object at work, kneading slowly
///   speaking   the body pulses with TTS amplitude (low lobes); the rim stays ink
///
/// Canvas only. The hot loop is multiply-adds over precomputed tables, and all
/// irregularity comes from seeded harmonic phases, so any frame is reproducible.
struct VoiceOrb: View {
    enum Mode { case idle, listening, thinking, speaking }
    var mode: Mode = .idle
    var level: Double = 0
    var size: CGFloat = 132

    @State private var physics = OrbPhysics()
    private var clampedLevel: Double { min(1, max(0, level)) }

    var body: some View {
        TimelineView(.animation) { timeline in
            let frame = physics.step(now: timeline.date.timeIntervalSinceReferenceDate,
                                     rawLevel: clampedLevel, mode: mode)
            Canvas { ctx, canvas in
                InkDrop.draw(frame, in: &ctx, canvas: canvas, orbSize: size)
            }
            // Overscan so spring overshoot and the shadow's blur tail never clip. SwiftUI
            // frames don't clip, so the layout footprint stays exactly `size`; the canvas
            // itself is not hit-testable so the overscan never widens a wrapping Button.
            .frame(width: size * InkDrop.overscan, height: size * InkDrop.overscan)
            .allowsHitTesting(false)
        }
        .frame(width: size, height: size)
        .contentShape(Circle())
        .accessibilityHidden(true)
    }
}

// MARK: - Geometry + paint

/// Static tables and the per-frame draw. The frame loop builds one Path and allocates
/// nothing else; harmonic terms come from lookup, not trig.
private enum InkDrop {
    static let overscan: CGFloat = 1.3

    // Brand, hardcoded so the file is self-contained. Paper is 0xF1F1EF and is never
    // drawn here: the orb is only ever ink, one accent, and its own warm shadow.
    static let ink    = Color(red: 28 / 255.0, green: 26 / 255.0, blue: 23 / 255.0)   // 0x1C1A17
    static let accent = Color(red: 44 / 255.0, green: 107 / 255.0, blue: 237 / 255.0) // 0x2C6BED
    static let shadow = Color(red: 46 / 255.0, green: 42 / 255.0, blue: 32 / 255.0)   // 0x2E2A20

    /// One radial harmonic; `k` is its lobe count around the rim. 6 and 7 carry the
    /// hand-drawn wobble, 2 and 3 lean the body, 9 adds grain. Rates differ in sign and
    /// magnitude so the silhouette never repeats; phases are fixed seeds, not random.
    struct Harmonic {
        let k: Int
        let weight: Double
        let rate: Double    // phase advance per unit of the volume-warped clock
        let phase: Double   // seed
        let spring: Int     // which staggered spring drives this lobe's amplitude
    }
    static let harmonics: [Harmonic] = [
        Harmonic(k: 2, weight: 0.30, rate:  0.31, phase: 0.80, spring: 0),
        Harmonic(k: 3, weight: 0.45, rate: -0.47, phase: 2.10, spring: 2),
        Harmonic(k: 6, weight: 0.75, rate:  0.62, phase: 4.30, spring: 1),
        Harmonic(k: 7, weight: 1.00, rate: -0.83, phase: 1.35, spring: 3),
        Harmonic(k: 9, weight: 0.30, rate:  1.10, phase: 5.60, spring: 1),
    ]
    static let harmonicCount = harmonics.count

    /// 144 samples: at the largest radius the chord is ~4pt and the sagitta ~0.03pt, so
    /// straight segments read as a curve, and the 9-lobe term still gets 16 samples per lobe.
    static let samples = 144

    /// cos θ, sin θ per sample (2 entries each).
    static let unit: [Double] = {
        var out: [Double] = []
        out.reserveCapacity(samples * 2)
        for i in 0..<samples {
            let t = 2 * Double.pi * Double(i) / Double(samples)
            out.append(cos(t)); out.append(sin(t))
        }
        return out
    }()

    /// cos kθ, sin kθ per sample per harmonic (sample-major, 2 entries per harmonic), so
    /// cos(kθ + φ) per point is two multiplies and a subtract.
    static let table: [Double] = {
        var out: [Double] = []
        out.reserveCapacity(samples * harmonicCount * 2)
        for i in 0..<samples {
            let t = 2 * Double.pi * Double(i) / Double(samples)
            for h in harmonics {
                let kt = Double(h.k) * t
                out.append(cos(kt)); out.append(sin(kt))
            }
        }
        return out
    }()

    // Deformation budget as a fraction of radius per unit of harmonic weight. Weights sum
    // to 2.8, and a sum of cosines typically sits near 60% of its bound. Tuned so a
    // loud syllable at 132pt moves the rim about ±7pt and swells the disc about 5pt.
    static let rippleRest = 0.012   // idle: about ±2% of radius, quietly alive
    static let rippleGain = 0.053   // full-level hit: about ±12%, more on overshoot
    static let swellGain  = 0.113   // whole-disc scale per unit of the main spring

    static func draw(_ f: OrbPhysics.Frame, in ctx: inout GraphicsContext, canvas: CGSize, orbSize: CGFloat) {
        let cx = Double(canvas.width) / 2
        let cy = Double(canvas.height) / 2
        let idle = max(0, 1 - f.listen - f.think - f.speak)
        let breath = 0.5 + 0.5 * sin(f.elapsed * 0.6)
        let scale = 1 + swellGain * f.swell * (1 + 0.3 * f.speak) + 0.018 * idle * breath
        let radius = Double(orbSize) * 0.44 * scale

        // Per-harmonic gain and phase, once per frame. Listening leans on the fine lobes
        // (a membrane picking up sound), speaking on the low ones (a body pushing it out).
        // Thinking switches the fine wobble off and leans hard on the low ones, so even a
        // still frame reads as a smooth kneaded lump, the opposite of idle's grainy rim.
        // The rigid spin folds into the phase: cos(k(θ − spin) + φ).
        var gain = SIMD8<Double>()
        var cp = SIMD8<Double>()
        var sp = SIMD8<Double>()
        for h in 0..<harmonicCount {
            let hm = harmonics[h]
            let drive = rippleRest + rippleGain * f.lobes[hm.spring]
            let emphasis = hm.k >= 6
                ? 1 + 0.45 * f.listen - 0.25 * f.speak - 0.95 * f.think
                : 1 + 0.90 * f.speak - 0.20 * f.listen + 4.00 * f.think
            gain[h] = hm.weight * drive * emphasis
            let phi = hm.phase + hm.rate * f.clock - Double(hm.k) * f.spin
            cp[h] = cos(phi)
            sp[h] = sin(phi)
        }

        var path = Path()
        let rowLength = harmonicCount * 2
        for i in 0..<samples {
            let row = i * rowLength
            var d = 0.0
            for h in 0..<harmonicCount {
                d += gain[h] * (table[row + 2 * h] * cp[h] - table[row + 2 * h + 1] * sp[h])
            }
            let r = radius * (1 + d)
            let p = CGPoint(x: cx + unit[2 * i] * r, y: cy + unit[2 * i + 1] * r)
            if i == 0 { path.move(to: p) } else { path.addLine(to: p) }
        }
        path.closeSubpath()

        // Warm cast shadow: the drop sits on the paper instead of floating in it.
        ctx.drawLayer { layer in
            layer.addFilter(.blur(radius: orbSize * 0.045))
            layer.translateBy(x: 0, y: orbSize * 0.035)
            layer.fill(path, with: .color(shadow.opacity(0.22)))
        }

        // Listening rim: stroked under the fill so only the outer half shows, a clean
        // accent edge that thickens with the voice. Eased by the mode blend, never popped.
        if f.listen > 0.004 {
            let width = orbSize * (0.024 + 0.022 * f.envelope)
            ctx.stroke(path,
                       with: .color(accent.opacity(f.listen * (0.75 + 0.25 * f.envelope))),
                       style: StrokeStyle(lineWidth: width, lineJoin: .round))
        }

        ctx.fill(path, with: .color(ink))
    }
}

// MARK: - Physics

/// Envelope follower + damped springs + volume-warped clock. The asymmetric follower
/// makes each syllable register as a distinct hit; the springs turn hits into elastic
/// bounces with overshoot; per-lobe spring constants stagger the response so the edge
/// ripples instead of merely scaling. Mode blends are eased here so a mode switch never
/// snaps colour or shape.
private final class OrbPhysics {
    struct Frame {
        var elapsed: Double        // plain seconds, drives idle breathing
        var clock: Double          // volume-warped clock the harmonic phases advance on
        var spin: Double           // rigid rotation of the silhouette (thinking)
        var envelope: Double       // followed level
        var swell: Double          // main spring: whole-disc scale
        var lobes: SIMD4<Double>   // staggered springs: per-harmonic ripple amplitude
        var listen: Double         // eased mode blends, 0..1, they sum to at most 1
        var think: Double
        var speak: Double
    }

    private struct Spring {
        var pos = 0.0
        var vel = 0.0
        let stiffness: Double
        let damping: Double
        mutating func step(target: Double, dt: Double) {
            let acc = stiffness * (target - pos) - damping * vel
            vel += acc * dt
            pos += vel * dt
            if pos < 0 { pos = 0; vel = max(0, vel) }
        }
    }

    private var lastTime: Double?
    private var elapsed = 0.0
    private var clock = 0.0
    private var spin = 0.0
    private var spinVel = 0.0
    private var envelope = 0.0
    private var listen = 0.0
    private var think = 0.0
    private var speak = 0.0
    private var swell = Spring(stiffness: 140, damping: 11)
    private var lobeSprings = [
        Spring(stiffness: 120, damping: 10),
        Spring(stiffness: 165, damping: 13),
        Spring(stiffness: 95,  damping: 9),
        Spring(stiffness: 145, damping: 12),
    ]

    func step(now: Double, rawLevel: Double, mode: VoiceOrb.Mode) -> Frame {
        // Wall time is clamped so a resume from background is at most 0.1s of motion, and
        // that span is integrated in slices no longer than a 60Hz frame: the springs are
        // stiff (k up to 165) and a single 0.1s Euler step would overshoot into a star.
        let span = lastTime.map { min(0.1, max(0, now - $0)) } ?? 1.0 / 60.0
        lastTime = now
        let slices = max(1, Int((span * 60).rounded(.up)))
        let dt = span / Double(slices)
        for _ in 0..<slices { integrate(dt: dt, rawLevel: rawLevel, mode: mode) }

        return Frame(
            elapsed: elapsed, clock: clock, spin: spin, envelope: envelope,
            swell: swell.pos,
            lobes: SIMD4(lobeSprings[0].pos, lobeSprings[1].pos, lobeSprings[2].pos, lobeSprings[3].pos),
            listen: listen, think: think, speak: speak
        )
    }

    private func integrate(dt: Double, rawLevel: Double, mode: VoiceOrb.Mode) {
        elapsed += dt

        let target: Double
        switch mode {
        case .idle:      target = 0.05 + 0.04 * sin(elapsed * 0.7)
        // Thinking kneads: a slow ~0.4Hz swell under the turning lump, nothing voice-like.
        case .thinking:  target = 0.45 + 0.15 * sin(elapsed * 2.4)
        case .listening, .speaking: target = rawLevel
        }
        // ~35ms attack so every syllable lands as its own hit; ~220ms release so hits
        // decay instead of flickering with the waveform.
        let rate = target > envelope ? 28.0 : 4.5
        envelope += (target - envelope) * min(1, rate * dt)

        swell.step(target: envelope, dt: dt)
        for i in lobeSprings.indices { lobeSprings[i].step(target: envelope, dt: dt) }

        // Boil rate: how fast the lobes drift around the rim. Thinking nearly freezes it
        // so the rigid spin reads as one object turning rather than a shape morphing.
        let base: Double
        switch mode {
        case .idle:      base = 0.45
        case .thinking:  base = 0.10
        case .listening, .speaking: base = 0.7
        }
        let voiced = mode == .listening || mode == .speaking
        let agitation = voiced ? 1.9 * envelope + 0.10 * min(3, abs(swell.vel)) : 0
        clock += dt * (base + agitation)

        // Spin eases in and coasts out, so entering/leaving thinking never jerks. Integer
        // lobe counts make wrapping at 2π invisible. ~0.95 rad/s: a 3-lobe lump cycles
        // every ~2.2s, slow enough to read as turning, fast enough to read as busy.
        let spinTarget = mode == .thinking ? 0.95 : 0.0
        spinVel += (spinTarget - spinVel) * min(1, 3 * dt)
        spin = (spin + dt * spinVel).truncatingRemainder(dividingBy: 2 * .pi)

        let ease = min(1, 7 * dt)
        listen += ((mode == .listening ? 1.0 : 0.0) - listen) * ease
        think  += ((mode == .thinking  ? 1.0 : 0.0) - think)  * ease
        speak  += ((mode == .speaking  ? 1.0 : 0.0) - speak)  * ease
    }
}
