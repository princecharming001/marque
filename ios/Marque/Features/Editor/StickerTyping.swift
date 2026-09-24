import Foundation

// MARK: - StickerTyping — what Return and commit mean for the on-canvas text field (ED-20).
//
// Foundation-only (LogicTests). The sticker field is a vertical-axis TextField so long text
// wraps, and on those iOS inserts "\n" for Return instead of firing .onSubmit: the
// keyboard's ✓ ("done") left the keyboard up and put a line break into the sticker, and
// commitTyping trimmed only spaces, so the break was saved and rendered as a blank line.

enum StickerTyping {
    /// Return was pressed when the draft ends with a newline: the text to commit, else nil.
    static func committedOnReturn(_ draft: String) -> String? {
        guard draft.hasSuffix("\n") else { return nil }
        return cleaned(draft)
    }

    /// What a sticker stores: no leading or trailing spaces or line breaks.
    static func cleaned(_ text: String) -> String {
        text.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
