import CoreImage
import CoreImage.CIFilterBuiltins
import Foundation

// TikTok-style "Retouch" (owner, 2026-08-22): live skin smoothing baked into both the
// camera preview and the recorded take. The look is EDGE-PRESERVING smoothing — a
// bilateral-filter approximation built entirely from built-in CIFilters so there are
// no custom Metal kernels to maintain:
//
//   1. blur the frame (what skin should look like),
//   2. build an EDGE matte from the original (eyes, lips, hairline, glasses —
//      everything that must stay crisp),
//   3. composite: blurred where the matte is flat, original where it has edges,
//   4. finish with the "enhance" half of TikTok's toggle: a whisper of luminance
//      sharpening + brightness/saturation lift so the result reads as "good light",
//      not "vaseline lens".
//
// Deliberately full-frame rather than Vision-face-masked: the smoothing only changes
// low-frequency regions (the edge matte protects everything with detail), a matte
// follows head movement with zero lag or face-tracking jitter, and it's ~3 filter
// passes — comfortably real-time at 1080p30 on any device that runs iOS 17.
enum FaceRetouch {

    /// One Metal-backed context shared by the record path and any offline callers.
    /// (The preview MTKView keeps its own context bound to its drawable's device.)
    static let context: CIContext = {
        // Explicit working space keeps the render pipeline linear-light while the
        // output stays sRGB — matching what AVCaptureVideoPreviewLayer showed, so
        // toggling retouch never causes a color shift.
        CIContext(options: [.workingColorSpace: CGColorSpace(name: CGColorSpace.sRGB) as Any,
                            .cacheIntermediates: false])
    }()

    /// `strength` 0…1 (TikTok's slider). 0 returns the input untouched.
    static func apply(to image: CIImage, strength: CGFloat) -> CIImage {
        let s = min(max(strength, 0), 1)
        guard s > 0.01 else { return image }
        let extent = image.extent
        guard !extent.isInfinite, extent.width > 16, extent.height > 16 else { return image }
        // Radii scale with frame size so the look is identical across 720p/1080p/4K.
        let unit = max(extent.width, extent.height) / 1920.0

        // 1. The smooth plate. clamped first so the gaussian doesn't pull in
        //    transparent black at the borders (visible as a dark vignette).
        let blur = CIFilter.gaussianBlur()
        blur.inputImage = image.clampedToExtent()
        blur.radius = Float((5.0 + 7.0 * s) * unit)
        guard let blurred = blur.outputImage?.cropped(to: extent) else { return image }

        // 2. Edge matte: CIEdges → soften → grayscale → invert. White = flat (smooth
        //    freely), black = detail (keep the original). The pre-invert blur dilates
        //    the protected band around each edge so features keep a clean halo.
        let edges = CIFilter.edges()
        edges.inputImage = image
        edges.intensity = 6
        let edgeBlur = CIFilter.gaussianBlur()
        edgeBlur.inputImage = edges.outputImage?.clampedToExtent()
        edgeBlur.radius = Float(2.5 * unit)
        let mono = CIFilter.colorControls()
        mono.inputImage = edgeBlur.outputImage?.cropped(to: extent)
        mono.saturation = 0
        mono.contrast = 1.6            // snap the matte toward binary keep/smooth
        mono.brightness = 0
        let inverted = CIFilter.colorInvert()
        inverted.inputImage = mono.outputImage
        // Strength rides the matte itself (scale its luminance) so the slider blends
        // smoothly from untouched to fully smoothed instead of gating on/off.
        let gained = CIFilter.colorMatrix()
        gained.inputImage = inverted.outputImage
        let g = CGFloat(0.9) * s
        gained.rVector = CIVector(x: g, y: 0, z: 0, w: 0)
        gained.gVector = CIVector(x: 0, y: g, z: 0, w: 0)
        gained.bVector = CIVector(x: 0, y: 0, z: g, w: 0)
        guard let matte = gained.outputImage else { return image }

        // 3. Composite smooth-over-original through the matte.
        let blend = CIFilter.blendWithMask()
        blend.inputImage = blurred
        blend.backgroundImage = image
        blend.maskImage = matte
        guard let smoothed = blend.outputImage else { return image }

        // 4. The "enhance" finish. Values are deliberately timid — at slider max this
        //    is +3% brightness and +6% saturation; anything stronger reads as a filter
        //    instead of a good camera.
        let sharpen = CIFilter.sharpenLuminance()
        sharpen.inputImage = smoothed
        sharpen.sharpness = Float(0.18 * s)
        let tone = CIFilter.colorControls()
        tone.inputImage = sharpen.outputImage ?? smoothed
        tone.brightness = Float(0.018 * s)
        tone.saturation = Float(1.0 + 0.06 * s)
        tone.contrast = 1.0
        return (tone.outputImage ?? smoothed).cropped(to: extent)
    }
}

/// Persisted retouch preference — set on the record screen, applied by CameraModel.
/// Plain UserDefaults (not the AppStore state tree): it's a device-local capture
/// preference like torch or grid, not brand/content state worth syncing.
enum RetouchSettings {
    private static let enabledKey = "record.retouch.enabled"
    private static let strengthKey = "record.retouch.strength"

    static var enabled: Bool {
        get { UserDefaults.standard.bool(forKey: enabledKey) }
        set { UserDefaults.standard.set(newValue, forKey: enabledKey) }
    }

    /// 0.1…1.0; defaults to TikTok's out-of-the-box middle feel.
    static var strength: Double {
        get {
            let v = UserDefaults.standard.double(forKey: strengthKey)
            return v == 0 ? 0.55 : min(max(v, 0.1), 1.0)
        }
        set { UserDefaults.standard.set(min(max(newValue, 0.1), 1.0), forKey: strengthKey) }
    }
}
