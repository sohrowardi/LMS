#!/usr/bin/env python3
"""
Run the LMS indexer.

Examples:
  python3 run_indexer.py --full              # rescan every server
  python3 run_indexer.py --new               # scan only servers never scanned before
  python3 run_indexer.py --retry             # rescan only servers that errored last time
  python3 run_indexer.py --only "Server One,Server Two"
  python3 run_indexer.py --summary           # just print the last scan's results
"""

import argparse
import asyncio
import sys

from common.db import init_db
from indexer.scan import run_scan, print_summary


def main():
    parser = argparse.ArgumentParser(description="LMS Indexer")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--full", action="store_true", help="Rescan every server")
    group.add_argument("--new", action="store_true", help="Scan only never-scanned servers")
    group.add_argument("--retry", action="store_true", help="Rescan only servers that errored")
    group.add_argument("--only", metavar="NAMES", help="Comma-separated server names to rescan")
    group.add_argument("--summary", action="store_true", help="Print last scan's results, no scanning")
    args = parser.parse_args()

    init_db()

    if args.summary:
        print_summary()
        return

    if args.full:
        mode, only = "full", None
    elif args.new:
        mode, only = "new", None
    elif args.retry:
        mode, only = "retry", None
    elif args.only:
        mode, only = "only", [n.strip() for n in args.only.split(",") if n.strip()]
    else:
        # Hard rule from the project brief: never silently wipe-and-rescan
        # everything if no mode was given. Ask explicitly instead.
        answer = input(
            "No scan mode given. Run a FULL rescan of every server now? [y/N]: "
        ).strip().lower()
        if answer != "y":
            print("Nothing to do. Pass --full, --new, --retry, --only, or --summary.")
            sys.exit(0)
        mode, only = "full", None

    try:
        asyncio.run(run_scan(mode, only))
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
