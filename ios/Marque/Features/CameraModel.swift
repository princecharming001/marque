import SwiftUI
import AVFoundation

// Real AVFoundation capture. On a physical device this records actual video.
// The iOS Simulator has no camera, so `hasCamera` is false there and RecordView
// falls back to a simulated capture so the flow stays testable. (05-screens-produce.md)

final class CameraModel: NSObject, ObservableObject {
    enum Status { case idle, ready, recording, unavailable }
    @Published var status: Status = .idle
    @Published var hasAudio = false     // false when mic permission was denied → warn the user

    let session = AVCaptureSession()
    private let output = AVCaptureMovieFileOutput()
    private let q = DispatchQueue(label: "marque.camera.session")
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
