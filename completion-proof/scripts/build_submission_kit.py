#!/usr/bin/env python3
"""Build the complete offline submission kit beside the plugin archive.

The kit mirrors the repository's deliverable subset so the site's existing
relative evidence links keep working when the kit is served:

    submission-kit/
      README.md
      completion-proof/        # source tree (plugin + site + tests + docs)
      evidence/                # raw benchmark, package, video, tests, hooks
      docs/                    # acceptance, submission checklist, publication steps
      SHA256SUMS
      manifest.json
      link-check.json
      scan.json

No publication/submission is performed. The kit is generated from committed,
sanitized evidence; it must not contain operator paths or credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parent
KIT_NAME = "submission-kit"


def copy_tree(source: Path, target: Path, ignore=None) -> None:
    shutil.copytree(source, target, ignore=ignore)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_links(html_path: Path) -> List[str]:
    text = html_path.read_text(encoding="utf-8", errors="replace")
    links = re.findall(r'(?:href|src)="([^"]+)"', text)
    result = []
    for link in links:
        if not link or link.startswith(("http://", "https://", "mailto:", "#", "data:")):
            continue
        base = link.split("#", 1)[0]
        if not base:
            continue
        candidate = (html_path.parent / base).resolve()
        result.append(candidate)
    return result


def serve_and_check(kit: Path) -> Dict[str, Any]:
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), lambda *a, **kw: Handler(*a, directory=str(kit), **kw)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    results = []
    try:
        html = kit / "completion-proof" / "site" / "index.html"
        for candidate in local_links(html):
            relative = candidate.relative_to(kit).as_posix() if str(candidate).startswith(str(kit)) else None
            if relative is None:
                results.append({"target": str(candidate), "ok": False, "reason": "outside kit"})
                continue
            import urllib.request

            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/{relative}", timeout=10) as response:
                    status = response.status
                results.append({"target": relative, "ok": status == 200, "status": status})
            except Exception as exc:  # noqa: BLE001
                results.append({"target": relative, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        server.shutdown()
        server.server_close()
    return {"ok": all(item.get("ok") for item in results), "links": results}


def scan_text(kit: Path) -> Dict[str, Any]:
    patterns = {
        "absolute_home": re.compile(r"/home/[A-Za-z0-9._-]+"),
        "tmp_path": re.compile(r"/tmp/[A-Za-z0-9._-]+"),
        "windows_user": re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+"),
        "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        "aws_key": re.compile(r"AKIA[0-9A-Z]{16}"),
        "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
        "openai_key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        "secret_quoted": re.compile(
            r"(?i)(['\"])([A-Za-z0-9_]*(?:_(?:PASS|PASSWORD|SECRET|TOKEN|KEY|CREDENTIAL|AUTH))|"
            r"password|passwd|secret|token|api[_-]?key|authorization)(['\"]\s*:\s*['\"])([^'\"]+)"
        ),
        "secret_env": re.compile(
            r"\b(?:DATABASE_URL|GITHUB_PAT|GOOGLE_APPLICATION_CREDENTIALS|OPENAI_API_KEY|"
            r"ANTHROPIC_API_KEY|TELEGRAM_CHAT_ID|ALERT_EMAIL|YU_GMAIL_PASS|YU_GMAIL|"
            r"MEM0_USER_ID|DSH_SESSION_ID|HERMES_YOLO_MODE|HERMES_NONINTERACTIVE)\b\s*=\s*\S+"
        ),
    }
    scan_suffixes = {".md", ".txt", ".json", ".jsonl", ".csv", ".yaml", ".yml", ".html", ".js"}
    findings = []
    for path in sorted(kit.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in scan_suffixes:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, pattern in patterns.items():
            for match in pattern.finditer(text):
                findings.append(
                    {
                        "pattern": name,
                        "path": path.relative_to(kit).as_posix(),
                        "preview": match.group(0)[:120],
                    }
                )
    return {"ok": not findings, "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO_ROOT / KIT_NAME))
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if str(out) in {"/", str(REPO_ROOT)} or out.parent == out:
        raise SystemExit(f"refusing unsafe kit output path: {out}")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    copy_tree(PLUGIN_ROOT, out / "completion-proof", ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", "dist", ".git", ".scratch"
    ))
    copy_tree(REPO_ROOT / "evidence", out / "evidence")
    (out / "docs").mkdir()
    for name in ("acceptance.md", "submission-checklist.md", "publication-and-submission.md"):
        source = REPO_ROOT / "docs" / name
        if source.exists():
            shutil.copy2(source, out / "docs" / name)
    shutil.copy2(PLUGIN_ROOT / "README.md", out / "docs" / "plugin-README.md")

    kit_readme = """# Completion Proof — offline submission kit

