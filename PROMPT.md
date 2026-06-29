# Marque autonomous buildout — loop instructions

You are building out the Marque iOS app at `/Users/home/Marque`. Goal: complete the remaining
functionality so that pasting an API key is the only thing left to make each service live —
exactly as already proven for Claude (the `AnthropicLLMRouter` goes live when a key is present).

## Context to read first (every iteration, quickly)
- `PROGRESS.md` — what already runs.
- `BACKLOG.md` — the ordered task list. THIS is your worklist.
- `DECISIONS.md` / `docs/` — the spec; honor canonical names, the Stoic aesthetic, the
  anti-clutter doctrine (Today shows one directive + streak + one trend line).

## Each iteration — do exactly ONE thing
1. Open `BACKLOG.md`, pick the FIRST unchecked `[ ]` item.
2. Implement just that item — smallest correct version. Match the existing code style, the
   design tokens in `DesignSystem/`, and the adapter pattern (vendor behind a protocol; mock
   default; live impl gated on a key from `AppConfig`).
3. **Gate — must pass before committing:**
   - `cd /Users/home/Marque && ./scripts/dev.sh build` must end in BUILD SUCCEEDED.
   - If the item changes UI/flow, run `./scripts/dev.sh test` and keep the Maestro flow GREEN.
     Fix anything you broke before moving on. Add new Maestro steps to `.maestro/flow-extras.yaml`
     for new screens.
4. Check the item off in `BACKLOG.md` (`[x]`) with a one-line note.
5. Commit: `git add -A && git -c user.name="Marque Dev" -c user.email="dev@marque.app" commit`
   with a clear message ending in the Co-Authored-By line used in prior commits.

## Hard rules
- **No new SPM/CocoaPods dependencies.** Use only URLSession, StoreKit 2, AVFoundation, PhotosUI,
  SwiftUI/Foundation. (Keeps builds reliable in this environment.)
- **Never commit secrets.** `.env` stays gitignored; keys come from `AppConfig` (env/UserDefaults).
- **Keep the build and the Maestro flow green at all times.** A red build is the only priority
  until it's green again.
- One item per iteration. Don't batch. Don't refactor unrelated code.
- Integration code that needs an absent key is expected to be untestable — make it compile,
  key-gate it, fall back to the mock, and say so in the commit message.

## Done
When every box in `BACKLOG.md` is checked, output exactly:
`<promise>MARQUE BUILDOUT COMPLETE</promise>`
