import SwiftUI
import AVFoundation
import Vision
import CoreImage
import MetalKit

// Real AVFoundation capture. On a physical device this records actual video.
// The iOS Simulator has no camera, so `hasCamera` is false there and RecordView
// falls back to a simulated capture so the flow stays testable. (05-screens-produce.md)
//
// REBUILT 2026-08-22 for TikTok-style Retouch (owner request): AVCaptureMovieFileOutput
// and AVCaptureVideoPreviewLayer are both closed pipes — neither can run a per-frame
// filter — so the capture path is now data outputs → CoreImage → AVAssetWriter, with
// the SAME filtered frame feeding the Metal preview and the recorded file. What you
// see is exactly what gets recorded, which is the whole contract of a retouch toggle.
// With retouch off the pixel buffers pass through untouched (no render, no re-encode
// difference), so the rebuilt path costs nothing when the feature isn't in use.
final class CameraModel: NSObject, ObservableObject {
    enum Status { case idle, ready, recording, unavailable }
    @Published var status: Status = .idle
    @Published var hasAudio = false     // false when mic permission was denied → warn the user

    @Published var position: AVCaptureDevice.Position = .front

    // Retouch (persisted): the record screen binds these; the capture queue reads a
    // lock-guarded snapshot (published vars must never be touched off-main).
    @Published var retouchEnabled: Bool = RetouchSettings.enabled {
        didSet {
            RetouchSettings.enabled = retouchEnabled
            stateLock.lock(); _retouchOn = retouchEnabled; stateLock.unlock()
        }
    }
    @Published var retouchStrength: Double = RetouchSettings.strength {
        didSet {
            RetouchSettings.strength = retouchStrength
            stateLock.lock(); _strength = retouchStrength; stateLock.unlock()
        }
    }

    let session = AVCaptureSession()
    private let videoOutput = AVCaptureVideoDataOutput()
    private let audioOutput = AVCaptureAudioDataOutput()
    private let q = DispatchQueue(label: "marque.camera.session")
    /// Dedicated capture-callback queue: filtering happens here, so a slow frame can
    /// never block session configuration (and alwaysDiscardsLateVideoFrames keeps the
    /// preview current instead of building latency).
    private let captureQ = DispatchQueue(label: "marque.camera.capture")
    private var videoInput: AVCaptureDeviceInput?

    /// Latest preview frame, handed to the Metal preview view. Lock-guarded — written
    /// on the capture queue, read on the MTKView draw loop.
    final class PreviewTap {
        private let lock = NSLock()
        private var image: CIImage?
        func set(_ img: CIImage) { lock.lock(); image = img; lock.unlock() }
        func take() -> CIImage? { lock.lock(); defer { lock.unlock() }; return image }
    }
    let previewTap = PreviewTap()
    /// Face rectangles for the retouch mask (Vision, ~5 Hz on its own queue).
    private let faces = FaceTracker()

    // Capture-thread state (never touched from main). The published retouch vars are
    // mirrored into these under stateLock.
    private let stateLock = NSLock()
    private var _retouchOn = RetouchSettings.enabled
    private var _strength = RetouchSettings.strength

    // Writer state — owned by captureQ exclusively.
    private var writer: AVAssetWriter?
    private var writerVideo: AVAssetWriterInput?
    private var writerAudio: AVAssetWriterInput?
    private var adaptor: AVAssetWriterInputPixelBufferAdaptor?
    private var writerURL: URL?
    private var sessionStarted = false
    private var isCapturingTake = false
    private var onFinish: ((URL?) -> Void)?

    var hasCamera: Bool {
        #if targetEnvironment(simulator)
        return false
        #else
        return AVCaptureDevice.default(for: .video) != nil
        #endif
    }

    private func setStatus(_ s: Status) { DispatchQueue.main.async { self.status = s } }

    func configure() {
        guard hasCamera else { setStatus(.unavailable); return }
        AVCaptureDevice.requestAccess(for: .video) { granted in
            guard granted else { self.setStatus(.unavailable); return }
            AVCaptureDevice.requestAccess(for: .audio) { audioOK in
                self.q.async { self.setup(audio: audioOK) }
            }
        }
    }

