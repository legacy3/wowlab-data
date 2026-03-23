#!/usr/bin/env python3
"""
Update game data tables and generate structure change report.

Usage:
    python scripts/update-data.py <source> <version>

Source can be:
    - A URL to a zip file (https://...)
    - A local zip or tar archive (.zip, .tar, .tar.gz, .tgz, .tar.bz2)
    - A local directory containing CSV files
"""

from __future__ import annotations

import argparse
import csv
import shutil
import tarfile
import tempfile
from pathlib import Path
from urllib.request import urlretrieve
from zipfile import ZipFile


def get_csv_header(path: Path) -> list[str] | None:
    """Read the header row from a CSV file."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            return next(reader, None)
    except Exception:
        return None


def read_headers(directory: Path) -> dict[str, list[str]]:
    """Read CSV headers from all files in a directory."""
    headers = {}
    if directory.exists():
        for csv_file in sorted(directory.glob("*.csv")):
            header = get_csv_header(csv_file)
            if header:
                headers[csv_file.stem] = header
    return headers


def copy_csvs_from_dir(src: Path, dest: Path) -> None:
    """Copy CSV files from a directory to destination."""
    for csv_file in src.glob("*.csv"):
        shutil.copy2(csv_file, dest / csv_file.name)


def extract_zip(zip_path: Path, dest: Path) -> None:
    """Extract CSV files from a zip archive."""
    with ZipFile(zip_path) as zf:
        for member in zf.namelist():
            if member.endswith(".csv"):
                filename = Path(member).name
                with zf.open(member) as src, open(dest / filename, "wb") as dst:
                    dst.write(src.read())


def extract_tar(tar_path: Path, dest: Path) -> None:
    """Extract CSV files from a tar archive."""
    with tarfile.open(tar_path) as tf:
        for member in tf.getmembers():
            if member.isfile() and member.name.endswith(".csv"):
                filename = Path(member.name).name
                with tf.extractfile(member) as src, open(dest / filename, "wb") as dst:
                    dst.write(src.read())


def load_source(source: str, dest: Path, tmp_dir: Path) -> None:
    """Load CSV data from a URL, archive, or directory into dest."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)

    source_path = Path(source)

    # Local directory
    if source_path.is_dir():
        print(f"Copying from {source}...")
        copy_csvs_from_dir(source_path, dest)
        return

    # Remote URL — download first
    if source.startswith(("http://", "https://")):
        archive_path = tmp_dir / "data.archive"
        print(f"Downloading {source}...")
        urlretrieve(source, archive_path)
        source_path = archive_path

    # Local or downloaded archive
    if not source_path.is_file():
        raise FileNotFoundError(f"Source not found: {source}")

    suffix = "".join(source_path.suffixes).lower()
    print(f"Extracting {source_path.name}...")

    if suffix == ".zip":
        extract_zip(source_path, dest)
    elif suffix in (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz"):
        extract_tar(source_path, dest)
    else:
        raise ValueError(
            f"Unsupported archive format: {suffix}. "
            "Expected .zip, .tar, .tar.gz, .tgz, .tar.bz2, or .tar.xz"
        )


def generate_diff(
    old_headers: dict[str, list[str]], new_headers: dict[str, list[str]]
) -> dict:
    """Compare old and new headers, return structured diff."""
    all_tables = sorted(set(old_headers) | set(new_headers))
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
    parser.add_argument(
        "source",
        help="URL, local archive (.zip/.tar/.tar.gz), or directory with CSV files",
    )
    parser.add_argument("version", help="Version string (e.g., 12.0.0.65459)")
    args = parser.parse_args()

    repo_dir = Path(__file__).resolve().parent.parent
    data_dir = repo_dir / "data" / "tables"
    changes_dir = repo_dir / "changes"

    # Save old headers
    print("Reading old headers...")
    old_headers = read_headers(data_dir)
    print(f"  {len(old_headers)} tables")

    # Load new data
    with tempfile.TemporaryDirectory() as tmp_dir:
        load_source(args.source, data_dir, Path(tmp_dir))

    # Read new headers
    print("Reading new headers...")
    new_headers = read_headers(data_dir)
    print(f"  {len(new_headers)} tables")

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
