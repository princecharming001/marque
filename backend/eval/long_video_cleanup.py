"""Emit cleanup SQL + Storage delete commands for everything the editor-audit prod runner created.

Reads eval/out/long_video/ledger.jsonl (written by eval/long_video_prod.py). Prints SQL for the
owner to run (this script never connects to anything and never deletes anything itself).

Usage (from backend/): python -m eval.long_video_cleanup > cleanup.sql
"""
import json
import os
import sys

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "long_video", "ledger.jsonl")


def main() -> int:
    jobs, creators, keys = [], set(), []
    for line in open(LEDGER):
        e = json.loads(line)
        if e.get("kind") == "clip_job" and e.get("job_id"):
            jobs.append(e["job_id"])
            creators.add(e.get("creator_id") or "")
        elif e.get("kind") == "storage_object":
            keys.append(e["key"])
    creators.discard("")
    q = lambda xs: ", ".join("'" + x.replace("'", "''") + "'" for x in sorted(set(xs)))
    out = ["-- Editor audit 2026-09-24: rows created by eval/long_video_prod.py (all ids prefixed qa-editor-).",
           "begin;"]
    if jobs:
        out.append(f"delete from clip_edit_sessions where job_id in ({q(jobs)});")
    out += [
        "delete from clip_edit_sessions where state->>'creator_id' like 'qa-editor-%';",
        "delete from ai_usage          where creator_id like 'qa-editor-%';",
        "delete from client_events     where creator_id like 'qa-editor-%';",
        "delete from post_registry     where creator_id like 'qa-editor-%';",
        "delete from arm_stats         where creator_id like 'qa-editor-%';",
        "delete from creators          where creator_id like 'qa-editor-%';",
        "delete from creator_profiles  where creator_id like 'qa-editor-%';",
        "commit;",
        "",
        "-- Storage objects: delete through the Storage API (a SQL delete on storage.objects orphans the file).",
        "-- Every key the runner minted (PUTs that 413'd never created an object; deleting a missing key is a no-op):",
    ]
    for k in sorted(set(keys)):
        out.append(f"-- curl -s -X DELETE \"$SUPABASE_URL/storage/v1/object/marque-clips/{k}\" "
                   "-H \"Authorization: Bearer $SUPABASE_SERVICE_KEY\"")
    out += ["", "-- Or in one statement from the SQL editor (Supabase's storage.delete_object is not public;",
            "-- use the dashboard Storage browser filter 'qa-editor-' if you prefer the UI).",
            f"-- {len(set(keys))} keys, {len(set(jobs))} jobs, creators: {', '.join(sorted(creators))}"]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
