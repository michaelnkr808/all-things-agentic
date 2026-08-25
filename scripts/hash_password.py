#!/usr/bin/env python3
"""Generate a password hash line for config/users.yaml (provisional — Malik).

    python scripts/hash_password.py            # prompts securely
    python scripts/hash_password.py 's3cret'   # inline (careful: shell history)

Paste the printed line as `password_hash:` for the user's entry.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from agentic.server.auth import hash_password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hash_password", description="Print a users.yaml-ready password hash."
    )
    parser.add_argument("password", nargs="?", help="omit to be prompted securely")
    args = parser.parse_args(argv)

    password = args.password if args.password is not None else getpass.getpass("password: ")
    if not password:
        print("error: empty password", file=sys.stderr)
        return 1

    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
