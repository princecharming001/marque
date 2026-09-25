import CoreImage
import CoreImage.CIFilterBuiltins
import Foundation

// TikTok-style "Retouch": live skin smoothing baked into both the camera preview and
// the recorded take.
//
// REBUILT 2026-09-25 (owner: "it should clear up your skin and imperfections exactly
// like TikTok's filter, not just make the screen blurrier"). The first version smoothed
// the WHOLE frame behind an edge matte, so hair, clothes and the room went soft along
// with the skin. This one smooths only skin:
//
//   1. a SKIN mask from the colour itself (a colour cube that lights up skin hues at
//      skin saturation), softened, and confined to the detected face region(s) with a
//      generous margin for forehead and neck (no face found yet → skin colour alone);
//   2. an EDGE matte from the original so eyes, lips, brows, glasses and the hairline
//      stay crisp inside that region;
//   3. a SMOOTH PLATE: a small morphological close first (dark blemishes and pores
//      fill in the way a blemish tool does), then a blur, then part of the original
//      texture mixed back so the skin keeps its grain instead of turning to plastic;
//   4. composite the plate over the original through skin × face × edge × strength,
//      plus a whisper of lift on the smoothed skin so it reads as good light.
//
// Everything is built-in Core Image, so there is no custom Metal to maintain, and the
// masks and the plate are computed at half resolution (the result is soft by nature),
// which keeps the whole thing real-time at 1080p30 on any iOS 17 device.
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

    /// `strength` 0…1 (TikTok's slider). 0 returns the input untouched. `faces` are
    /// normalized (0…1, origin bottom-left, in `image`'s own coordinate space — what
    /// Vision's `boundingBox` gives for an `.up` image); empty means "not known yet".
    static func apply(to image: CIImage, strength: CGFloat, faces: [CGRect] = []) -> CIImage {
        let s = min(max(strength, 0), 1)
        guard s > 0.01 else { return image }
        let extent = image.extent
        guard !extent.isInfinite, extent.width > 32, extent.height > 32 else { return image }
        // Radii scale with frame size so the look is identical across 720p/1080p/4K.
        let unit = max(extent.width, extent.height) / 1920.0

        // Half-resolution working copy for the masks and the plate.
        let half = image.transformed(by: CGAffineTransform(scaleX: 0.5, y: 0.5))
        let hExtent = half.extent
        let hUnit = unit * 0.5
        guard let mask = skinMask(half: half, hUnit: hUnit, strength: s, faces: faces)?
                .transformed(by: CGAffineTransform(scaleX: 2, y: 2)).cropped(to: extent) else { return image }

        // 3. The smooth plate. A small close (dilate → erode) fills dark blemishes and
        //    pores; the blur evens the tone; mixing the original back keeps real grain.
        var plate: CIImage = half
        if s > 0.25 {
            let r = Float((3.0 + 3.0 * s) * hUnit)       // 1080p: 3–6 px, the size of a blemish
            let dilate = CIFilter.morphologyMaximum()
            dilate.inputImage = half.clampedToExtent()
            dilate.radius = r
            let erode = CIFilter.morphologyMinimum()
            erode.inputImage = dilate.outputImage
            erode.radius = r
            plate = erode.outputImage?.cropped(to: hExtent) ?? half
        }
        let blur = CIFilter.gaussianBlur()
        blur.inputImage = plate.clampedToExtent()
        blur.radius = Float((5.0 + 7.0 * s) * hUnit)    // 1080p: 5–12 px, evens tone across a cheek
        guard let low = blur.outputImage?.cropped(to: hExtent) else { return image }
        let texture = CIFilter.blendWithMask()
        texture.inputImage = low
        texture.backgroundImage = half
        let keep = 0.60 + 0.30 * s   // share of the plate that is smoothed; the rest is real skin grain
        texture.maskImage = CIImage(color: CIColor(red: keep, green: keep, blue: keep)).cropped(to: hExtent)
        let lit = CIFilter.colorControls()
        lit.inputImage = texture.outputImage ?? low
        lit.brightness = Float(0.014 * s)
        lit.saturation = Float(1.0 + 0.03 * s)
        lit.contrast = 1.0
        guard let plateH = lit.outputImage else { return image }
        let plateFull = plateH.transformed(by: CGAffineTransform(scaleX: 2, y: 2)).cropped(to: extent)

        // 4. Composite: smoothed skin over the original, only where the mask says skin.
        let blend = CIFilter.blendWithMask()
        blend.inputImage = plateFull
        blend.backgroundImage = image
        blend.maskImage = mask
        return (blend.outputImage ?? image).cropped(to: extent)
    }


    /// The smoothing mask at the working (half) resolution: skin colour × face region ×
    /// flat (non-edge) × strength. Exposed so the offline harness can look at it.
    static func skinMask(half: CIImage, hUnit: CGFloat, strength s: CGFloat, faces: [CGRect]) -> CIImage? {
        let hExtent = half.extent
        // 1. Skin mask: colour cube (skin hue × skin saturation × not-too-dark) → soften.
        let cube = CIFilter.colorCube()
        cube.inputImage = half
        cube.cubeDimension = Float(skinCubeDimension)
        cube.cubeData = skinCubeData
        let skinBlur = CIFilter.gaussianBlur()
        skinBlur.inputImage = cube.outputImage?.clampedToExtent()
        skinBlur.radius = Float(5.0 * hUnit)
        guard let skin = skinBlur.outputImage?.cropped(to: hExtent) else { return nil }

        // Face region: the detected rectangles, widened for forehead, neck and ears,
        // feathered at the border. Without a detection the region is the whole frame and
        // the skin colour alone decides (still far better than smoothing everything).
        var region: CIImage? = nil
        if !faces.isEmpty {
            var canvas = CIImage(color: .black).cropped(to: hExtent)
            for f in faces {
                let w = f.width * hExtent.width, h = f.height * hExtent.height
                let r = CGRect(x: hExtent.minX + f.minX * hExtent.width - w * 0.30,
                               y: hExtent.minY + f.minY * hExtent.height - h * 0.45,
                               width: w * 1.60, height: h * 1.75)
                    .intersection(hExtent)
                guard !r.isNull, r.width > 4, r.height > 4 else { continue }
                canvas = CIImage(color: .white).cropped(to: r).composited(over: canvas)
            }
            let feather = CIFilter.gaussianBlur()
            feather.inputImage = canvas.clampedToExtent()
            feather.radius = Float(max(6.0 * hUnit, 0.06 * faces.map { $0.width * hExtent.width }.max()!))
            region = feather.outputImage?.cropped(to: hExtent)
        }

        // 2. Feature protection from LOCAL CONTRAST (what a bilateral filter keys on): how far
        //    each pixel sits from its neighbourhood's average. Eyes, lash lines, brows, the
        //    lip line and nostrils deviate a lot and are protected; pores, blemishes and
        //    uneven tone deviate a little and get smoothed. Soft threshold between the two,
        //    then a small dilate + feather so each feature keeps a clean halo.
        let local = CIFilter.gaussianBlur()
        local.inputImage = half.clampedToExtent()
        local.radius = Float(6.0 * hUnit)
        guard let localMean = local.outputImage?.cropped(to: hExtent) else { return nil }
        let diff = CIFilter.differenceBlendMode()
        diff.inputImage = half
        diff.backgroundImage = localMean
        // |Δ| luminance → ramp: below ~0.035 = skin texture (0), above ~0.10 = feature (1).
        let lum = CIFilter.colorMatrix()
        lum.inputImage = diff.outputImage
        let k: CGFloat = 15
        let lr = CIVector(x: 0.30 * k, y: 0.59 * k, z: 0.11 * k, w: 0)
        lum.rVector = lr; lum.gVector = lr; lum.bVector = lr
        lum.aVector = CIVector(x: 0, y: 0, z: 0, w: 1)
        lum.biasVector = CIVector(x: -0.5, y: -0.5, z: -0.5, w: 0)
        let clamp = CIFilter.colorClamp()
        clamp.inputImage = lum.outputImage
        clamp.minComponents = CIVector(x: 0, y: 0, z: 0, w: 1)
        clamp.maxComponents = CIVector(x: 1, y: 1, z: 1, w: 1)
        let grow = CIFilter.morphologyMaximum()
        grow.inputImage = clamp.outputImage?.clampedToExtent()
        grow.radius = Float(2.0 * hUnit)
        let soften = CIFilter.gaussianBlur()
        soften.inputImage = grow.outputImage
        soften.radius = Float(2.5 * hUnit)
        let inverted = CIFilter.colorInvert()
        inverted.inputImage = soften.outputImage?.cropped(to: hExtent)
        guard let edgeMatte = inverted.outputImage else { return nil }

        // Combine skin × region × edge, then let the slider scale the mix.
        var maskH: CIImage = multiply(skin, edgeMatte) ?? skin
        if let region { maskH = multiply(maskH, region) ?? maskH }
        let gained = CIFilter.colorMatrix()
        gained.inputImage = maskH
        let g = CGFloat(0.55) + 0.45 * s
        gained.rVector = CIVector(x: g, y: 0, z: 0, w: 0)
        gained.gVector = CIVector(x: 0, y: g, z: 0, w: 0)
        gained.bVector = CIVector(x: 0, y: 0, z: g, w: 0)
        return gained.outputImage
    }

    private static func multiply(_ a: CIImage, _ b: CIImage) -> CIImage? {
        let m = CIFilter.multiplyCompositing()
        m.inputImage = a
        m.backgroundImage = b
        return m.outputImage
    }

    // MARK: Skin colour cube

    static let skinCubeDimension = 32

    /// A 32³ lookup that maps every colour to a skin likelihood (0…1, written to RGB with
    /// alpha 1). Skin lives in a narrow hue band (red-orange, roughly 0–45°), at moderate
    /// saturation (0.15–0.7, so neither grey walls nor saturated clothes) and above the
    /// darkest tones. Each window has soft shoulders so the mask fades instead of cutting.
    static let skinCubeData: Data = {
        let n = skinCubeDimension
        var cube = [Float](repeating: 0, count: n * n * n * 4)
        func ramp(_ x: Float, _ a: Float, _ b: Float) -> Float {   // 0 at a → 1 at b (either order)
            if a == b { return x >= a ? 1 : 0 }
            let t = min(max((x - a) / (b - a), 0), 1)
            return t * t * (3 - 2 * t)
        }
        var i = 0
        for b in 0..<n {
            for g in 0..<n {
                for r in 0..<n {
                    let rf = Float(r) / Float(n - 1), gf = Float(g) / Float(n - 1), bf = Float(b) / Float(n - 1)
                    let (h, s, v) = hsv(rf, gf, bf)
                    // Hue: full from 0° to 42°, gone by 55°; wraps for the pinker tones down to 345°.
                    let hueScore: Float
                    if h <= 42 { hueScore = 1 }
                    else if h < 55 { hueScore = 1 - ramp(h, 42, 55) }
                    else if h >= 345 { hueScore = ramp(h, 345, 358) }
                    else { hueScore = 0 }
                    let satScore = ramp(s, 0.09, 0.18) * (1 - ramp(s, 0.66, 0.86))
                    let valScore = ramp(v, 0.18, 0.34)
                    let score = hueScore * satScore * valScore
                    cube[i] = score; cube[i + 1] = score; cube[i + 2] = score; cube[i + 3] = 1
                    i += 4
                }
            }
        }
        return cube.withUnsafeBufferPointer { Data(buffer: $0) }
    }()

    /// (hue in degrees 0…360, saturation 0…1, value 0…1)
    private static func hsv(_ r: Float, _ g: Float, _ b: Float) -> (Float, Float, Float) {
        let mx = max(r, g, b), mn = min(r, g, b)
        let d = mx - mn
        var h: Float = 0
        if d > 1e-5 {
            if mx == r { h = 60 * ((g - b) / d).truncatingRemainder(dividingBy: 6) }
            else if mx == g { h = 60 * ((b - r) / d + 2) }
            else { h = 60 * ((r - g) / d + 4) }
            if h < 0 { h += 360 }
        }
        let s: Float = mx > 1e-5 ? d / mx : 0
        return (h, s, mx)
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
