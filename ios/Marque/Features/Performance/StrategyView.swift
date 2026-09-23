import SwiftUI

// P7.4 "Your Strategy" — the compiled brain, visible. The doc arrives as markdown with a
// fixed section contract (Insights / Plan / Buckets / Brand Bets / Not-Doing, enforced by
// the backend's validate_sections), and this screen renders it as a designed hierarchy
// instead of prose cards: the Plan's REGIME + LEVER as a hero verdict up top (the whole
// screen answers "what's my one move?" in seconds), insights as claim cards with a
// confidence badge and the evidence collapsed behind a tap, buckets as rows with job
// chips, and Brand Bets / Not-Doing as a paired do/don't scan-list. Anything that doesn't
// parse degrades to the old prose card, never a blank.
struct StrategyView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var doc: BackendClient.StrategyDoc?
    @State private var loading = true
    @State private var expandedInsights: Set<Int> = []

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    VStack(alignment: .leading, spacing: Space.xs) {
                        if let doc, !doc.isTemplate, !loading {
                            DSEyebrow(text: "Revision \(doc.revision)")
                        }
                        DSPageTitle(title: "your strategy.")
                    }
                    if loading {
                        ProgressView().tint(Palette.textSecondary)
                            .frame(maxWidth: .infinity).padding(.top, 48)
                    } else if let doc, !doc.isTemplate {
                        let model = StrategyModel.parse(doc.markdown)
                        if let plan = model.plan { heroCard(plan) }
                        if !model.insights.isEmpty {
                            section("What's working") {
                                VStack(spacing: Space.stack) {
                                    ForEach(Array(model.insights.enumerated()), id: \.offset) { i, insight in
                                        insightCard(insight, index: i)
                                    }
                                }
                            }
                        }
                        if !model.buckets.isEmpty {
                            section("Make these") {
                                listCard {
                                    ForEach(Array(model.buckets.enumerated()), id: \.offset) { i, b in
                                        bucketRow(b)
                                        if i < model.buckets.count - 1 { rowDivider }
                                    }
                                }
                            }
                        }
                        if !model.bets.isEmpty || !model.notDoing.isEmpty {
                            section("Do / don't") {
                                listCard {
                                    ForEach(Array(model.bets.enumerated()), id: \.offset) { i, line in
                                        doDontRow(line, isDo: true)
                                        if i < model.bets.count - 1 || !model.notDoing.isEmpty { rowDivider }
                                    }
                                    ForEach(Array(model.notDoing.enumerated()), id: \.offset) { i, line in
                                        doDontRow(line, isDo: false)
                                        if i < model.notDoing.count - 1 { rowDivider }
                                    }
                                }
                            }
                        }
                        // Sections outside the known contract (or bodies that produced no
                        // bullets) still show — as the old prose card, at the end.
                        ForEach(Array(model.unparsed.enumerated()), id: \.offset) { _, s in
                            proseCard(title: s.title, body: s.body)
                        }
                        if !doc.updates.isEmpty {
                            section("What changed recently") {
                                listCard {
                                    ForEach(Array(doc.updates.prefix(6).enumerated()), id: \.offset) { i, u in
                                        HStack(alignment: .top, spacing: Space.md) {
                                            Image(systemName: "arrow.triangle.2.circlepath")
                                                .font(.system(size: 14, weight: .regular))
                                                .foregroundStyle(Palette.textPrimary)
                                                .frame(width: 20)
                                                .padding(.top, 2)
                                            Text(u).font(AppFont.supporting)
                                                .foregroundStyle(Palette.textSecondary)
                                                .fixedSize(horizontal: false, vertical: true)
                                        }
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                        if i < min(doc.updates.count, 6) - 1 { rowDivider }
                                    }
                                }
                            }
                        }
                    } else {
                        EmptyStateView(icon: "brain",
                                       title: "Not ready yet",
                                       message: "Film and analyze a few clips, your strategy builds from them.")
                            .padding(.top, 32)
                    }
                }
                .screenPadding().padding(.top, Space.sm).padding(.bottom, Space.xxl)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }.fontWeight(.semibold)
                }
            }
            .tint(Palette.ink)
        }
        .task {
            #if DEBUG
            if CommandLine.arguments.contains("-demoStrategy") {
                doc = .init(markdown: StrategyModel.demoMarkdown, revision: 7,
                            updatedAt: "", updates: [
                                "Promoted \"myth vs. reality\" from experiment to core after two above-median runs.",
                                "New insight: your direct-to-camera confession openers hold attention past 3s.",
                            ], isTemplate: false)
                loading = false
                return
            }
            #endif
            doc = await store.backend.fetchStrategy()
            loading = false
        }
    }

    // MARK: Hero — the Plan as a verdict (the one night card on the page)

    private func heroCard(_ plan: StrategyModel.Plan) -> some View {
        DSHeroCard(radius: Radius.card, padding: Space.cardPad) {
            VStack(alignment: .leading, spacing: Space.md) {
                regimeLadder(current: plan.regimeIndex)
                if !plan.regimeNote.isEmpty {
                    Text(plan.regimeNote).font(AppFont.supporting)
                        .foregroundStyle(Palette.onNight.opacity(0.8))
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !plan.lever.isEmpty {
                    VStack(alignment: .leading, spacing: Space.xs) {
                        DSEyebrow(text: "Your one move", color: Palette.onNightSecondary)
                        Text(plan.lever)
                            .font(AppFont.title2).tracking(-0.2)
                            .foregroundStyle(Palette.onNight)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                if !plan.priority.isEmpty {
                    Text(plan.priority).font(AppFont.supporting)
                        .foregroundStyle(Palette.onNight.opacity(0.8))
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .accessibilityIdentifier("strategy.hero")
    }

    /// Where the channel sits on the growth ladder — the regime as a 3-step track, not a
    /// word buried in prose. Active step inverts (white pill, dark label).
    private func regimeLadder(current: Int?) -> some View {
        HStack(spacing: 6) {
            ForEach(Array(StrategyModel.regimes.enumerated()), id: \.offset) { i, name in
                let active = i == current
                Text(name)
                    .font(AppFont.caption.weight(active ? .semibold : .regular))
                    .foregroundStyle(active ? Palette.night : Palette.onNightSecondary)
                    .lineLimit(1).minimumScaleFactor(0.75)
                    .padding(.horizontal, 10).frame(height: 28)
                    .background(Capsule().fill(active ? Palette.onNight : Color.white.opacity(0.10)))
                    .accessibilityAddTraits(active ? .isSelected : [])
                if i < StrategyModel.regimes.count - 1 {
                    Rectangle().fill(Color.white.opacity(0.25))
                        .frame(width: 8, height: 1)
                }
            }
            Spacer(minLength: 0)
        }
    }

    // MARK: Insights — one claim per card, evidence behind a tap

    private func insightCard(_ insight: StrategyModel.Insight, index: Int) -> some View {
        let expanded = expandedInsights.contains(index)
        return VStack(alignment: .leading, spacing: Space.md) {
            HStack(alignment: .top, spacing: Space.sm) {
                Text(insight.claim)
                    .font(AppFont.headline)
                    .foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            HStack(spacing: Space.sm) {
                if let conf = insight.confidence { badge(conf.label, tint: conf.tint) }
                Spacer()
                if !insight.evidence.isEmpty {
                    Button {
                        withAnimation(Motion.quick) {
                            if expanded { expandedInsights.remove(index) }
                            else { expandedInsights.insert(index) }
                        }
                    } label: {
                        HStack(spacing: 4) {
                            Text("Why").font(AppFont.supporting.weight(.semibold))
                            Image(systemName: "chevron.down")
                                .font(.system(size: 11, weight: .semibold))
                                .rotationEffect(.degrees(expanded ? 180 : 0))
                        }
                        .foregroundStyle(Palette.textPrimary)
                        .padding(.horizontal, 12).frame(height: 32)
                        .background(Capsule().fill(Palette.surfaceSunken))
                        .contentShape(Capsule())
                    }
                    .buttonStyle(PressableStyle(dim: 0.7))
                }
            }
            if expanded, !insight.evidence.isEmpty {
                Text(insight.evidence).font(AppFont.supporting)
                    .foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCard(.surface, radius: Radius.group, padding: Space.rowPad)
    }

    // MARK: Buckets — the format, its job, its provenness

    private func bucketRow(_ b: StrategyModel.Bucket) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: Space.sm) {
                Text(b.name)
                    .font(AppFont.headline)
                    .foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                if let job = b.job { badge(job.uppercased(), tint: job == "experiment" ? Palette.textSecondary : Palette.textPrimary) }
            }
            if !b.detail.isEmpty {
                Text(b.detail).font(AppFont.supporting)
                    .foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Do / don't — scan-lists, not cards (glyph shape carries do vs don't)

    private func doDontRow(_ line: StrategyModel.Line, isDo: Bool) -> some View {
        HStack(alignment: .top, spacing: Space.md) {
            Image(systemName: isDo ? "plus.circle" : "xmark.circle")
                .font(.system(size: 17, weight: .regular))
                .foregroundStyle(Palette.textPrimary)
                .frame(width: 20)
                .padding(.top, 1)
                .accessibilityLabel(isDo ? "Do" : "Don't")
            VStack(alignment: .leading, spacing: 2) {
                Text(line.lead)
                    .font(AppFont.bodyText.weight(.medium))
                    .foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                if !line.rest.isEmpty {
                    Text(line.rest).font(AppFont.supporting)
                        .foregroundStyle(Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Shared bits

    /// Monochrome provenance badge: outline capsule, glyph + tracked label.
    private func badge(_ text: String, tint: Color) -> some View {
        HStack(spacing: 4) {
            if let glyph = badgeGlyph(text) {
                Image(systemName: glyph).font(.system(size: 10, weight: .semibold))
            }
            Text(text).font(AppFont.eyebrow).tracking(1)
                .lineLimit(1).minimumScaleFactor(0.8)
        }
        .foregroundStyle(tint)
        .padding(.horizontal, 10).frame(height: 24)
        .background(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
    }

    private func badgeGlyph(_ label: String) -> String? {
        switch label {
        case "PROVEN IN NICHE": return "checkmark.seal"
        case "YOUR DATA": return "chart.bar"
        case "UNTESTED": return "questionmark.circle"
        case "EXPERIMENT": return "testtube.2"
        case "HEADLINE": return "star"
        case "CORE": return "circle.fill"
        default: return nil
        }
    }

    /// Eyebrow-headed section (DESIGN.md: eyebrow 8pt above its card, aligned with row text).
    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            DSEyebrow(text: title).padding(.horizontal, Space.rowPad)
            content()
        }
    }

    private func listCard<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: Space.md) { content() }
            .frame(maxWidth: .infinity, alignment: .leading)
            .dsCard(.surface, radius: Radius.group, padding: Space.rowPad)
    }

    private var rowDivider: some View {
        Rectangle().fill(Palette.hairline).frame(height: 1)
    }

    private func proseCard(title: String, body text: String) -> some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            if !title.isEmpty {
                Text(title).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
            }
            Text(text).font(AppFont.bodyText)
                .foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCard(.surface, radius: Radius.group, padding: Space.rowPad)
    }
}

// MARK: - Parser

/// Typed view of the strategy doc. Parsing is deliberately forgiving: every accessor has a
/// prose fallback, so a doc that drifts from the contract renders as readable cards, never
/// a blank screen. The section names + REGIME:/LEVER: lines + confidence vocabulary all
/// come from the backend's synthesis prompt, which validate_sections enforces server-side.
struct StrategyModel {
    static let regimes = ["sub-breakout", "breakout", "scaling"]

    struct Plan {
        var regimeIndex: Int?      // position on the ladder; nil renders no highlight
        var regimeNote: String     // the one-line consequence after the regime word
        var lever: String
        var priority: String
    }
    struct Confidence { let label: String; let tint: Color }
    struct Insight { let claim: String; let evidence: String; let confidence: Confidence? }
    struct Bucket { let name: String; let job: String?; let detail: String }
    /// A bullet split at its first sentence: bold lead, secondary rest.
    struct Line { let lead: String; let rest: String }
    struct Prose { let title: String; let body: String }

    var plan: Plan?
    var insights: [Insight] = []
    var buckets: [Bucket] = []
    var bets: [Line] = []
    var notDoing: [Line] = []
    var unparsed: [Prose] = []

    static func parse(_ md: String) -> StrategyModel {
        var model = StrategyModel()
        for (title, body) in sections(md) {
            switch title.lowercased() {
            case "plan": model.plan = parsePlan(body)
            case "insights":
                model.insights = bullets(body).map(parseInsight)
                if model.insights.isEmpty { model.unparsed.append(Prose(title: title, body: body)) }
            case "buckets":
                model.buckets = bullets(body).map(parseBucket)
                if model.buckets.isEmpty { model.unparsed.append(Prose(title: title, body: body)) }
            case "brand bets":
                model.bets = bullets(body).map(splitLead)
                if model.bets.isEmpty { model.unparsed.append(Prose(title: title, body: body)) }
            case "not-doing", "not doing":
                model.notDoing = bullets(body).map(splitLead)
                if model.notDoing.isEmpty { model.unparsed.append(Prose(title: title, body: body)) }
            default:
                model.unparsed.append(Prose(title: title, body: body))
            }
        }
        if model.plan == nil, model.insights.isEmpty, model.buckets.isEmpty,
           model.bets.isEmpty, model.notDoing.isEmpty, model.unparsed.isEmpty, !md.isEmpty {
            model.unparsed = [Prose(title: "", body: md)]   // headerless doc: whole thing as prose
        }
        return model
    }

    /// "## Title\nbody…" blocks in order; anything before the first header is an untitled block.
    private static func sections(_ md: String) -> [(String, String)] {
        var out: [(String, String)] = []
        var title = ""
        var buf: [String] = []
        func flush() {
            let body = buf.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !body.isEmpty || !title.isEmpty { out.append((title, body)) }
            buf = []
        }
        for line in md.components(separatedBy: "\n") {
            if line.hasPrefix("## ") {
                flush()
                title = String(line.dropFirst(3)).trimmingCharacters(in: .whitespaces)
            } else {
                buf.append(line)
            }
        }
        flush()
        return out
    }

    private static func bullets(_ body: String) -> [String] {
        body.components(separatedBy: "\n")
            .filter { $0.trimmingCharacters(in: .whitespaces).hasPrefix("- ") }
            .map { String($0.trimmingCharacters(in: .whitespaces).dropFirst(2))
                .trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }

    private static func parsePlan(_ body: String) -> Plan {
        var plan = Plan(regimeIndex: nil, regimeNote: "", lever: "", priority: "")
        var rest: [String] = []
        for raw in body.components(separatedBy: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.uppercased().hasPrefix("REGIME:") {
                let value = String(line.dropFirst("REGIME:".count)).trimmingCharacters(in: .whitespaces)
                let lower = value.lowercased()
                // Longest keyword first: "sub-breakout" contains "breakout".
                for (i, name) in regimes.enumerated().sorted(by: { $0.1.count > $1.1.count }) {
                    if let r = lower.range(of: name) {
                        plan.regimeIndex = i
                        plan.regimeNote = String(value[value.index(value.startIndex, offsetBy: lower.distance(from: lower.startIndex, to: r.upperBound))...])
                            .trimmingCharacters(in: CharacterSet(charactersIn: " -–, :().")).trimmingCharacters(in: .whitespaces)
                        break
                    }
                }
                if plan.regimeIndex == nil { plan.regimeNote = value }
            } else if line.uppercased().hasPrefix("LEVER:") {
                plan.lever = String(line.dropFirst("LEVER:".count)).trimmingCharacters(in: .whitespaces)
            } else if !line.isEmpty {
                rest.append(line.hasPrefix("Priority:")
                            ? String(line.dropFirst("Priority:".count)).trimmingCharacters(in: .whitespaces)
                            : line)
            }
        }
        plan.priority = rest.joined(separator: " ")
        return plan
    }

    /// The synthesis prompt has each insight name its confidence source (niche-proven /
    /// own-data / untested). Detect it, badge it, and strip the bare parenthetical so the
    /// claim reads clean.
    private static func parseInsight(_ text: String) -> Insight {
        let lower = text.lowercased()
        var confidence: Confidence?
        if lower.contains("niche-proven") || lower.contains("niche proven") {
            confidence = Confidence(label: "PROVEN IN NICHE", tint: Palette.textPrimary)
        } else if lower.contains("own-data") || lower.contains("own data") || lower.contains("own-proven") || lower.contains("your data") {
            confidence = Confidence(label: "YOUR DATA", tint: Palette.textSecondary)
        } else if lower.contains("untested") {
            confidence = Confidence(label: "UNTESTED", tint: Palette.textSecondary)
        }
        var cleaned = text
        for tag in ["(niche-proven)", "(niche proven)", "(own-data)", "(own data)", "(own-proven)", "(untested)",
                    "(niche-proven)."] {
            cleaned = cleaned.replacingOccurrences(of: tag, with: "", options: .caseInsensitive)
        }
        cleaned = cleaned.replacingOccurrences(of: "  ", with: " ")
            .replacingOccurrences(of: " .", with: ".")   // tag stripped mid-sentence leaves " ."
            .trimmingCharacters(in: .whitespaces)
        let split = splitLead(cleaned)
        return Insight(claim: split.lead, evidence: split.rest, confidence: confidence)
    }

    /// Buckets read like "name (core): how proven…" or free prose naming its job. The job
    /// keyword becomes a chip; a leading "name:" split gives the row its title.
    private static func parseBucket(_ text: String) -> Bucket {
        var job: String?
        for candidate in ["experiment", "headline", "core"] {
            if text.range(of: "\\b\(candidate)\\b", options: [.regularExpression, .caseInsensitive]) != nil {
                job = candidate
                break
            }
        }
        var cleaned = text
        for candidate in ["experiment", "headline", "core"] {
            for wrapped in ["(\(candidate))", "(job: \(candidate))"] {
                cleaned = cleaned.replacingOccurrences(of: wrapped, with: "", options: .caseInsensitive)
            }
        }
        cleaned = cleaned.replacingOccurrences(of: "  ", with: " ").trimmingCharacters(in: .whitespaces)
        // "Name: detail" or "Name. Detail" → title + secondary; otherwise the sentence split.
        if let colon = cleaned.firstIndex(of: ":"), cleaned.distance(from: cleaned.startIndex, to: colon) <= 48 {
            let name = String(cleaned[..<colon]).trimmingCharacters(in: .whitespaces)
            let detail = String(cleaned[cleaned.index(after: colon)...]).trimmingCharacters(in: .whitespaces)
            return Bucket(name: name, job: job, detail: detail)
        }
        let split = splitLead(cleaned)
        return Bucket(name: split.lead, job: job, detail: split.rest)
    }

    /// First sentence = the bold lead; the remainder = secondary text.
    private static func splitLead(_ text: String) -> Line {
        guard let r = text.range(of: ". ") else {
            return Line(lead: text, rest: "")
        }
        let lead = String(text[..<r.lowerBound]) + "."
        let rest = String(text[r.upperBound...]).trimmingCharacters(in: .whitespaces)
        return Line(lead: lead, rest: rest)
    }

    #if DEBUG
    /// A realistic compiled doc (mirrors the synthesis prompt's shapes) for the
    /// -demoStrategy sim seam — lets the screen be screenshot-verified without a backend.
    static let demoMarkdown = """
    ## Insights
    - Your direct-to-camera confession openers hold attention past the 3-second mark. Both of your above-median videos open on a personal admission before any context, while every video that opens with setup sits at your floor (own-data).
    - Specific numbers in the first line outperform vague claims in your niche. "I spent $400 on retinol" style openers are niche-proven; your own tests are too few to settle it (niche-proven).
    - Splitting one routine into a 3-part series is untested on you but is the dominant format among breakout accounts your size (untested).

    ## Plan
    REGIME: sub-breakout. Your catalog sits below the niche ceiling, so proof comes from the niche, not your own averages.
    LEVER: open every video on the admission or the number, never the setup
    Priority: this month, re-shoot your three strongest topics with confession-first openers and post consistently.

    ## Buckets
    - Confession openers (headline): your proven attention-holder; lead the week with one.
    - Myth vs. reality (core): steady performer across your catalog; keep two per week.
    - 3-part routine series (experiment): niche-proven, unfired on your account; run one series and read the data.

    ## Brand Bets
    - The on-screen receipt: show the actual product or bill you are talking about. It is becoming your signature and no one else in your lane does it.

    ## Not-Doing
    - Trend-audio reaction cuts. Your one spike with trend audio was carried by the sound, not the structure; the views did not transfer to the next video.
    - Setup-first intros. Every floor video opens with context before the point.
    """
    #endif
}
