#!/usr/bin/env python3
"""Build a deterministic standalone plugin archive plus a SHA-256 manifest.

The archive is reproducible: sorted file order, zeroed timestamps/owners, and a
gzip header with mtime 0. Extract it into ``$HERMES_HOME/plugins/`` and Hermes
will discover ``completion-proof/`` as a native plugin.

Usage:
    python3 scripts/package.py [--out-dir dist] [--json]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Iterable, List

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_NAME = "completion-proof"

EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "dist",
    "evidence",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".swp", ".tmp"}
EXCLUDE_NAMES = {".DS_Store", "data.db", "data.db-shm", "data.db-wal"}


def version() -> str:
    value = (PLUGIN_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not value:
        raise SystemExit("VERSION is empty")
    return value


def iter_files() -> Iterable[Path]:
    for path in sorted(PLUGIN_ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(PLUGIN_ROOT)
        if any(part in EXCLUDE_DIRS for part in relative.parts):
            continue
        if path.suffix in EXCLUDE_SUFFIXES:
            continue
        if path.name in EXCLUDE_NAMES:
            continue
        yield path


def build_archive(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{ARCHIVE_NAME}-{version()}.tar.gz"
    with tempfile.NamedTemporaryFile(prefix=".completion-proof-", suffix=".tar.gz", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with tmp_path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
                    for path in iter_files():
                        relative = path.relative_to(PLUGIN_ROOT)
                        info = tarfile.TarInfo(name=f"{ARCHIVE_NAME}/{relative.as_posix()}")
                        data = path.read_bytes()
                        info.size = len(data)
                        info.mtime = 0
                        info.mode = 0o755 if os.access(path, os.X_OK) else 0o644
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        import io

                        tar.addfile(info, io.BytesIO(data))
        os.replace(tmp_path, archive)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return archive


def write_manifest(out_dir: Path, archives: List[Path]) -> Path:
    manifest = out_dir / "SHA256SUMS"
    lines = []
    for archive in archives:
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        lines.append(f"{digest}  {archive.name}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=str(PLUGIN_ROOT / "dist"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    archive = build_archive(out_dir)
    manifest = write_manifest(out_dir, [archive])
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    summary = {
        "archive": str(archive),
        "sha256": digest,
        "bytes": archive.stat().st_size,
        "manifest": str(manifest),
        "file_count": sum(1 for _ in iter_files()),
        "version": version(),
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"archive:  {archive}")
        print(f"sha256:   {digest}")
        print(f"bytes:    {summary['bytes']}")
        print(f"manifest: {manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
