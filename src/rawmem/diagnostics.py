from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import default_global_config, global_config_path, load_global_config
from .daemon import status_path
from .ledger import resolve_ledger_path
from .setup_tools import git_config_get_global, global_git_hooks_dir, startup_task_exists


@dataclass(frozen=True)
class DiagnosticCheck:
    name: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def run_diagnostics(
    *,
    config_path: str | Path | None = None,
    timeout: float = 1.5,
    status_max_age: float = 30.0,
) -> list[DiagnosticCheck]:
    checks: list[DiagnosticCheck] = []
    config_file = Path(config_path) if config_path else global_config_path()
    config = default_global_config()
    if not config_file.exists():
        checks.append(
            DiagnosticCheck(
                "config",
                "WARN",
                f"missing {config_file}; run `rawmem config --init`",
            )
        )
    else:
        try:
            config = load_global_config(config_file)
            checks.append(DiagnosticCheck("config", "PASS", f"loaded {config_file}"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            checks.append(DiagnosticCheck("config", "FAIL", f"cannot load {config_file}: {exc}"))

    serve = (config.get("daemon") or {}).get("serve") or {}
    serve_enabled = bool(serve.get("enabled", True))
    require_token = bool(serve.get("require_token", True))
    token = serve.get("token")
    token_ready = isinstance(token, str) and bool(token.strip())
    if not serve_enabled:
        checks.append(DiagnosticCheck("browser token", "WARN", "capture endpoint is disabled"))
    elif require_token and token_ready:
        checks.append(DiagnosticCheck("browser token", "PASS", "configured (value hidden)"))
    elif require_token:
        checks.append(
            DiagnosticCheck(
                "browser token",
                "FAIL",
                "required but missing; run `rawmem config --init`",
            )
        )
    else:
        checks.append(DiagnosticCheck("browser token", "WARN", "token authentication is disabled"))

    ledger = resolve_ledger_path(config.get("ledger"))
    ledger_ready = check_ledger_writable(ledger)
    checks.append(ledger_ready)

    checks.append(check_daemon_status(status_max_age=status_max_age))

    endpoint_ok = False
    if serve_enabled:
        endpoint_check, endpoint_ok = check_capture_endpoint(serve, timeout=timeout)
        checks.append(endpoint_check)
        if endpoint_ok and require_token and token_ready:
            checks.append(check_capture_token(serve, token, timeout=timeout))
    else:
        checks.append(DiagnosticCheck("capture endpoint", "WARN", "disabled in config"))

    if sys.platform.startswith("win"):
        if startup_task_exists():
            checks.append(DiagnosticCheck("startup task", "PASS", "rawmem-daemon is registered"))
        else:
            checks.append(
                DiagnosticCheck(
                    "startup task",
                    "WARN",
                    "not installed; run `rawmem setup --install-startup --yes`",
                )
            )
    else:
        checks.append(DiagnosticCheck("startup task", "WARN", "Windows startup task check unavailable"))

    expected_hooks = global_git_hooks_dir().resolve().as_posix()
    current_hooks = git_config_get_global("core.hooksPath")
    if current_hooks == expected_hooks:
        checks.append(DiagnosticCheck("global git hooks", "PASS", f"core.hooksPath={expected_hooks}"))
    elif current_hooks:
        checks.append(
            DiagnosticCheck(
                "global git hooks",
                "WARN",
                f"core.hooksPath points elsewhere: {current_hooks}",
            )
        )
    else:
        checks.append(
            DiagnosticCheck(
                "global git hooks",
                "WARN",
                "not installed; run `rawmem setup --install-global-git-hooks --yes`",
            )
        )

    checks.append(check_recent_events(ledger) if ledger_ready.status != "FAIL" else DiagnosticCheck(
        "recent events", "FAIL", "ledger is not writable"
    ))
    return checks


def diagnostics_exit_code(checks: list[DiagnosticCheck], *, strict: bool = False) -> int:
    if any(check.status == "FAIL" for check in checks):
        return 1
    if strict and any(check.status == "WARN" for check in checks):
        return 1
    return 0


def render_diagnostics(checks: list[DiagnosticCheck]) -> str:
    name_width = max((len(check.name) for check in checks), default=5)
    lines = [f"{'STATUS':<6}  {'CHECK':<{name_width}}  DETAIL"]
    lines.extend(
        f"{check.status:<6}  {check.name:<{name_width}}  {check.detail}" for check in checks
    )
    return "\n".join(lines)


def check_ledger_writable(ledger: Path) -> DiagnosticCheck:
    parent = ledger.parent
    if not parent.exists():
        return DiagnosticCheck("ledger", "FAIL", f"parent directory does not exist: {parent}")
    if not parent.is_dir():
        return DiagnosticCheck("ledger", "FAIL", f"parent is not a directory: {parent}")
    if ledger.exists() and not ledger.is_file():
        return DiagnosticCheck("ledger", "FAIL", f"not a regular file: {ledger}")
    probe_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".rawmem-doctor-", dir=parent, delete=False) as probe:
            probe_path = Path(probe.name)
        if ledger.exists() and not os.access(ledger, os.W_OK):
            return DiagnosticCheck("ledger", "FAIL", f"file is not writable: {ledger}")
    except OSError as exc:
        return DiagnosticCheck("ledger", "FAIL", f"cannot write beside {ledger}: {exc}")
    finally:
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError:
                pass
    state = "existing" if ledger.exists() else "ready to create"
    return DiagnosticCheck("ledger", "PASS", f"{state}: {ledger}")


def check_daemon_status(*, status_max_age: float) -> DiagnosticCheck:
    target = status_path()
    if not target.exists():
        return DiagnosticCheck("daemon status", "WARN", f"missing {target}")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        updated = parse_utc(data.get("updated_ts"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return DiagnosticCheck("daemon status", "WARN", f"unreadable {target}: {exc}")
    age = max(0.0, (datetime.now(timezone.utc) - updated).total_seconds())
    if age <= status_max_age:
        return DiagnosticCheck("daemon status", "PASS", f"updated {age:.0f}s ago (pid {data.get('pid', '?')})")
    return DiagnosticCheck(
        "daemon status",
        "WARN",
        f"stale: updated {age:.0f}s ago (expected <= {status_max_age:.0f}s)",
    )


def check_capture_endpoint(serve: dict[str, Any], *, timeout: float) -> tuple[DiagnosticCheck, bool]:
    url = capture_url(serve, "/health")
    try:
        data = request_json(url, timeout=timeout)
    except (OSError, ValueError, URLError, HTTPError) as exc:
        return DiagnosticCheck("capture endpoint", "WARN", f"unreachable at {url}: {describe_http_error(exc)}"), False
    if data.get("ok") is True:
        return DiagnosticCheck("capture endpoint", "PASS", f"healthy at {url}"), True
    return DiagnosticCheck("capture endpoint", "WARN", f"unexpected response from {url}"), False


def check_capture_token(serve: dict[str, Any], token: str, *, timeout: float) -> DiagnosticCheck:
    url = capture_url(serve, "/check")
    try:
        data = request_json(url, timeout=timeout, token=token)
    except HTTPError as exc:
        if exc.code == 401:
            return DiagnosticCheck("token handshake", "FAIL", "daemon rejected the configured browser token")
        return DiagnosticCheck("token handshake", "FAIL", f"HTTP {exc.code} from {url}")
    except (OSError, ValueError, URLError) as exc:
        return DiagnosticCheck("token handshake", "FAIL", describe_http_error(exc))
    if data.get("ok") is True and data.get("authorized") is True:
        return DiagnosticCheck("token handshake", "PASS", "daemon accepted the configured token")
    return DiagnosticCheck("token handshake", "FAIL", "daemon returned an invalid token-check response")


def check_recent_events(ledger: Path) -> DiagnosticCheck:
    if not ledger.exists():
        return DiagnosticCheck("recent events", "WARN", "ledger has not been created yet")
    try:
        events = read_recent_events(ledger, limit=5)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return DiagnosticCheck("recent events", "FAIL", f"cannot read ledger tail: {exc}")
    if not events:
        return DiagnosticCheck("recent events", "WARN", "ledger is empty")
    latest = events[-1]
    label = f"{latest.get('source', '?')}/{latest.get('event_type', '?')}"
    return DiagnosticCheck(
        "recent events",
        "PASS",
        f"sampled {len(events)}; latest {latest.get('ts', '?')} {label}",
    )


def read_recent_events(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        data = b""
        while position > 0 and data.count(b"\n") <= limit:
            size = min(8192, position)
            position -= size
            handle.seek(position)
            data = handle.read(size) + data
    lines = [line for line in data.splitlines() if line.strip()][-limit:]
    return [json.loads(line.decode("utf-8")) for line in lines]


def capture_url(serve: dict[str, Any], path: str) -> str:
    host = str(serve.get("host") or "127.0.0.1")
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    port = int(serve.get("port", 8765))
    return f"http://{host}:{port}{path}"


def request_json(url: str, *, timeout: float, token: str | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Rawmem-Token"] = token
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    return data


def parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("missing updated_ts")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def describe_http_error(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code}"
    reason = getattr(exc, "reason", None)
    return str(reason or exc)
