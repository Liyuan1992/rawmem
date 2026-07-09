from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "rawmem.event.v1"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def default_home() -> Path:
    rawmem_home = os.environ.get("RAWMEM_HOME")
    if rawmem_home:
        return Path(rawmem_home).expanduser()
    return Path.home() / ".rawmem"


def resolve_ledger_path(
    explicit: str | Path | None = None,
    *,
    local: bool = False,
    cwd: str | Path | None = None,
) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env_path = os.environ.get("RAWMEM_LEDGER")
    if env_path:
        return Path(env_path).expanduser()
    base_cwd = Path(cwd or os.getcwd())
    if local:
        return base_cwd / ".rawmem" / "events.jsonl"
    return default_home() / "events.jsonl"


def infer_project(cwd: str | Path | None = None) -> str:
    base = Path(cwd or os.getcwd())
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=base,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).name
    except OSError:
        pass
    return base.resolve().name


def parse_key_value_pairs(items: Iterable[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Expected KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Empty key in field: {item}")
        payload[key] = value
    return payload


def file_artifact(path: str | Path, *, kind: str = "file") -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    artifact: dict[str, Any] = {
        "kind": kind,
        "path": str(resolved),
        "exists": resolved.exists(),
    }
    if resolved.is_file():
        artifact["size"] = resolved.stat().st_size
        artifact["sha256"] = sha256_file(resolved)
    return artifact


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(event: dict[str, Any]) -> str:
    payload = {key: value for key, value in event.items() if key != "content_hash"}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def read_events(ledger_path: str | Path) -> list[dict[str, Any]]:
    path = Path(ledger_path)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path} line {line_number}: {exc}") from exc
    return events


def last_event(ledger_path: str | Path) -> dict[str, Any] | None:
    events = read_events(ledger_path)
    return events[-1] if events else None


def build_event(
    *,
    source: str,
    event_type: str,
    project: str | None = None,
    cwd: str | Path | None = None,
    summary: str | None = None,
    raw_text: str | None = None,
    tags: Iterable[str] = (),
    artifacts: Iterable[dict[str, Any]] = (),
    payload: dict[str, Any] | None = None,
    privacy_scope: str = "local_only",
    review_required: bool = True,
) -> dict[str, Any]:
    base_cwd = Path(cwd or os.getcwd()).resolve()
    return {
        "schema": SCHEMA,
        "event_id": new_event_id(),
        "ts": utc_now_iso(),
        "source": source,
        "event_type": event_type,
        "project": project or infer_project(base_cwd),
        "cwd": str(base_cwd),
        "summary": summary or summarize(raw_text),
        "raw_text": raw_text or "",
        "tags": list(tags),
        "artifacts": list(artifacts),
        "payload": payload or {},
        "privacy": {
            "scope": privacy_scope,
            "review_required": review_required,
        },
        "previous_hash": None,
    }


def summarize(text: str | None, *, limit: int = 120) -> str:
    if not text:
        return ""
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "..."


def append_event(ledger_path: str | Path, event: dict[str, Any]) -> dict[str, Any]:
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = last_event(path)
    event = dict(event)
    event["previous_hash"] = previous.get("content_hash") if previous else None
    event["content_hash"] = content_hash(event)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
    return event


def init_local_store(cwd: str | Path | None = None) -> Path:
    base = Path(cwd or os.getcwd())
    store = base / ".rawmem"
    store.mkdir(parents=True, exist_ok=True)
    gitignore = store / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
    return store / "events.jsonl"


def artifact_dir_for(ledger_path: str | Path, event_id: str) -> Path:
    path = Path(ledger_path)
    return path.parent / "artifacts" / event_id