    private func setup(audio: Bool) {
        session.beginConfiguration()
        session.sessionPreset = .high
        if let cam = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .front),
           let input = try? AVCaptureDeviceInput(device: cam), session.canAddInput(input) {
            session.addInput(input)
            videoInput = input
        }
        if audio, let mic = AVCaptureDevice.default(for: .audio),
           let aInput = try? AVCaptureDeviceInput(device: mic), session.canAddInput(aInput) {
            session.addInput(aInput)
        }
        DispatchQueue.main.async { self.hasAudio = audio }
        // BGRA delivery: CoreImage's native layout, so CIImage(cvPixelBuffer:) is a
        // zero-copy wrap instead of a YUV conversion per frame.
        videoOutput.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String:
                                        kCVPixelFormatType_32BGRA]
        videoOutput.alwaysDiscardsLateVideoFrames = true
        videoOutput.setSampleBufferDelegate(self, queue: captureQ)
        if session.canAddOutput(videoOutput) { session.addOutput(videoOutput) }
        audioOutput.setSampleBufferDelegate(self, queue: captureQ)
        if session.canAddOutput(audioOutput) { session.addOutput(audioOutput) }
        configureVideoConnection()
        session.commitConfiguration()
        session.startRunning()
        configureVideoConnection()      // a device that ignores it before start honours it now
        setStatus(.ready)
    }

    /// Portrait buffers straight off the connection (hardware-rotated), UNmirrored —
    /// matching what MovieFileOutput wrote, so downstream (stitcher, backend, render)
    /// sees identical files. The PREVIEW mirrors front-camera frames itself, exactly
    /// like AVCaptureVideoPreviewLayer did. Re-applied after every flip: swapping the
    /// input rebuilds the connection and silently drops its settings.
    private func configureVideoConnection() {
        guard let conn = videoOutput.connection(with: .video) else { return }
        if conn.isVideoRotationAngleSupported(90) { conn.videoRotationAngle = 90 }
        if conn.isVideoMirroringSupported { conn.isVideoMirrored = false }
        #if DEBUG
        print("[camera] rotation 90 supported=\(conn.isVideoRotationAngleSupported(90)) angle=\(conn.videoRotationAngle)")
        #endif
    }

    /// Flip front/back between takes (used while paused, never mid-recording — the
    /// segments stitch together afterward). Swaps only the video input. Simulator
    /// no-op (no camera). The camera flip is why a paused multi-take can change
    /// angle and still export as one continuous clip.
    func flip() {
        guard hasCamera, status == .ready else { return }
        let target: AVCaptureDevice.Position = (position == .front) ? .back : .front
        q.async {
            guard let cam = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: target),
                  let newInput = try? AVCaptureDeviceInput(device: cam) else { return }
            self.session.beginConfiguration()
            if let old = self.videoInput { self.session.removeInput(old) }
            if self.session.canAddInput(newInput) {
                self.session.addInput(newInput)
                self.videoInput = newInput
            } else if let old = self.videoInput {
                self.session.addInput(old)   // restore on failure
            }
            self.configureVideoConnection()
            self.session.commitConfiguration()
            self.faces.reset()
            DispatchQueue.main.async { self.position = target }
        }
    }

    func start() {
        guard status == .ready else { return }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".mov")
        DispatchQueue.main.async { self.status = .recording }
        captureQ.async {
            self.writerURL = url
            self.sessionStarted = false
            self.isCapturingTake = true
            // The writer itself is built lazily on the first video frame — that's the
            // only place the (rotated) buffer dimensions are known for certain.
        }
    }

    func stop(_ done: @escaping (URL?) -> Void) {
        guard status == .recording else { done(nil); return }
        captureQ.async {
            self.isCapturingTake = false
            let url = self.writerURL
            let finished = { (ok: Bool) in
                DispatchQueue.main.async {
                    self.status = .ready
                    done(ok ? url : nil)
                }
            }
            guard let writer = self.writer, self.sessionStarted else {
                // Stopped before a single frame landed (sub-100ms tap) — nothing real
                // was recorded; surface nil exactly like the old delegate's error path.
                self.writer?.cancelWriting()
                self.resetWriterState()
                finished(false)
                return
            }
            self.writerVideo?.markAsFinished()
            self.writerAudio?.markAsFinished()
            writer.finishWriting {
                let ok = writer.status == .completed
                self.captureQ.async { self.resetWriterState() }
                finished(ok)
            }
        }
    }

    private func resetWriterState() {
        writer = nil; writerVideo = nil; writerAudio = nil
        adaptor = nil; writerURL = nil; sessionStarted = false
    }

    func teardown() {
        q.async { if self.session.isRunning { self.session.stopRunning() } }
        captureQ.async {
            if self.isCapturingTake { self.writer?.cancelWriting() }
            self.isCapturingTake = false
            self.resetWriterState()
        }
    }

    // MARK: - Writer plumbing (captureQ only)

    private func buildWriter(width: Int, height: Int) {
        guard let url = writerURL, let w = try? AVAssetWriter(url: url, fileType: .mov) else {
            isCapturingTake = false
            return
        }
        // H.264 ~12Mbps ≈ what the .high-preset MovieFileOutput produced at 1080p30 —
        // the on-device compressor (MediaCompressor) and the 150MB cap were both tuned
        // against that, so the swap must not change the size envelope.
        let video = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: width,
            AVVideoHeightKey: height,
            AVVideoCompressionPropertiesKey: [
                AVVideoAverageBitRateKey: 12_000_000,
                AVVideoExpectedSourceFrameRateKey: 30,
                AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
            ],
        ])
        video.expectsMediaDataInRealTime = true
        let audio = AVAssetWriterInput(mediaType: .audio, outputSettings: [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 44_100,
            AVNumberOfChannelsKey: 1,
            AVEncoderBitRateKey: 96_000,
        ])
        audio.expectsMediaDataInRealTime = true
        if w.canAdd(video) { w.add(video) }
        if w.canAdd(audio) { w.add(audio) }
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(
            assetWriterInput: video,
            sourcePixelBufferAttributes: [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
                kCVPixelBufferWidthKey as String: width,
                kCVPixelBufferHeightKey as String: height,
            ])
        guard w.startWriting() else { isCapturingTake = false; return }
        self.writer = w
        self.writerVideo = video
        self.writerAudio = audio
        self.adaptor = adaptor
    }

    /// `rendered` is the frame to write when it is not the raw buffer (retouched, or
    /// rotated to portrait by the sideways fallback); nil appends the buffer as-is.
    private func appendVideo(_ pixelBuffer: CVPixelBuffer, rendered: CIImage?, at pts: CMTime) {
        if writer == nil {
            buildWriter(width: rendered.map { Int($0.extent.width) } ?? CVPixelBufferGetWidth(pixelBuffer),
                        height: rendered.map { Int($0.extent.height) } ?? CVPixelBufferGetHeight(pixelBuffer))
        }
        guard let writer, let video = writerVideo, let adaptor else { return }
        if !sessionStarted {
            writer.startSession(atSourceTime: pts)
            sessionStarted = true
        }
        guard video.isReadyForMoreMediaData else { return }   // drop, never block capture
        if let rendered {
            // Render the frame into a writer-pool buffer. The pool is the adaptor's own
            // (right format, recycled) — zero allocations at steady state.
            guard let pool = adaptor.pixelBufferPool else { return }
            var out: CVPixelBuffer?
            CVPixelBufferPoolCreatePixelBuffer(nil, pool, &out)
            guard let out else { return }
            FaceRetouch.context.render(rendered, to: out)
            adaptor.append(out, withPresentationTime: pts)
        } else {
            adaptor.append(pixelBuffer, withPresentationTime: pts)
        }
    }
}

