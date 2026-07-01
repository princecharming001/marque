import SwiftUI

struct MetricsEntrySheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let post: ScheduledPost
    @State private var views: String = ""
    @State private var likes: String = ""
    @State private var comments: String = ""
    @State private var shares: String = ""
    @State private var saves: String = ""
    @State private var reach: String = ""
    @State private var avgWatchPct: String = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("How did it do?") {
                    TextField("Views", text: $views).keyboardType(.numberPad)
                    TextField("Likes", text: $likes).keyboardType(.numberPad)
                    TextField("Comments", text: $comments).keyboardType(.numberPad)
                    TextField("Shares", text: $shares).keyboardType(.numberPad)
                    TextField("Saves", text: $saves).keyboardType(.numberPad)
                    TextField("Reach (unique viewers)", text: $reach).keyboardType(.numberPad)
                    TextField("Avg watch % (0-100)", text: $avgWatchPct).keyboardType(.decimalPad)
                }
            }
            .navigationTitle("Log metrics")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Save") {
                        let metrics = PostMetrics(
                            views: Int(views) ?? 0,
                            likes: Int(likes) ?? 0,
                            comments: Int(comments) ?? 0,
                            shares: Int(shares) ?? 0,
                            saves: Int(saves) ?? 0,
                            reach: Int(reach) ?? 0,
                            avgWatchPct: (Double(avgWatchPct) ?? 0) / 100.0,
                            settled: false
                        )
                        store.logMetrics(metrics, for: post)
                        dismiss()
                    }
                }
            }
        }
    }
}
