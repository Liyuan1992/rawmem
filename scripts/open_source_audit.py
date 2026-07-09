from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{12,}"),
]

PRIVATE_PATH_PATTERNS = [
    re.compile(r"C:\\Users\\Administrator", re.IGNORECASE),
    re.compile(r"D:\\Dev\\Projects", re.IGNORECASE),
    re.compile(r"E:\\", re.IGNORECASE),
]

PATH_SCAN_SUFFIXES = {".md", ".py", ".toml", ".json", ".js", ".html"}
PRIVATE_PATH_ALLOWLIST_PREFIXES = {"tests/"}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return [ROOT / line.strip() for line in result.stdout.splitlines() if line.strip()]


def should_scan_text(path: Path) -> bool:
    return path.suffix.lower() in PATH_SCAN_SUFFIXES or path.name in {"LICENSE", "AGENTS.md"}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def main() -> int:
    issues: list[str] = []
    for path in tracked_files():
        relative = rel(path)
        if relative.startswith(".rawmem/") or relative.startswith("data/private/"):
            issues.append(f"private tracked path: {relative}")
            continue
        if not should_scan_text(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                issues.append(f"possible secret in {relative}: {pattern.pattern}")
        if relative != "scripts/open_source_audit.py" and not any(
            relative.startswith(prefix) for prefix in PRIVATE_PATH_ALLOWLIST_PREFIXES
        ):
            for pattern in PRIVATE_PATH_PATTERNS:
                if pattern.search(text):
                    issues.append(f"private workstation path in {relative}: {pattern.pattern}")
    if issues:
        print("open-source audit failed:")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("open-source audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