extension CameraModel: AVCaptureVideoDataOutputSampleBufferDelegate,
                        AVCaptureAudioDataOutputSampleBufferDelegate {
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        if output === audioOutput {
            // Audio only counts once the video session anchor exists — an audio buffer
            // stamped before the first video PTS would drag the session start backward.
            if isCapturingTake, sessionStarted,
               let audio = writerAudio, audio.isReadyForMoreMediaData {
                audio.append(sampleBuffer)
            }
            return
        }
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        stateLock.lock()
        let retouch = _retouchOn
        let strength = _strength
        stateLock.unlock()

        var base = CIImage(cvPixelBuffer: pixelBuffer)
        // Sideways fallback (owner, 2026-09-25: "when I record the display is sideways").
        // The connection is asked for portrait buffers above; when a device delivers them
        // landscape anyway (the rotation request not honoured), rotate here so the preview
        // AND the recorded file are portrait regardless. Portrait buffers pass through.
        let sideways = CVPixelBufferGetWidth(pixelBuffer) > CVPixelBufferGetHeight(pixelBuffer)
        if sideways {
            base = base.oriented(.right)
            base = base.transformed(by: CGAffineTransform(translationX: -base.extent.minX,
                                                          y: -base.extent.minY))
        }
        if retouch { faces.submit(base) }
        let filtered = retouch ? FaceRetouch.apply(to: base, strength: strength, faces: faces.rects) : nil

        // Preview always gets the current look — the toggle is visible instantly,
        // recording or not.
        previewTap.set(filtered ?? base)

        if isCapturingTake {
            appendVideo(pixelBuffer, rendered: filtered ?? (sideways ? base : nil),
                        at: CMSampleBufferGetPresentationTimeStamp(sampleBuffer))
        }
    }
}

