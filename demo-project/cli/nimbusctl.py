#!/usr/bin/env python3
"""nimbusctl — a tiny CLI over the Nimbus API logic (no network needed).

    python cli/nimbusctl.py shorten https://example.com
    python cli/nimbusctl.py resolve 1
    python cli/nimbusctl.py stats 1

Uses the same LinkStore as the API service, pointed at a file DB by default so
codes persist between invocations.
"""

import os
import sys

# Make the api package importable without installing it.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "api"))

from nimbus_api.store import LinkError, LinkStore  # noqa: E402

_STORE = LinkStore(os.environ.get("NIMBUS_DB", "nimbus.db"))


def main(argv):
    if len(argv) < 3:
        print("usage: nimbusctl <shorten|resolve|stats> <arg>", file=sys.stderr)
        return 2
    cmd, arg = argv[1], argv[2]
    try:
        if cmd == "shorten":
            print(_STORE.create_link(arg))
        elif cmd == "resolve":
            print(_STORE.resolve(arg))
        elif cmd == "stats":
            print(_STORE.stats(arg))
        else:
            print(f"unknown command: {cmd}", file=sys.stderr)
            return 2
    except LinkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
