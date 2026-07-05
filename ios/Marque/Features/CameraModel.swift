import SwiftUI
import AVFoundation

// Real AVFoundation capture. On a physical device this records actual video.
// The iOS Simulator has no camera, so `hasCamera` is false there and RecordView
// falls back to a simulated capture so the flow stays testable. (05-screens-produce.md)

final class CameraModel: NSObject, ObservableObject {
    enum Status { case idle, ready, recording, unavailable }
    @Published var status: Status = .idle
    @Published var hasAudio = false     // false when mic permission was denied → warn the user

    @Published var position: AVCaptureDevice.Position = .front

    let session = AVCaptureSession()
    private let output = AVCaptureMovieFileOutput()
    private let q = DispatchQueue(label: "marque.camera.session")
    private var onFinish: ((URL?) -> Void)?
    private var videoInput: AVCaptureDeviceInput?

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
        if session.canAddOutput(output) { session.addOutput(output) }
        session.commitConfiguration()
        session.startRunning()
        setStatus(.ready)
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
            self.session.commitConfiguration()
            DispatchQueue.main.async { self.position = target }
        }
    }

    func start() {
        guard status == .ready else { return }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".mov")
        DispatchQueue.main.async { self.status = .recording }
        q.async { self.output.startRecording(to: url, recordingDelegate: self) }
    }

    func stop(_ done: @escaping (URL?) -> Void) {
        guard status == .recording else { done(nil); return }
        onFinish = done
        q.async { self.output.stopRecording() }
    }

    func teardown() {
        q.async { if self.session.isRunning { self.session.stopRunning() } }
    }
}

extension CameraModel: AVCaptureFileOutputRecordingDelegate {
    func fileOutput(_ output: AVCaptureFileOutput, didFinishRecordingTo url: URL,
                    from connections: [AVCaptureConnection], error: Error?) {
        DispatchQueue.main.async {
            self.status = .ready
            self.onFinish?(error == nil ? url : nil)
            self.onFinish = nil
        }
    }
}

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession
    func makeUIView(context: Context) -> PreviewView {
        let v = PreviewView()
        v.videoPreviewLayer.session = session
        v.videoPreviewLayer.videoGravity = .resizeAspectFill
        return v
    }
    func updateUIView(_ uiView: PreviewView, context: Context) {}
    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var videoPreviewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }
}
