from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .ledger import resolve_ledger_path


CONFIG_SCHEMA = "rawmem.config.v1"

DEFAULT_IGNORE_GLOBS = [
    ".git/**",
    ".rawmem/**",
    ".venv/**",
    "__pycache__/**",
    ".pytest_cache/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
    "node_modules/**",
    "dist/**",
    "build/**",
    "*.pyc",
    "*.pyo",
    "*.log",
]

DEFAULT_GIT_HOOKS = [
    "post-commit",
    "post-checkout",
    "post-merge",
    "post-rewrite",
    "pre-push",
]


def default_config(project_root: str | Path, *, local: bool = True) -> dict[str, Any]:
    root = Path(project_root).resolve()
    ledger = resolve_ledger_path(local=local, cwd=root)
    return {
        "schema": CONFIG_SCHEMA,
        "enabled": True,
        "project_root": str(root),
        "ledger": str(ledger),
        "privacy": {
            "scope": "local_only",
            "review_required": True,
            "upload_default": "never",
        },
        "capture": {
            "manual": {"enabled": True},
            "ingest": {"enabled": True},
            "terminal": {"enabled": True, "mode": "powershell_prompt_hook"},
            "git_hooks": {"enabled": True, "hooks": DEFAULT_GIT_HOOKS},
            "watch": {
                "enabled": True,
                "interval_seconds": 5,
                "paths": [str(root)],
                "ignore_globs": DEFAULT_IGNORE_GLOBS,
            },
            "browser": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": 8765,
                "mode": "localhost_http_plus_bookmarklet",
            },
            "clipboard": {"enabled": True},
        },
    }


def config_path_for(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / ".rawmem" / "config.json"


def load_config(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_config(path: str | Path, config: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