This kit is the **finished technical candidate** for the Nous Research Plugin
Hackathon. It is **not a submitted entry**: no public repository, catalog PR, X
post or form submission was made. Publication is a separate, human-authorized
step; see `docs/publication-and-submission.md`.

## Contents

- `completion-proof/` — native Hermes plugin source (MIT), tests, scripts, site.
- `completion-proof/site/index.html` — local static marketing/evidence page.
- `evidence/benchmark/` — raw JSON/CSV, generated charts and resource report.
- `evidence/demo/` — real local MP4 replay, transcript, captions, narration and
  ffprobe metadata. The video is labelled **replay, not live**.
- `evidence/install/`, `evidence/hooks/`, `evidence/package/`, `evidence/tests/`
  — real tests, native hook receipts, archive fresh-install and screenshots.
- `docs/` — acceptance mapping, submission checklist, publication steps and the
  plugin README.

## Install the standalone plugin

```bash
tar -xzf evidence/package/completion-proof-0.1.5.tar.gz \
  -C "${HERMES_HOME:-$HOME/.hermes}/plugins"
hermes plugins enable completion-proof
hermes plugins doctor completion-proof --ci
```

Review the check configuration in `config.yaml` before trusting it. The plugin
runs only operator-configured checks; the model cannot supply argv, cwd, env or
paths.

## Verify this kit

```bash
# hashes
sha256sum -c SHA256SUMS

# site and links (any static server keeps the relative evidence links working)
python3 -m http.server 8000
# open http://127.0.0.1:8000/completion-proof/site/

# video metadata
ffprobe -v error -show_entries format=duration,size:stream=codec_name,width,height \
  -of json evidence/demo/completion-proof-demo.mp4
```

## What a PASS proves

Only that the configured command exited 0 on the declared source/test/artifact
scope. It is not proof that all software is correct. Evidence is local and not
cryptographically tamper-proof. The engine makes no network or LLM call; a
`pre_verify` nudge can add agent turns, whose token cost is not measured here.

## External prerequisites (not in this kit)

Public repository; full 40-character catalog SHA pin; catalog PR; X demo post;
Typeform submission; Mauritius eligibility confirmation; human review. Native
Windows remains untested. These are not reasons to stop offline development and
are not permanent product limitations.

License: MIT for newly authored code; see `completion-proof/LICENSE`.
"""
    (out / "README.md").write_text(kit_readme, encoding="utf-8")

    link_check = serve_and_check(out)
    (out / "link-check.json").write_text(json.dumps(link_check, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    scan = scan_text(out)
    (out / "scan.json").write_text(json.dumps(scan, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    entries = []
    for path in sorted(out.rglob("*")):
        if path.is_file() and path.name not in {"SHA256SUMS", "manifest.json"}:
            entries.append(
                {
                    "path": path.relative_to(out).as_posix(),
                    "sha256": sha256(path),
                    "bytes": path.stat().st_size,
                }
            )
    (out / "SHA256SUMS").write_text(
        "\n".join(f"{item['sha256']}  {item['path']}" for item in entries) + "\n",
        encoding="utf-8",
    )
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "completion-proof-submission-kit-v1",
                "files": entries,
                "link_check": "link-check.json",
                "scan": "scan.json",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "kit": str(out),
                "files": len(entries),
                "links_ok": link_check["ok"],
                "scan_ok": scan["ok"],
            },
            indent=2,
        )
    )
    return 0 if link_check["ok"] and scan["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
