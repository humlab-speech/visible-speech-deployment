#!/usr/bin/env python3
"""Rename legacy .meta_json files to .json in VISP repository data.

The container-agent source was updated to emit .json session metadata files
instead of the old .meta_json format. This script migrates existing project
data on disk so that old projects are consistent with new ones.

Files renamed:
  <session>_ses/<session>.meta_json   ->  <session>_ses/<session>.json
  VISP_emuDB/VISP.meta_json           ->  VISP_emuDB/VISP.json

Usage:
  python3 scripts/migrate-meta-json.py              # dry run (no changes)
  python3 scripts/migrate-meta-json.py --apply      # actually rename files
  python3 scripts/migrate-meta-json.py --path /path/to/repos   # custom root
"""

import argparse
import sys
from pathlib import Path


def find_meta_json_files(root: Path) -> list[tuple[Path, Path]]:
    """Return list of (src, dst) rename pairs found under root."""
    pairs: list[tuple[Path, Path]] = []

    for meta_file in sorted(root.rglob("*.meta_json")):
        dst = meta_file.with_suffix(".json")
        pairs.append((meta_file, dst))

    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description="Rename .meta_json files to .json in VISP repository data.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually rename files. Without this flag the script is a dry run.",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=Path(__file__).parent.parent / "mounts" / "repositories",
        help="Root directory to scan (default: mounts/repositories/)",
    )
    args = parser.parse_args()

    root: Path = args.path.resolve()
    if not root.exists():
        print(f"Error: path does not exist: {root}", file=sys.stderr)
        return 1

    pairs = find_meta_json_files(root)

    if not pairs:
        print(f"No .meta_json files found under {root}")
        return 0

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"[{mode}] Scanning {root}")
    print()

    renamed = 0
    skipped = 0
    errors = 0

    for src, dst in pairs:
        rel = src.relative_to(root)
        if dst.exists():
            print(f"  SKIP  {rel}  (target .json already exists)")
            skipped += 1
            continue

        if args.apply:
            try:
                src.rename(dst)
                print(f"  RENAME  {rel}  ->  {dst.name}")
                renamed += 1
            except OSError as e:
                print(f"  ERROR  {rel}:  {e}", file=sys.stderr)
                errors += 1
        else:
            print(f"  WOULD RENAME  {rel}  ->  {dst.name}")
            renamed += 1

    print()
    if args.apply:
        print(f"Done: {renamed} renamed, {skipped} skipped, {errors} errors.")
        if errors:
            return 1
    else:
        print(f"Dry run: {renamed} would be renamed, {skipped} would be skipped.")
        print("Re-run with --apply to perform the renames.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
