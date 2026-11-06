#!/usr/bin/env python3
"""Capture real before/after screenshots of the local static marketing page.

Before: an empty document root served over HTTP, so the requested page is a real
HTTP 404 (the page did not exist). After: the actual ``site/index.html`` with the
generated benchmark data. No mockup is presented as the previous implementation.

Usage:
    CP_CHROME_BIN=/path/to/chrome python3 scripts/capture_site.py
"""

from __future__ import annotations

import io
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parent
OUT_DIR = REPO_ROOT / "evidence" / "site"


def find_chrome() -> Optional[str]:
    explicit = os.environ.get("CP_CHROME_BIN", "").strip()
    if explicit and Path(explicit).exists():
        return explicit
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found
    cache = Path.home() / ".cache" / "ms-playwright"
    if cache.is_dir():
        # The headless shell is more reliable than full Chrome in containers
        # while still rendering the real page.
        shell = sorted(cache.glob("chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))
        if shell:
            return str(shell[-1])
        candidates = sorted(cache.glob("chromium-*/chrome-linux*/chrome"))
        if candidates:
            return str(candidates[-1])
    return None


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass


def serve(directory: Path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **kw: QuietHandler(*a, directory=str(directory), **kw))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def screenshot(chrome: str, url: str, output: Path, height: int = 1800) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        chrome,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        f"--window-size=1280,{height}",
        "--virtual-time-budget=3000",
        f"--screenshot={output}",
        url,
    ]
    last = ""
    for _attempt in range(3):
        completed = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90
        )
        if completed.returncode == 0 and output.exists():
            return
        last = f"chrome screenshot failed ({completed.returncode}): {completed.stderr[-1000:]}"
        time.sleep(1.0)
    raise SystemExit(last)


def baseline_ref() -> str:
    explicit = os.environ.get("CP_BEFORE_REF", "").strip()
    if explicit:
        return explicit
    try:
        completed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "merge-base", "HEAD", "origin/main"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def extract_ref(ref: str, target: Path) -> bool:
    if not ref:
        return False
    try:
        completed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "archive", ref],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout:
            return False
        with tarfile.open(fileobj=io.BytesIO(completed.stdout), mode="r:") as archive:
            try:
                archive.extractall(target, filter="data")
            except TypeError:  # Python < 3.12
                archive.extractall(target)
        return True
    except (OSError, tarfile.TarError, subprocess.SubprocessError):
        return False


def main() -> int:
    chrome = find_chrome()
    if not chrome:
        print("no Chromium/Chrome found; set CP_CHROME_BIN to capture screenshots", file=sys.stderr)
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ref = baseline_ref()
    with tempfile.TemporaryDirectory(prefix="cp-site-before-") as before_dir:
        before_root = Path(before_dir)
        extracted = extract_ref(ref, before_root)
        before_server, _thread = serve(before_root)
        try:
            port = before_server.server_address[1]
            screenshot(
                chrome,
                f"http://127.0.0.1:{port}/completion-proof/site/",
                OUT_DIR / "before-404.png",
                height=1800,
            )
        finally:
            before_server.shutdown()
        print(
            "before baseline: "
            + (f"git ref {ref} (site/ absent)" if extracted else "empty document root")
        )

    after_server, _thread = serve(REPO_ROOT)
    try:
        port = after_server.server_address[1]
        screenshot(
            chrome,
            f"http://127.0.0.1:{port}/completion-proof/site/",
            OUT_DIR / "after.png",
        )
    finally:
        after_server.shutdown()

    print(f"wrote {OUT_DIR / 'before-404.png'} and {OUT_DIR / 'after.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
