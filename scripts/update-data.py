#!/usr/bin/env python3
"""
Update game data tables and generate structure change report.

Usage:
    python scripts/update-data.py <zip_url> <version>
"""

from __future__ import annotations

import argparse
import csv
import shutil
import tempfile
from pathlib import Path
from typing import Optional
from urllib.request import urlretrieve
from zipfile import ZipFile


def get_csv_header(path: Path) -> Optional[list[str]]:
    """Read the header row from a CSV file."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            return next(reader, None)
    except Exception:
        return None


def download_and_extract(url: str, dest: Path, tmp_dir: Path) -> None:
    """Download zip from URL and extract CSV files to destination."""
    zip_path = tmp_dir / "data.zip"

    print(f"Downloading {url}...")
    urlretrieve(url, zip_path)

    print(f"Extracting to {dest}...")
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)

    with ZipFile(zip_path) as zf:
        for member in zf.namelist():
            if member.endswith(".csv"):
                filename = Path(member).name
                with zf.open(member) as src, open(dest / filename, "wb") as dst:
                    dst.write(src.read())


def generate_diff(old_headers: dict, new_headers: dict) -> dict:
    """Compare old and new headers, return structured diff."""
    all_tables = sorted(set(old_headers.keys()) | set(new_headers.keys()))
    changes = {}

    for table in all_tables:
        old = old_headers.get(table)
        new = new_headers.get(table)

        if old is None and new is not None:
            changes[table] = {"status": "added", "columns": new}
        elif old is not None and new is None:
            changes[table] = {"status": "removed"}
        elif old != new:
            old_set = set(old)
            new_set = set(new)
            added = [c for c in new if c not in old_set]
            removed = [c for c in old if c not in new_set]
            changes[table] = {
                "status": "modified",
                "added": added,
                "removed": removed,
            }

    return changes


def write_markdown(changes: dict, version: str, output_path: Path) -> None:
    """Write changes to markdown file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [f"# {version} Structure Changes", ""]

    if not changes:
        lines.append("No structure changes detected.")
    else:
        for table, diff in sorted(changes.items()):
            if diff["status"] == "added":
                lines.append(f"## {table} (NEW TABLE)")
                lines.append("")
                lines.append("**Columns:**")
                for col in diff["columns"]:
                    lines.append(f"- `{col}`")
                lines.append("")

            elif diff["status"] == "removed":
                lines.append(f"## {table} (REMOVED)")
                lines.append("")

            elif diff["status"] == "modified":
                lines.append(f"## {table}")
                lines.append("")
                if diff["added"]:
                    lines.append("**Added:**")
                    for col in diff["added"]:
                        lines.append(f"- `{col}`")
                    lines.append("")
                if diff["removed"]:
                    lines.append("**Removed:**")
                    for col in diff["removed"]:
                        lines.append(f"- `{col}`")
                    lines.append("")

    output_path.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(
        description="Update game data and generate structure diff"
    )
    parser.add_argument("url", help="URL to the zip file containing new data")
    parser.add_argument("version", help="Version string (e.g., 12.0.0.65459)")
    args = parser.parse_args()

    repo_dir = Path(__file__).resolve().parent.parent
    data_dir = repo_dir / "data" / "tables"
    changes_dir = repo_dir / "changes"

    # Save old headers
    print("Reading old headers...")
    old_headers = {}
    if data_dir.exists():
        for csv_file in data_dir.glob("*.csv"):
            header = get_csv_header(csv_file)
            if header:
                old_headers[csv_file.stem] = header

    # Download and extract new data
    with tempfile.TemporaryDirectory() as tmp_dir:
        download_and_extract(args.url, data_dir, Path(tmp_dir))

    # Read new headers
    print("Reading new headers...")
    new_headers = {}
    for csv_file in data_dir.glob("*.csv"):
        header = get_csv_header(csv_file)
        if header:
            new_headers[csv_file.stem] = header

    # Generate diff
    print("Generating diff...")
    changes = generate_diff(old_headers, new_headers)

    # Write markdown
    output_path = changes_dir / f"{args.version}.md"
    write_markdown(changes, args.version, output_path)

    print(f"\nDone! Changes written to {output_path}")
    print(f"Tables with changes: {len(changes)}")

    # Print summary
    if changes:
        print("\n" + "=" * 50)
        print(output_path.read_text())


if __name__ == "__main__":
    main()
