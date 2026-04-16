"""Helper command for saving agent-browser screenshots into Qazy results."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def slugify(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower() or "shot"


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def next_index(screenshot_dir: Path, prefix: str) -> int:
    return 1 + sum(1 for path in screenshot_dir.glob(f"{prefix}-*.png") if path.is_file())


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    label = slugify(" ".join(args) if args else "shot")
    screenshot_dir = Path(required_env("QAZY_SCREENSHOT_DIR")).resolve()
    manifest_path = Path(required_env("QAZY_SCREENSHOT_MANIFEST")).resolve()
    prefix = required_env("QAZY_SCREENSHOT_PREFIX")

    screenshot_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    filename = f"{prefix}-{next_index(screenshot_dir, prefix):02d}-{label}.png"
    output_path = screenshot_dir / filename

    result = subprocess.run(
        ["agent-browser", "screenshot", str(output_path)],
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "agent-browser screenshot failed"
        print(message, file=sys.stderr)
        return result.returncode or 1

    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(str(output_path) + "\n")

    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
