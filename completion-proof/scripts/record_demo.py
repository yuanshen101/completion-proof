#!/usr/bin/env python3
"""Capture a real CLI run and render it as a replay video.

Every command shown is executed against a throwaway HERMES_HOME with the current
plugin; the displayed stdout/stderr is captured from that run. The MP4 is a
replay (not a live recording); actual execution time is measured and stated in
the video, transcript and metadata.

Usage:
    CP_HERMES_TEMPLATE_HOME=$(bash scripts/prepare_hermes_template.sh) \
      python3 scripts/record_demo.py [--out-dir evidence/demo]

Requires Pillow and ffmpeg/ffprobe on PATH. No network or paid service is used
for the demo itself.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from completion_proof.sandbox_env import sandbox_env

WIDTH = 1280
HEIGHT = 720
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


@dataclass
class Step:
    command: str
    lines: List[str]
    caption: str
    returncode: int
    elapsed_s: float
    excerpt: bool = False


def _find_hermes() -> str:
    hermes = shutil.which("hermes")
    if not hermes:
        raise SystemExit("hermes not found on PATH")
    return hermes


def _run(hermes: str, project: Path, home: Path, userhome: Path, tmpdir: Path, argv: List[str], timeout: int = 60) -> Dict[str, Any]:
    env = sandbox_env(
        {
            "HERMES_HOME": str(home),
            "HOME": str(userhome),
            "TMPDIR": str(tmpdir),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    started = time.monotonic()
    completed = subprocess.run(
        [hermes, *argv],
        cwd=str(project),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {
        "argv": [hermes, *argv],
        "command": "hermes " + shlex.join(argv),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "elapsed_s": round(time.monotonic() - started, 3),
    }


def _run_plain(project: Path, home: Path, userhome: Path, tmpdir: Path, argv: List[str], timeout: int = 60) -> Dict[str, Any]:
    env = sandbox_env(
        {
            "HERMES_HOME": str(home),
            "HOME": str(userhome),
            "TMPDIR": str(tmpdir),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    started = time.monotonic()
    completed = subprocess.run(
        argv,
        cwd=str(project),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {
        "command": shlex.join(
            [Path(part).name if index == 0 else part for index, part in enumerate(argv)]
        ),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "elapsed_s": round(time.monotonic() - started, 3),
    }


def _clean_output(text: str, replacements: Dict[str, str]) -> List[str]:
    for source, target in replacements.items():
        if source:
            text = text.replace(source, target)
    text = re.sub(r"/home/[^\s\"']+", "<HOME>/...", text)
    text = re.sub(r"/tmp/[^\s\"']+", "<TEMP>/...", text)
    text = text.replace("\r", "")
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _steps(hermes: str, project: Path, home: Path, userhome: Path, tmpdir: Path, base: Path) -> List[Step]:
    replacements = {
        str(PLUGIN_ROOT): "<PLUGIN>",
        str(project): "<PROJECT>",
        str(home): "<HERMES_HOME>",
        str(userhome): "<HOME>",
        str(tmpdir): "<TEMP>",
        str(base): "<TEMP>",
    }
    raw_steps = []
    raw_steps.append(_run(hermes, project, home, userhome, tmpdir, ["completion-proof", "checks"]))
    raw_steps.append(_run(hermes, project, home, userhome, tmpdir, ["completion-proof", "verify"]))
    raw_steps.append(
        _run_plain(
            project,
            home,
            userhome,
            tmpdir,
            ["sed", "-i", "s/VALUE = 1/VALUE = 2/", "src/app.py"],
        )
    )
    raw_steps.append(_run(hermes, project, home, userhome, tmpdir, ["completion-proof", "verify"]))
    raw_steps.append(_run(hermes, project, home, userhome, tmpdir, ["completion-proof", "status"]))
    raw_steps.append(
        _run_plain(
            project,
            home,
            userhome,
            tmpdir,
            ["sed", "-i", "s/VALUE = 2/VALUE = 3/", "src/app.py"],
        )
    )
    raw_steps.append(_run(hermes, project, home, userhome, tmpdir, ["completion-proof", "status"]))

    captions = [
        "Configured checks are operator settings: argv, cwd, timeout and declared globs.",
        "The check fails, so the receipt is FAIL. The command output is not independent evidence.",
        "Fix the source, then run the configured check again.",
        "The receipt is PASS. It proves only that this command exited 0 on the declared scope.",
        "Freshness is computed from the current fingerprint, not from the receipt timestamp.",
        "Change one covered source file. The historical PASS is retained but no longer current.",
        "The status is STALE: the old receipt is not proof for this revision.",
    ]
    steps: List[Step] = []
    for index, raw in enumerate(raw_steps):
        text = raw["stdout"] + (("\n" + raw["stderr"]) if raw["stderr"] else "")
        lines = _clean_output(text, replacements)
        excerpt = False
        if raw["command"].endswith("status"):
            lines = lines[:10]
            excerpt = True
        elif len(lines) > 20:
            lines = lines[:20]
            excerpt = True
        steps.append(
            Step(
                command=raw["command"],
                lines=lines,
                caption=captions[index],
                returncode=raw["returncode"],
                elapsed_s=raw["elapsed_s"],
                excerpt=excerpt,
            )
        )
    return steps


def _wrap(text: str, width: int = 102) -> List[str]:
    if not text:
        return [""]
    out: List[str] = []
    while len(text) > width:
        cut = text.rfind(" ", 0, width)
        if cut <= 0:
            cut = width
        out.append(text[:cut])
        text = text[cut:].lstrip()
    out.append(text)
    return out


def _draw_frame(
    path: Path,
    lines: List[str],
    caption: str,
    *,
    title: str = "",
    badge: str = "REPLAY — NOT LIVE",
    footer: str = "",
):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (WIDTH, HEIGHT), "#0b1020")
    draw = ImageDraw.Draw(image)
    mono = ImageFont.truetype(FONT_MONO, 16)
    sans = ImageFont.truetype(FONT_SANS, 17)
    small = ImageFont.truetype(FONT_SANS, 14)
    draw.rounded_rectangle((24, 20, WIDTH - 24, HEIGHT - 24), radius=14, fill="#111a31", outline="#263251")
    draw.rectangle((24, 20, WIDTH - 24, 62), fill="#172750")
    draw.text((44, 33), "Completion Proof — real CLI replay", font=sans, fill="#e8ecf8")
    draw.text((WIDTH - 210, 34), badge, font=small, fill="#7ce2b5")
    if title:
        draw.text((44, 82), title, font=sans, fill="#e8ecf8")
    y = 118 if title else 88
    for line in lines:
        for wrapped in _wrap(line):
            if y > HEIGHT - 150:
                break
            color = "#7ce2b5" if wrapped.startswith("$ ") else "#d6def2"
            draw.text((44, y), wrapped, font=mono, fill=color)
            y += 22
        if y > HEIGHT - 150:
            break
    if footer:
        draw.text((44, HEIGHT - 142), footer, font=small, fill="#9aa8c7")
    draw.rectangle((24, HEIGHT - 108, WIDTH - 24, HEIGHT - 24), fill="#070b16")
    draw.text((44, HEIGHT - 90), caption, font=sans, fill="#e8ecf8")
    draw.text((44, HEIGHT - 58), "Captions in English · résumé français à la fin", font=small, fill="#9aa8c7")
    image.save(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=str(PLUGIN_ROOT.parent / "evidence" / "demo"))
    parser.add_argument("--template-home", default=os.environ.get("CP_HERMES_TEMPLATE_HOME", ""))
    args = parser.parse_args()
    template = Path(args.template_home).resolve()
    if not template or not (template / "installs").is_dir():
        raise SystemExit("CP_HERMES_TEMPLATE_HOME with installs/ is required")
    hermes = _find_hermes()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    try:
        base = Path(tempfile.mkdtemp(prefix="cp-video-"))
    except OSError as exc:
        raise SystemExit(f"cannot create demo temp dir: {exc}") from exc
    try:
        home = base / "home"
        home.mkdir()
        os.symlink(template / "installs", home / "installs")
        userhome = base / "userhome"
        userhome.mkdir()
        tmpdir = base / "tmp"
        tmpdir.mkdir()
        plugin_dir = home / "plugins"
        plugin_dir.mkdir()
        shutil.copytree(
            PLUGIN_ROOT,
            plugin_dir / "completion-proof",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "site", "evidence", "dist", ".git"),
        )
        project = base / "proj"
        (project / "src").mkdir(parents=True)
        (project / "tests").mkdir()
        (project / "src" / "app.py").write_text("VALUE = 1\n")
        (project / "tests" / "test_app.py").write_text(
            "import pathlib\nimport sys\nimport unittest\n\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / \"src\"))\n"
            "from app import VALUE\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertEqual(VALUE, 2)\n\n"
            "if __name__ == \"__main__\":\n    unittest.main()\n"
        )
        import yaml

        config = {
            "updates": {"check": False},
            "plugins": {
                "enabled": ["completion-proof"],
                "entries": {
                    "completion-proof": {
                        "enabled": True,
                        "settings": {
                            "workspace": str(project),
                            "checks": [
                                {
                                    "id": "unit",
                                    "description": "fixture unittest (fails until the bug is fixed)",
                                    "argv": ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"],
                                    "source_globs": ["src/**/*.py"],
                                    "test_globs": ["tests/**/*.py"],
                                    "timeout_seconds": 30,
                                }
                            ],
                        },
                    }
                },
            },
        }
        (home / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
        started = time.monotonic()
        steps = _steps(hermes, project, home, userhome, tmpdir, base)
        total_elapsed = round(time.monotonic() - started, 2)
        pid_file = home / "gateway.pid"
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text().strip()), 9)
            except (OSError, ValueError):
                pass
    finally:
        # Keep the temporary tree until after frames are rendered; cleaned below.
        pass

    frames_dir = base / "frames"
    frames_dir.mkdir()
    frame_specs: List[tuple[Path, float]] = []
    title_lines = [
        "A real run against the current plugin in an isolated HERMES_HOME.",
        "Every command/result below was executed during this capture.",
        "This video is a replay with fixed display timing — not a live recording.",
        f"Actual execution elapsed: {total_elapsed}s (measured).",
        "",
        "Scenario: configured test check FAIL -> fix -> PASS -> source edit -> STALE.",
    ]
    title_path = frames_dir / "frame_0001.png"
    _draw_frame(
        title_path,
        title_lines,
        "Completion Proof: a receipt tied to the current revision, not a memory of a command.",
        title="Replay of a captured real CLI run",
        badge="REPLAY — NOT LIVE",
        footer="No model call, no network call for the checks. Local isolated runtime.",
    )
    frame_specs.append((title_path, 4.0))
    counter = 2
    for step in steps:
        lines = [f"$ {step.command}"] + step.lines
        if step.excerpt:
            lines.append("[displayed output excerpt; full capture is in demo_capture.json]")
        lines.append(f"[exit {step.returncode}]")
        path = frames_dir / f"frame_{counter:04d}.png"
        _draw_frame(
            path,
            lines,
            step.caption,
            footer=f"Actual command duration: {step.elapsed_s:.3f}s (video timing is a replay)",
        )
        frame_specs.append((path, 2.4))
        counter += 1
    final_lines = [
        "Completion Proof — what the receipt proves",
        "",
        "The configured command exited 0 on the declared source/test/artifact scope.",
        "A relevant change makes the old PASS STALE; history is retained, proof is current-only.",
        "The engine is deterministic, local and network-free; the model cannot supply argv/cwd/env.",
        "",
        "En bref (français)",
        "Les contrôles configurés produisent des reçus PASS/FAIL/UNKNOWN liés aux empreintes",
        "SHA-256 des fichiers déclarés. Un PASS devient STALE dès qu'un fichier pertinent change.",
        "Local, déterministe, sans réseau ni LLM dans le moteur de vérification.",
        "",
        "Full evidence: evidence/ and docs/acceptance.md. Not a public submission.",
    ]
    final_path = frames_dir / f"frame_{counter:04d}.png"
    _draw_frame(
        final_path,
        final_lines,
        "Technical kit complete for this offline run; publication/submission not executed.",
        title="Summary",
        badge="REPLAY — NOT LIVE",
    )
    frame_specs.append((final_path, 5.0))

    # ffmpeg consumes one image per second; duplicate each frame for its duration.
    sequence_dir = base / "sequence"
    sequence_dir.mkdir()
    index = 0
    for path, duration in frame_specs:
        for _ in range(max(1, int(round(duration)))):
            shutil.copyfile(path, sequence_dir / f"f_{index:04d}.png")
            index += 1
    video = out / "completion-proof-demo.mp4"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg not found")
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-framerate",
        "1",
        "-i",
        str(sequence_dir / "f_%04d.png"),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "21",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(video),
    ]
    subprocess.run(command, check=True, stdin=subprocess.DEVNULL)
    ffprobe = shutil.which("ffprobe")
    probe = {}
    if ffprobe:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration,size:stream=codec_name,width,height,r_frame_rate,pix_fmt",
                "-of",
                "json",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        probe = json.loads(completed.stdout or "{}")
        (out / "ffprobe.json").write_text(json.dumps(probe, indent=2, sort_keys=True) + "\n")
        (out / "ffprobe.txt").write_text(completed.stdout)
    capture = {
        "schema": "completion-proof-demo-v1",
        "replay": True,
        "live": False,
        "actual_execution_elapsed_s": total_elapsed,
        "video_timing": "fixed replay timing; not live, no speed claim",
        "commands": [
            {
                "command": step.command,
                "returncode": step.returncode,
                "elapsed_s": step.elapsed_s,
                "output": step.lines,
                "excerpt": step.excerpt,
                "caption": step.caption,
            }
            for step in steps
        ],
        "video": {
            "path": "completion-proof-demo.mp4",
            "duration_s": float((probe.get("format") or {}).get("duration", 0) or 0),
            "codec": ((probe.get("streams") or [{}])[0]).get("codec_name"),
            "width": ((probe.get("streams") or [{}])[0]).get("width"),
            "height": ((probe.get("streams") or [{}])[0]).get("height"),
        },
    }
    (out / "demo_capture.json").write_text(json.dumps(capture, indent=2, sort_keys=True) + "\n")
    transcript = [
        "Completion Proof — captured real CLI run (replay, not live)",
        f"Actual execution elapsed: {total_elapsed}s",
        "Timing in the MP4 is fixed replay timing; no live/speed claim.",
        "",
    ]
    for step in steps:
        transcript.append(f"$ {step.command}")
        transcript.extend(step.lines)
        if step.excerpt:
            transcript.append("[displayed output excerpt; full capture in demo_capture.json]")
        transcript.append(f"[exit {step.returncode}] (actual {step.elapsed_s:.3f}s)")
        transcript.append("")
    (out / "transcript.txt").write_text("\n".join(transcript) + "\n")
    (out / "render-command.txt").write_text(
        "CP_HERMES_TEMPLATE_HOME=\"${TMPDIR:?}/cp-hermes-template\" \\\n"
        "  python3 scripts/record_demo.py --out-dir evidence/demo\n"
    )
    (out / "narration.md").write_text(
        "\n".join(
            [
                "# Demo narration (English)",
                "",
                "Completion Proof is a native Hermes plugin. This is a replay of a real CLI run;",
                "the commands and results were executed for this capture, and video timing is not live.",
                "",
                "1. `hermes completion-proof checks` shows the operator-configured check. The model",
                "   cannot supply argv, cwd, env or globs.",
                "2. The configured test fails, so the receipt is FAIL. Passing text in output is not",
                "   independent proof.",
                "3. Fix the source and run the configured check again.",
                "4. The receipt is PASS and the current status is fresh.",
                "5. Change one covered source file; the status becomes STALE. Historical evidence is",
                "   retained, but it is not proof for this revision.",
                "",
                "## Résumé français",
                "",
                "Completion Proof transforme une exécution de contrôles configurés en reçus",
                "PASS/FAIL/UNKNOWN liés aux empreintes SHA-256 des fichiers déclarés. Un PASS",
                "devient STALE dès qu'un fichier pertinent change. Le moteur est local et",
                "déterministe, sans réseau ni LLM. Cette vidéo est un replay d'une exécution réelle ;",
                "le temps d'exécution mesuré est indiqué séparément.",
                "",
                "Actual command execution elapsed: " + str(total_elapsed) + "s.",
            ]
        )
        + "\n"
    )
    # SRT captions from the same logical timings.
    srt = []
    time_cursor = 0.0
    for number, (path, duration) in enumerate(frame_specs, start=1):
        if number == 1:
            caption = "Replay of captured real commands. Not live. Actual execution elapsed: %ss." % total_elapsed
        elif number == len(frame_specs):
            caption = "French summary: PASS/FAIL/UNKNOWN receipts tied to file fingerprints; a relevant change makes old PASS STALE. Local, deterministic, no network/LLM in the engine."
        else:
            caption = steps[number - 2].caption
        start = time_cursor
        end = time_cursor + duration
        time_cursor = end

        def stamp(value: float) -> str:
            hours = int(value // 3600)
            minutes = int((value % 3600) // 60)
            seconds = int(value % 60)
            millis = int(round((value - int(value)) * 1000))
            return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

        srt.append(f"{number}\n{stamp(start)} --> {stamp(end)}\n{caption}\n")
    (out / "video_captions.srt").write_text("\n".join(srt))
    shutil.rmtree(base, ignore_errors=True)
    print(json.dumps(capture["video"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
