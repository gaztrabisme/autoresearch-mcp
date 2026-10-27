"""Foreground commands suitable for `caffeinate -i -s autoresearch run ...`."""

import argparse
import json
import sys
import tomllib
from pathlib import Path

from . import loop, loop_core
from .service import RunManager, stop
from .store import Config, Session


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="autoresearch")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser(
        "init", help="Create a session from pre-registered TOML configuration"
    )
    init.add_argument("name")
    init.add_argument("--config", type=Path, required=True)
    for name in ("baseline", "iterate", "status", "stop", "finalize"):
        command = commands.add_parser(name)
        command.add_argument("name")
    run = commands.add_parser(
        "run", help="Run in the foreground until a configured stopping condition"
    )
    run.add_argument("name")
    run.add_argument("--max-iter", type=int)
    run.add_argument("--time-cap-s", type=float)
    run.add_argument("--plateau-limit", type=int)
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            config = Config(**tomllib.loads(args.config.read_text()))
            session = Session.create(args.name, config)
            result = {
                "session": session.name,
                "session_dir": str(session.session_dir),
                "workspace_dir": str(session.workspace_dir),
                "branch": session.branch,
            }
        else:
            session = Session.load(args.name)
            if args.command == "baseline":
                result = loop_core.baseline(session)
            elif args.command == "iterate":
                result = loop.iterate(session)
            elif args.command == "run":
                result = loop.run(
                    session, args.max_iter, args.time_cap_s, plateau_limit=args.plateau_limit
                )
            elif args.command == "status":
                result = RunManager().status(session)
            elif args.command == "stop":
                result = stop(session)
            else:
                loop.finalize(session)  # Prints only the report path.
                return 0
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except KeyboardInterrupt:
        if args.command == "run":
            stop(session)
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"autoresearch: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