// MARK: - Face tracking for the retouch mask

/// Vision face rectangles at ~5 Hz on a private queue, never blocking capture: a frame is
/// analysed only when no analysis is in flight, at quarter resolution, and the latest
/// result is read lock-free-ish by the capture thread. Rects are normalized (0…1,
/// origin bottom-left) in the frame's own coordinate space, which is what FaceRetouch
/// expects. Faces move slowly against 30 fps and the mask is feathered wide, so a
/// result up to ~200 ms old is invisible.
final class FaceTracker {
    private let queue = DispatchQueue(label: "marque.camera.faces", qos: .userInitiated)
    private let lock = NSLock()
    private var _rects: [CGRect] = []
    private var busy = false
    private var counter = 0
    private let request = VNDetectFaceRectanglesRequest()

    var rects: [CGRect] { lock.lock(); defer { lock.unlock() }; return _rects }

    func reset() { lock.lock(); _rects = []; lock.unlock() }

    /// Call once per captured frame with the (portrait, unmirrored) frame image.
    func submit(_ image: CIImage) {
        counter &+= 1
        guard counter % 6 == 0 else { return }
        lock.lock()
        let wasBusy = busy
        if !wasBusy { busy = true }
        lock.unlock()
        guard !wasBusy else { return }
        let small = image.transformed(by: CGAffineTransform(scaleX: 0.25, y: 0.25))
        queue.async { [self] in
            let handler = VNImageRequestHandler(ciImage: small, orientation: .up, options: [:])
            try? handler.perform([request])
            let found = (request.results ?? []).map { $0.boundingBox }
            lock.lock(); _rects = found; busy = false; lock.unlock()
        }
    }
}

// MARK: - Metal preview (replaces AVCaptureVideoPreviewLayer)

/// Draws the SAME frames the writer records — filtered when retouch is on — so the
/// preview is truthful by construction. Mirrors front-camera frames locally, exactly
/// like AVCaptureVideoPreviewLayer's default, while the recorded file stays unmirrored.
struct CameraPreview: UIViewRepresentable {
    @ObservedObject var camera: CameraModel

    func makeUIView(context: Context) -> PreviewMetalView {
        let v = PreviewMetalView()
        v.tap = camera.previewTap
        v.mirrored = camera.position == .front
        return v
    }

    func updateUIView(_ uiView: PreviewMetalView, context: Context) {
        uiView.mirrored = camera.position == .front
    }

    final class PreviewMetalView: MTKView {
        var tap: CameraModel.PreviewTap?
        var mirrored = true
        private var ciContext: CIContext?
        private var queue: MTLCommandQueue?

        init() {
            let dev = MTLCreateSystemDefaultDevice()
            super.init(frame: .zero, device: dev)
            framebufferOnly = false               // CIContext renders INTO the drawable
            colorPixelFormat = .bgra8Unorm
            preferredFramesPerSecond = 30
            backgroundColor = .black
            if let dev {
                ciContext = CIContext(mtlDevice: dev,
                                      options: [.cacheIntermediates: false])
                queue = dev.makeCommandQueue()
            }
        }

        required init(coder: NSCoder) { fatalError("unused") }

        override func draw(_ rect: CGRect) {
            guard let image = tap?.take(), let ciContext, let queue,
                  let drawable = currentDrawable,
                  let buffer = queue.makeCommandBuffer() else { return }
            let dest = CGSize(width: drawableSize.width, height: drawableSize.height)
            guard dest.width > 0, dest.height > 0, image.extent.width > 0 else { return }
            // Aspect-FILL into the drawable (the old preview layer's videoGravity).
            let scale = max(dest.width / image.extent.width, dest.height / image.extent.height)
            var img = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
            if mirrored {
                img = img.transformed(by: CGAffineTransform(scaleX: -1, y: 1)
                    .translatedBy(x: -img.extent.width, y: 0))
            }
            // Center the overflow so the crop matches resizeAspectFill.
            img = img.transformed(by: CGAffineTransform(
                translationX: -img.extent.minX - (img.extent.width - dest.width) / 2,
                y: -img.extent.minY - (img.extent.height - dest.height) / 2))
            ciContext.render(img, to: drawable.texture, commandBuffer: buffer,
                             bounds: CGRect(origin: .zero, size: dest),
                             colorSpace: CGColorSpaceCreateDeviceRGB())
            buffer.present(drawable)
            buffer.commit()
        }
    }
}
