import Foundation

// Trends from the backend (which scrapes real TikTok/Instagram trend data into a niche-keyed
// cache). Conforms to the existing InsightsProviding so Coach/Today are unchanged. Mock fallback.
struct LiveInsights: InsightsProviding {
    private let fallback = MockInsights()
    private struct Resp: Decodable { let trends: [TrendWire] }   // scripts v2: + promise / asOf

    func trends(niche: String) async -> [TrendItem] {
        let q = niche.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? ""
        guard let url = URL(string: AppConfig.backendBaseURL + "/v1/trends?niche=" + q),
              let (data, resp) = try? await URLSession.shared.data(from: url),
              let http = resp as? HTTPURLResponse, http.statusCode == 200,
              let r = try? JSONDecoder().decode(Resp.self, from: data), !r.trends.isEmpty else {
            return await fallback.trends(niche: niche)
        }
        return r.trends.map(\.item)
    }
}
