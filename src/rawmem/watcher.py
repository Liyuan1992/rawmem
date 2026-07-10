from __future__ import annotations

import fnmatch
import json
import time
from pathlib import Path
from typing import Any, Callable

from .config import DEFAULT_IGNORE_GLOBS
from .ledger import append_event, build_event, resolve_ledger_path


def scan_tree(root: str | Path, ignore_globs: list[str] | None = None) -> dict[str, dict[str, Any]]:
    base = Path(root).resolve()
    ignores = ignore_globs or DEFAULT_IGNORE_GLOBS
    snapshot: dict[str, dict[str, Any]] = {}
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        if is_ignored(rel, ignores):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[rel] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return snapshot


def is_ignored(rel_path: str, ignore_globs: list[str]) -> bool:
    rel = rel_path.replace("\\", "/")
    parts = rel.split("/")
    for pattern in ignore_globs:
        normalized = pattern.replace("\\", "/")
        if fnmatch.fnmatch(rel, normalized):
            return True
        if normalized.endswith("/**"):
            prefix = normalized[:-3]
            if rel == prefix or rel.startswith(prefix + "/"):
                return True
        if normalized in parts:
            return True
    return False


def diff_snapshots(
    previous: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    previous_keys = set(previous)
    current_keys = set(current)
    created = sorted(current_keys - previous_keys)
    deleted = sorted(previous_keys - current_keys)
    modified = sorted(
        path
        for path in previous_keys & current_keys
        if previous[path].get("size") != current[path].get("size")
        or previous[path].get("mtime_ns") != current[path].get("mtime_ns")
    )
    return {
        "created": created,
        "modified": modified,
        "deleted": deleted,
    }


def load_state(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def watch_once(
    *,
    root: str | Path,
    ledger_path: str | Path | None = None,
    local: bool = False,
    project: str | None = None,
    state_path: str | Path | None = None,
    ignore_globs: list[str] | None = None,
    source: str = "file-watch",
    event_policy: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    base = Path(root).resolve()
    ledger = resolve_ledger_path(ledger_path, local=local, cwd=base)
    state_file = Path(state_path) if state_path else ledger.parent / "watch-state.json"
    state = load_state(state_file)
    previous = state.get("files", {})
    current = scan_tree(base, ignore_globs)

    if not previous:
        save_state(state_file, {"root": str(base), "files": current})
        event = build_event(
            source=source,
            event_type="watch_baseline",
            project=project,
            cwd=base,
            summary=f"Watch baseline for {len(current)} files",
            raw_text=f"watch_root={base}\nfiles={len(current)}",
            tags=["watch", "baseline"],
            payload={"root": str(base), "file_count": len(current)},
        )
        if event_policy is not None:
            event = event_policy(event)
            if event is None:
                return None
        return append_event(ledger, event)

    changes = diff_snapshots(previous, current)
    save_state(state_file, {"root": str(base), "files": current})
    if not any(changes.values()):
        return None

    changed_count = sum(len(items) for items in changes.values())
    raw_text = "\n".join(
        [
            f"watch_root={base}",
            f"changed={changed_count}",
            f"created={len(changes['created'])}",
            f"modified={len(changes['modified'])}",
            f"deleted={len(changes['deleted'])}",
            "",
            *[f"+ {item}" for item in changes["created"]],
            *[f"~ {item}" for item in changes["modified"]],
            *[f"- {item}" for item in changes["deleted"]],
        ]
    ).strip()
    event = build_event(
        source=source,
        event_type="file_change_batch",
        project=project,
        cwd=base,
        summary=f"File change batch: {changed_count} paths",
        raw_text=raw_text,
        tags=["watch"],
        payload={"root": str(base), "changes": changes},
    )
    if event_policy is not None:
        event = event_policy(event)
        if event is None:
            return None
    return append_event(ledger, event)


def watch_loop(
    *,
    root: str | Path,
    ledger_path: str | Path | None = None,
    local: bool = False,
    project: str | None = None,
    interval_seconds: float = 5,
    state_path: str | Path | None = None,
    ignore_globs: list[str] | None = None,
    source: str = "file-watch",
    event_policy: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> None:
    while True:
        watch_once(
            root=root,
            ledger_path=ledger_path,
            local=local,
            project=project,
            state_path=state_path,
            ignore_globs=ignore_globs,
            source=source,
            event_policy=event_policy,
        )
        time.sleep(interval_seconds)
