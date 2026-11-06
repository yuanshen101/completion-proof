#!/usr/bin/env python3
"""Execute one command with the isolated-test environment allowlist.

Usage:
    python3 scripts/clean_exec.py --hermes-home HOME --home USERHOME \
        [--tmp TMPDIR] [--env KEY=VALUE ...] -- hermes plugins list

Used by the demo and package smoke so Hermes never inherits operator credentials
or profile switches. Returns the child exit code.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from completion_proof.sandbox_env import sandbox_env


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--hermes-home", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--tmp", default="")
    parser.add_argument("--env", action="append", default=[])
    parser.add_argument("--", dest="separator", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("clean_exec: missing command after --", file=sys.stderr)
        return 2
    extra = {
        "HERMES_HOME": args.hermes_home,
        "HOME": args.home,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if args.tmp:
        extra["TMPDIR"] = args.tmp
    for item in args.env:
        key, sep, value = item.partition("=")
        if not sep or not key:
            print(f"clean_exec: invalid --env {item!r}", file=sys.stderr)
            return 2
        extra[key] = value
    env = sandbox_env(extra)
    os.execvpe(command[0], command, env)
    return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
