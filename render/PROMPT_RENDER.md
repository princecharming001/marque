# Ralph loop: render fidelity (Loop G)

Each iteration:
1. Read `/Users/home/Marque/render/BACKLOG_RENDER.md`. Pick the FIRST unchecked item.
2. Write the failing repro/pin FIRST (a TS unit-testable assertion where feasible,
   or a Python test in backend/test_editor_hardening.py / test_main.py against
   `build_render_plan`'s output contract, since that's what the compositions
   actually consume). If it doesn't fail on current code, the audit claim was a
   false positive — check the item off as "no-repro (regression test kept)".
3. Key files: render/src/components/*.tsx, render/src/compositions/*.tsx,
   render/src/types.ts, render/src/lambda-render.ts, backend/app/edl.py
   (build_render_plan — the CONTRACT the bridge consumes), backend/main.py
   (_submit_remotion_render / _run_render_bridge / render env knobs).
4. GATE (must be green before checking anything off):
   `cd /Users/home/Marque/backend && python -m pytest -q` (keyless) AND
   `cd /Users/home/Marque/render && npx tsc -p tsconfig.bridge.json --noEmit`
   AND `npx tsc -p tsconfig.json --noEmit` (full project type-check).
5. Check the item off in BACKLOG_RENDER.md with a one-line note of what landed.
6. Commit locally with a focused message + the repo's Co-Authored-By trailer.

Hard rules:
- Never weaken or delete an existing test.
- The render plan contract (build_render_plan's output shape) is the single
  source of truth the compositions consume — verify field names/units match
  EXACTLY between backend/app/edl.py and render/src/types.ts on every change.
- Keyless-green mandatory for the Python side.
- No new npm/pip dependencies unless the item explicitly requires one (font
  embedding may need one — justify it in the commit message if so).
- Do NOT touch ios/Marque/Features/Onboarding/** (owned by a parallel session).
- Do NOT deploy the Remotion Lambda site or redeploy the backend. Local commits
  only — the end-of-loop site/backend redeploy is a standing checkpoint;
  implement + commit, then STOP and surface it instead of running the deploy.

When EVERY box in BACKLOG_RENDER.md is checked AND both gates are green, output
the completion promise exactly: RENDER FIDELITY GREEN
