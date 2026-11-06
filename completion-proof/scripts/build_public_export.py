#!/usr/bin/env python3
"""Build the sanitized public-source export under .scratch/public-export.

The export keeps the working nested layout (`completion-proof/` plus root
`evidence/` and `docs/`) so existing relative links and scripts keep working.
It intentionally excludes the submission-kit, private git history, scratch
homes/databases/logs, research scoring docs, hello-migration.sh and workflows.
No publication is performed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parent
SCRIPTS = PLUGIN_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_submission_kit import scan_text, serve_and_check, sha256
from sanitize_evidence import sanitize

EXCLUDE_DIRS = {"__pycache__", ".git", ".scratch", "dist", "submission-kit"}
EXCLUDE_FILES = {"hello-migration.sh"}


def copy_tree_filtered(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(*EXCLUDE_DIRS, "__pycache__", "*.pyc"),
    )


def main() -> int:
    out = Path(os.environ.get("CP_PUBLIC_EXPORT", str(REPO_ROOT / ".scratch" / "public-export"))).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    copy_tree_filtered(PLUGIN_ROOT, out / "completion-proof")
    shutil.copytree(REPO_ROOT / "evidence", out / "evidence", ignore=shutil.ignore_patterns(*EXCLUDE_DIRS))
    (out / "docs").mkdir()
    for name in ("acceptance.md", "submission-checklist.md", "publication-and-submission.md"):
        shutil.copy2(REPO_ROOT / "docs" / name, out / "docs" / name)
    shutil.copy2(REPO_ROOT / "docs" / "public-readme.md", out / "README.md")
    shutil.copy2(PLUGIN_ROOT / "LICENSE", out / "LICENSE")
    (out / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n.venv/\ndist/\n.scratch/\n", encoding="utf-8"
    )

    # Sanitize copied text evidence/docs (source .py is left intact and is not
    # scanned for paths; evidence logs must not carry local absolute paths).
    text_suffixes = {".md", ".txt", ".json", ".jsonl", ".csv", ".yaml", ".yml"}
    for path in out.rglob("*"):
        if path.is_file() and path.suffix.lower() in text_suffixes:
            try:
                original = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            updated = sanitize(original)
            if updated != original:
                path.write_text(updated, encoding="utf-8")

    # Remove files that must never be public.
    for unwanted in (out / "hello-migration.sh", out / ".github"):
        if unwanted.is_dir():
            shutil.rmtree(unwanted, ignore_errors=True)
        elif unwanted.exists():
            unwanted.unlink()

    link_check = serve_and_check(out)
    (out / "link-check.json").write_text(
        json.dumps(link_check, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    scan = scan_text(out)
    (out / "scan.json").write_text(json.dumps(scan, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    files = []
    for path in sorted(out.rglob("*")):
        if path.is_file() and path.name not in {"SHA256SUMS", "PUBLIC-EXPORT.json"}:
            files.append(
                {
                    "path": path.relative_to(out).as_posix(),
                    "sha256": sha256(path),
                    "bytes": path.stat().st_size,
                }
            )
    (out / "SHA256SUMS").write_text(
        "\n".join(f"{item['sha256']}  {item['path']}" for item in files) + "\n",
        encoding="utf-8",
    )
    try:
        status = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout
        commit = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        status, commit = "", ""
    plugin_files = [
        item for item in files
        if item["path"].startswith("completion-proof/") and item["path"].endswith(
            (".py", ".yaml", ".md", ".html", ".js", ".css")
        )
    ]
    source_digest = hashlib.sha256(
        json.dumps(plugin_files, sort_keys=True).encode()
    ).hexdigest()
    provenance = {
        "schema": "completion-proof-public-export-v1",
        "source_commit": commit,
        "working_tree_dirty": bool(status),
        "tested_content_sha256": source_digest,
        "note": (
            "Built from the current working tree. The parent will resolve the final "
            "public commit/SHA after review; this file does not claim an untested future SHA."
        ),
        "link_check": link_check["ok"],
        "scan": scan["ok"],
        "files": files,
    }
    (out / "PUBLIC-EXPORT.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "export": str(out),
                "files": len(files),
                "links_ok": link_check["ok"],
                "scan_ok": scan["ok"],
                "source_commit": commit,
                "dirty": bool(status),
            },
            indent=2,
        )
    )
    return 0 if link_check["ok"] and scan["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
