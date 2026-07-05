# Ralph loop: editor correctness fixes (Loop F)

Each iteration:
1. Read `/Users/home/Marque/backend/BACKLOG_FIX.md`. Pick the FIRST unchecked item.
2. Write the failing repro test FIRST. If it doesn't fail on current code, the audit
   claim was a false positive — check the item off as "no-repro (regression test
   kept)" and move on. If it fails, implement the smallest correct fix.
3. Key files: backend/main.py (pipeline/tweak/retry), backend/app/edl.py (EDL model,
   apply_edl_ops, build_render_plan), backend/test_editor_hardening.py (the gate).
4. GATE (must be green before checking anything off):
   `cd /Users/home/Marque/backend && python -m pytest -q` with ZERO env keys.
5. Check the item off in BACKLOG_FIX.md with a one-line note of what landed (or
   "no-repro").
6. Commit locally with a focused message + the repo's Co-Authored-By trailer.

Hard rules:
- Never weaken or delete an existing test. A red suite is the only priority until green.
- Error codes come only from main.ERROR_CODES.
- EDL segments stay monotonic — reordering is expressed ONLY via segment_order.
- Keyless-green mandatory: every new code path needs a deterministic mock/fallback.
- No new pip dependencies.
- Do NOT touch ios/Marque/Features/Onboarding/** or onboarding asset catalogs
  (owned by a parallel session).
- Do NOT deploy or push. Local commits only. F0's deploy step is a standing
  checkpoint — implement + commit it, then STOP and surface it in your response
  instead of running the deploy.

When EVERY box in BACKLOG_FIX.md is checked AND the full pytest suite is green,
output the completion promise exactly: EDITOR CORRECTNESS GREEN
