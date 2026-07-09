from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from .ledger import default_home, resolve_ledger_path


CONFIG_SCHEMA = "rawmem.config.v1"
GLOBAL_CONFIG_SCHEMA = "rawmem.config.v2"

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


def default_global_config() -> dict[str, Any]:
    """Machine-wide daemon config stored at ~/.rawmem/config.json."""
    return {
        "schema": GLOBAL_CONFIG_SCHEMA,
        "ledger": None,
        "daemon": {
            "cycle_seconds": 1.0,
            "serve": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": 8765,
                "require_token": True,
                "token": None,
                "allowed_origins": [
                    "http://127.0.0.1",
                    "http://localhost",
                    "chrome-extension://",
                    "moz-extension://",
                ],
            },
            "watch": {
                "enabled": False,
                "roots": [],
                "interval_seconds": 120,
                "ignore_globs": DEFAULT_IGNORE_GLOBS,
            },
            "tailers": {
                "claude_code": {
                    "enabled": True,
                    "interval_seconds": 20,
                    "include_assistant": True,
                    "include_sidechain": False,
                    "max_chars": 6000,
                    "root": None,
                },
                "codex": {
                    "enabled": True,
                    "interval_seconds": 20,
                    "include_assistant": True,
                    "max_chars": 6000,
                    "root": None,
                },
                "powershell_history": {
                    "enabled": True,
                    "interval_seconds": 15,
                    "path": None,
                    "batch_threshold": 20,
                },
                "clipboard": {
                    "enabled": False,
                    "interval_seconds": 4,
                    "max_chars": 20000,
                },
            },
        },
    }


def new_capture_token() -> str:
    return secrets.token_urlsafe(32)


def ensure_capture_token(config: dict[str, Any], *, rotate: bool = False) -> str:
    serve = config.setdefault("daemon", {}).setdefault("serve", {})
    token = serve.get("token")
    if rotate or not isinstance(token, str) or not token.strip():
        token = new_capture_token()
        serve["token"] = token
    serve.setdefault("require_token", True)
    return token


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def global_config_path() -> Path:
    return default_home() / "config.json"


def load_global_config(path: str | Path | None = None) -> dict[str, Any]:
    """Defaults deep-merged with the user's ~/.rawmem/config.json when present."""
    target = Path(path) if path else global_config_path()
    defaults = default_global_config()
    if not target.exists():
        return defaults
    loaded = load_config(target)
    if not isinstance(loaded, dict):
        return defaults
    return deep_merge(defaults, loaded)


def write_global_config(
    path: str | Path | None = None,
    *,
    force: bool = False,
    include_clipboard: bool = False,
    disable_clipboard: bool = False,
    ensure_browser_token: bool = True,
    rotate_browser_token: bool = False,
) -> Path:
    target = Path(path) if path else global_config_path()
    if (
        target.exists()
        and not force
        and not include_clipboard
        and not disable_clipboard
        and not ensure_browser_token
        and not rotate_browser_token
    ):
        return target
    config = default_global_config() if force or not target.exists() else load_global_config(target)
    if include_clipboard and disable_clipboard:
        raise ValueError("include_clipboard and disable_clipboard cannot both be true")
    if ensure_browser_token or rotate_browser_token:
        ensure_capture_token(config, rotate=rotate_browser_token)
    if include_clipboard:
        config.setdefault("daemon", {}).setdefault("tailers", {}).setdefault("clipboard", {})[
            "enabled"
        ] = True
    if disable_clipboard:
        config.setdefault("daemon", {}).setdefault("tailers", {}).setdefault("clipboard", {})[
            "enabled"
        ] = False
    save_config(target, config)
    return target


def config_path_for(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / ".rawmem" / "config.json"


def load_config(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_config(path: str | Path, config: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
