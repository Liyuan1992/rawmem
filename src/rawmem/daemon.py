"""Single resident process that runs every background capture surface.

One daemon = capture endpoint (serve) + AI-session/shell-history tailers +
clipboard poller + optional file watchers. Each surface is a periodic task
with its own interval inside one scheduler loop, so "background capture"
needs exactly one process and one autostart entry.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TextIO

from .clipboard import ClipboardTailer
from .config import load_global_config
from .ledger import append_event, build_event, default_home, resolve_ledger_path, utc_now_iso
from .tailers import TailState, build_tailers_from_config
from .watcher import watch_once
from .web_capture import create_capture_server


@dataclass
class PeriodicTask:
    name: str
    interval_seconds: float
    run: Callable[[], int]
    next_due: float = 0.0
    runs: int = 0
    events: int = 0
    errors: int = 0
    last_error: str | None = None
    last_run_ts: str | None = None

    def tick(self, now: float) -> None:
        if now < self.next_due:
            return
        self.next_due = now + self.interval_seconds
        self.runs += 1
        self.last_run_ts = utc_now_iso()
        try:
            self.events += self.run()
        except Exception as exc:  # noqa: BLE001 - one surface must not kill the daemon
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"


def status_path() -> Path:
    return default_home() / "daemon-status.json"


def tail_state_path() -> Path:
    return default_home() / "tailer-state.json"


def build_tasks(
    config: dict[str, Any],
    *,
    ledger: Path,
    state: TailState,
    backfill: bool = False,
) -> list[PeriodicTask]:
    daemon_cfg = config.get("daemon") or {}
    tailer_cfg = daemon_cfg.get("tailers") or {}
    tasks: list[PeriodicTask] = []

    def make_tailer_task(tailer: Any, interval: float) -> PeriodicTask:
        def run() -> int:
            events = tailer.poll(state)
            for event in events:
                append_event(ledger, event)
            state.save()
            return len(events)

        return PeriodicTask(name=tailer.name, interval_seconds=interval, run=run)

    for tailer in build_tailers_from_config(tailer_cfg, backfill=backfill):
        section = tailer_cfg.get(tailer.name.replace("-", "_")) or {}
        interval = float(section.get("interval_seconds", 20))
        tasks.append(make_tailer_task(tailer, interval))

    clip_cfg = tailer_cfg.get("clipboard") or {}
    if clip_cfg.get("enabled", True):
        clip_tailer = ClipboardTailer(max_chars=int(clip_cfg.get("max_chars", 20000)))
        tasks.append(make_tailer_task(clip_tailer, float(clip_cfg.get("interval_seconds", 4))))

    watch_cfg = daemon_cfg.get("watch") or {}
    if watch_cfg.get("enabled", False):
        interval = float(watch_cfg.get("interval_seconds", 120))
        ignore_globs = watch_cfg.get("ignore_globs")
        for root in watch_cfg.get("roots") or []:
            root_path = Path(root).expanduser()
            if not root_path.is_dir():
                continue
            digest = hashlib.sha256(str(root_path.resolve()).lower().encode("utf-8")).hexdigest()[:12]
            watch_state = default_home() / f"watch-state-{digest}.json"

            def run(root_path: Path = root_path, watch_state: Path = watch_state) -> int:
                saved = watch_once(
                    root=root_path,
                    ledger_path=ledger,
                    state_path=watch_state,
                    ignore_globs=ignore_globs,
                )
                return 1 if saved else 0

            tasks.append(
                PeriodicTask(name=f"watch:{root_path.name}", interval_seconds=interval, run=run)
            )

    return tasks


def write_status(
    tasks: list[PeriodicTask],
    *,
    ledger: Path,
    serve_info: dict[str, Any] | None,
    started_ts: str,
) -> None:
    data = {
        "schema": "rawmem.daemon_status.v1",
        "pid": os.getpid(),
        "started_ts": started_ts,
        "updated_ts": utc_now_iso(),
        "ledger": str(ledger),
        "serve": serve_info,
        "tasks": [
            {
                "name": task.name,
                "interval_seconds": task.interval_seconds,
                "runs": task.runs,
                "events": task.events,
                "errors": task.errors,
                "last_error": task.last_error,
                "last_run_ts": task.last_run_ts,
            }
            for task in tasks
        ],
    }
    target = status_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def open_log(log_file: str | Path | None) -> TextIO:
    # Under pythonw.exe there is no console; stdout is None.
    if log_file:
        path = Path(log_file).expanduser()
    elif sys.stdout is None:
        path = default_home() / "daemon.log"
    else:
        return sys.stdout
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a", encoding="utf-8")


def run_daemon(
    *,
    once: bool = False,
    serve: bool = True,
    backfill: bool = False,
    log_file: str | Path | None = None,
    config: dict[str, Any] | None = None,
) -> int:
    cfg = config or load_global_config()
    ledger = resolve_ledger_path(cfg.get("ledger"))
    state = TailState(tail_state_path())
    log = open_log(log_file)

    def emit(message: str) -> None:
        print(f"{utc_now_iso()} {message}", file=log, flush=True)

    daemon_cfg = cfg.get("daemon") or {}
    serve_cfg = daemon_cfg.get("serve") or {}
    server = None
    serve_info: dict[str, Any] | None = None
    if serve and serve_cfg.get("enabled", True) and not once:
        host = serve_cfg.get("host", "127.0.0.1")
        port = int(serve_cfg.get("port", 8765))
        try:
            server = create_capture_server(host=host, port=port, ledger_path=ledger)
        except OSError as exc:
            emit(f"daemon: port {port} unavailable ({exc}); another daemon is likely running")
            return 1
        thread = threading.Thread(target=server.serve_forever, name="rawmem-serve", daemon=True)
        thread.start()
        serve_info = {"host": host, "port": port}
        emit(f"daemon: capture endpoint on http://{host}:{port}")

    tasks = build_tasks(cfg, ledger=ledger, state=state, backfill=backfill)
    if not tasks:
        emit("daemon: no capture tasks enabled; check ~/.rawmem/config.json")
        return 1
    started_ts = utc_now_iso()
    emit(f"daemon: {len(tasks)} tasks -> {ledger}")

    if not once:
        append_event(
            ledger,
            build_event(
                source="rawmem",
                event_type="daemon_start",
                project="rawmem",
                cwd=Path.home(),
                summary=f"rawmem daemon started with {len(tasks)} tasks",
                raw_text="\n".join(task.name for task in tasks),
                tags=["daemon"],
                payload={"tasks": [task.name for task in tasks], "serve": serve_info},
            ),
        )

    cycle = float(daemon_cfg.get("cycle_seconds", 1.0))
    try:
        if once:
            total = 0
            for task in tasks:
                task.tick(time.monotonic())
                if task.last_error:
                    emit(f"daemon: task {task.name} error: {task.last_error}")
                total += task.events
            state.save()
            write_status(tasks, ledger=ledger, serve_info=serve_info, started_ts=started_ts)
            emit(f"daemon: single pass captured {total} event(s)")
            return 0
        last_status = 0.0
        reported_errors: dict[str, str] = {}
        while True:
            now = time.monotonic()
            for task in tasks:
                task.tick(now)
                if task.last_error and reported_errors.get(task.name) != task.last_error:
                    reported_errors[task.name] = task.last_error
                    emit(f"daemon: task {task.name} error: {task.last_error}")
            if now - last_status >= 10:
                last_status = now
                write_status(tasks, ledger=ledger, serve_info=serve_info, started_ts=started_ts)
            time.sleep(cycle)
    finally:
        if server is not None:
            server.shutdown()
        state.save()
        write_status(tasks, ledger=ledger, serve_info=serve_info, started_ts=started_ts)


def read_status() -> dict[str, Any] | None:
    target = status_path()
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
