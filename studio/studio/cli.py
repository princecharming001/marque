"""Command line entry point ``studio`` (ARCHITECTURE §10).

::

    studio edit <video> [--brief TEXT] [--style NAME] [--platform tiktok|reels|shorts]
                [--director-provider P --director-model M --director-key-env VAR] [--rounds N] [--out DIR]
    studio chat <job_dir> "<instruction>" [--director-…]
    studio index <video> [--asr-provider elevenlabs|assemblyai]
    studio render <job_dir> [--version N] [--preview]
    studio report <job_dir>
    studio qa <job_dir> [--render N]

Global options: ``--work-dir DIR`` (overrides ``STUDIO_WORK_DIR``), ``--json`` (machine-readable
output). Keys are never read here: ``--director-key-env`` passes the *name* of an env var.

Exit codes: 0 ok · 1 error · 2 usage · 3 stage not implemented yet.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from studio import __version__

PLATFORMS = ("tiktok", "reels", "shorts")
PROVIDERS = ("anthropic", "openai", "google", "openai_compat")

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_NOT_IMPLEMENTED = 0, 1, 2, 3


def _positive_int(v: str) -> int:
    n = int(v)
    if n < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return n


def _non_negative_int(v: str) -> int:
    n = int(v)
    if n < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return n


def _add_director_flags(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("director model (bring your own key)")
    g.add_argument("--director-provider", choices=PROVIDERS, help="Director provider (default: house Anthropic)")
    g.add_argument("--director-model", metavar="M", help="Director model id")
    g.add_argument("--director-key-env", metavar="VAR", help="NAME of the env var holding the Director key")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="studio", description="Yunicorn Studio: agentic talking-head editor")
    parser.add_argument("--version", action="version", version=f"studio {__version__}")
    parser.add_argument("--work-dir", type=Path, help="Job work directory (default: $STUDIO_WORK_DIR)")
    parser.add_argument("--json", action="store_true", help="Print results as JSON")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    p = sub.add_parser("edit", help="Edit a video end to end")
    p.add_argument("video", type=Path)
    p.add_argument("--brief", metavar="TEXT", help="Creator intent (goal, CTA, audience, vibe)")
    p.add_argument("--style", metavar="NAME", help="Style name, e.g. educational, storytime, comedy")
    p.add_argument("--platform", choices=PLATFORMS, default="tiktok")
    _add_director_flags(p)
    p.add_argument("--rounds", type=_positive_int, metavar="N", help="Champion-loop round guard")
    p.add_argument("--out", type=Path, metavar="DIR", help="Copy deliverables into DIR")

    p = sub.add_parser("chat", help="Apply an instruction to an edited job")
    p.add_argument("job_dir", type=Path)
    p.add_argument("instruction")
    _add_director_flags(p)

    p = sub.add_parser("index", help="Ingest a video and build its Take Index")
    p.add_argument("video", type=Path)
    p.add_argument("--asr-provider", choices=("elevenlabs", "assemblyai"))

    p = sub.add_parser("render", help="Render a document version")
    p.add_argument("job_dir", type=Path)
    p.add_argument("--version", type=_non_negative_int, metavar="N", dest="doc_version",
                   help="Document version (default: latest)")
    p.add_argument("--preview", action="store_true", help="Fast preview render (proxy, fast preset)")

    p = sub.add_parser("report", help="Write report.md for a job")
    p.add_argument("job_dir", type=Path)

    p = sub.add_parser("qa", help="Run invariants on a render")
    p.add_argument("job_dir", type=Path)
    p.add_argument("--render", type=_positive_int, metavar="N", help="Render number (default: latest)")
    return parser


def _jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj


def _print_result(command: str, result: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"command": command, "result": _jsonable(result)}, indent=2))
        return
    if isinstance(result, Path):
        print(result)
        return
    data = _jsonable(result)
    if not isinstance(data, dict):
        print(data)
        return
    for key, value in data.items():
        if isinstance(value, dict):
            if not value:
                continue
            print(f"{key}:")
            for k, v in value.items():
                print(f"  {k}: {v}")
        elif isinstance(value, list):
            print(f"{key}: {len(value)} item(s)")
        elif value is not None:
            print(f"{key}: {value}")


def _settings(args: argparse.Namespace) -> Any:
    from studio.config import get_settings

    s = get_settings()
    if args.work_dir is not None:
        s = s.with_overrides(work_dir=args.work_dir.expanduser())
    return s


def _dispatch(args: argparse.Namespace) -> Any:
    from studio import pipeline

    settings = _settings(args)
    cmd = args.command
    if cmd == "edit":
        return pipeline.edit(
            args.video, brief=args.brief, style=args.style, platform=args.platform,
            director_provider=args.director_provider, director_model=args.director_model,
            director_key_env=args.director_key_env, rounds=args.rounds, out=args.out, settings=settings,
        )
    if cmd == "chat":
        return pipeline.chat(
            args.job_dir, args.instruction, director_provider=args.director_provider,
            director_model=args.director_model, director_key_env=args.director_key_env, settings=settings,
        )
    if cmd == "index":
        return pipeline.index(args.video, asr_provider=args.asr_provider, settings=settings)
    if cmd == "render":
        return pipeline.render(args.job_dir, version=args.doc_version, preview=args.preview, settings=settings)
    if cmd == "report":
        return pipeline.report(args.job_dir, settings=settings)
    if cmd == "qa":
        return pipeline.qa(args.job_dir, render=args.render, settings=settings)
    raise AssertionError(f"unhandled command {cmd}")  # pragma: no cover - argparse restricts choices


def _check_inputs(args: argparse.Namespace) -> str | None:
    if getattr(args, "video", None) is not None and not args.video.exists():
        return f"video not found: {args.video}"
    if getattr(args, "job_dir", None) is not None and not args.job_dir.is_dir():
        return f"job directory not found: {args.job_dir}"
    if getattr(args, "director_model", None) and not getattr(args, "director_provider", None):
        return "--director-model needs --director-provider"
    if getattr(args, "director_key_env", None) and not getattr(args, "director_provider", None):
        return "--director-key-env needs --director-provider"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:  # --help/--version exit 0; usage errors exit 2
        return int(e.code) if isinstance(e.code, int) else EXIT_USAGE
    problem = _check_inputs(args)
    if problem:
        print(f"studio {args.command}: {problem}", file=sys.stderr)
        return EXIT_USAGE
    try:
        result = _dispatch(args)
    except NotImplementedError as e:
        print(f"studio {args.command}: not implemented yet ({e})", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED
    except KeyboardInterrupt:  # pragma: no cover
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as e:
        from studio.config import redact

        print(f"studio {args.command}: error: {redact(f'{type(e).__name__}: {e}')}", file=sys.stderr)
        return EXIT_ERROR
    _print_result(args.command, result, args.json)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
