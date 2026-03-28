"""One-command pipeline: download data + load into DuckDB + backfill Test gaps.

Usage:
    python scripts/refresh.py              # incremental (skip existing)
    python scripts/refresh.py --force      # full re-download + reload
    python scripts/refresh.py --load-only  # skip download, just reload DB
"""

import argparse
import time

from download_data import download_cricsheet, download_kaggle
from load_cricsheet import load_cricsheet
from load_kaggle import load_kaggle
from backfill_tests import backfill


def main():
    parser = argparse.ArgumentParser(description="Download + load all cricket data")
    parser.add_argument("--force", action="store_true", help="Re-download and reload everything")
    parser.add_argument("--load-only", action="store_true", help="Skip downloads, just reload DB")
    args = parser.parse_args()

    start = time.time()

    if not args.load_only:
        print("=" * 60)
        print("STEP 1: Download data")
        print("=" * 60)
        download_cricsheet(force=args.force)
        download_kaggle()

    print("\n" + "=" * 60)
    print("STEP 2: Load Cricsheet into DuckDB")
    print("=" * 60)
    load_cricsheet(force=args.force)

    print("\n" + "=" * 60)
    print("STEP 3: Load Kaggle into DuckDB")
    print("=" * 60)
    load_kaggle(force=args.force)

    print("\n" + "=" * 60)
    print("STEP 4: Backfill missing Test matches from Cricsheet → Kaggle tables")
    print("=" * 60)
    backfill()

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
