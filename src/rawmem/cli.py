from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .archive import (
    iter_archive_events,
    list_archives,
    seal_ledger,
    verify_sealed_archive,
)
from .archive_format import assert_active_ledger
from .ledger import (
    LedgerCursor,
    append_event,
    artifact_dir_for,
    build_event,
    file_artifact,
    init_local_store,
    iter_events,
    load_cursor,
    parse_key_value_pairs,
    read_events,
    resolve_ledger_path,
    save_cursor,
    summarize,
    verify_ledger,
)
from .projection import EVENT_PROJECTIONS, project_event
from .config import (
    DEFAULT_GIT_HOOKS,
    ensure_capture_token,
    global_config_path,
    load_global_config,
    write_global_config,
)
from .daemon import read_status, run_daemon
from .diagnostics import diagnostics_exit_code, render_diagnostics, run_diagnostics
from .privacy import CapturePolicy
from .setup_tools import (
    default_powershell_profile,
    git_config_get_global,
    global_git_hooks_dir,
    install_global_git_hooks,
    install_powershell_profile,
    install_startup_task,
    remove_rawmem_home,
    setup_project,
    start_startup_task,
    startup_task_exists,
    startup_task_name,
    stop_startup_task,
    uninstall_global_git_hooks,
    uninstall_powershell_profile,
    uninstall_startup_task,
)
from .watcher import watch_loop, watch_once
from .web_capture import build_bookmarklet, event_from_adapter_payload, serve_capture


def main(argv: list[str] | None = None) -> int:
    # Windows consoles often default to a legacy codepage; ledger content is
    # UTF-8 and may be CJK, so emit UTF-8 instead of crashing or garbling.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and (stream.encoding or "").lower() not in (
            "utf-8",
            "utf8",
        ):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        report_error("rawmem: interrupted")
        return 130
    except Exception as exc:
        report_error(f"rawmem: {exc}", exc=exc)
        return 1


def report_error(message: str, *, exc: Exception | None = None) -> None:
    # Under pythonw.exe there is no console and sys.stderr is None; a crash
    # must still leave a trace somewhere findable.
    if sys.stderr is not None:
        print(message, file=sys.stderr)
        return
    try:
        import traceback

        from .ledger import default_home

        crash_log = default_home() / "cli-errors.log"
        crash_log.parent.mkdir(parents=True, exist_ok=True)
        with crash_log.open("a", encoding="utf-8") as handle:
            handle.write(f"{message}\n")
            if exc is not None:
                traceback.print_exception(exc, file=handle)
    except OSError:
        pass


def append_cli_event(ledger_path: Path, event: dict[str, Any]) -> dict[str, Any]:
    policy = CapturePolicy.from_config(load_global_config().get("privacy"))
    decision = policy.apply(event)
    if not decision.accepted or decision.event is None:
        raise ValueError(f"capture rejected by privacy policy: {decision.reason}")
    return append_event(ledger_path, decision.event)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rawmem",
        description="Local-first raw evidence ledger.",
    )
    parser.add_argument("--version", action="version", version=f"rawmem {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)
    add_init_parser(sub)
    add_setup_parser(sub)
    add_uninstall_parser(sub)
    add_config_parser(sub)
    add_doctor_parser(sub)
    add_capture_parser(sub)
    add_ingest_parser(sub)
    add_clip_parser(sub)
    add_run_parser(sub)
    add_git_snapshot_parser(sub)
    add_watch_parser(sub)
    add_daemon_parser(sub)
    add_sync_parser(sub)
    add_serve_parser(sub)
    add_bookmarklet_parser(sub)
    add_verify_parser(sub)
    add_export_parser(sub)
    add_seal_parser(sub)
    add_rotate_parser(sub)
    add_archives_parser(sub)
    add_tail_parser(sub)
    add_path_parser(sub)
    return parser


def add_common_store_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ledger", help="Explicit JSONL ledger path.")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use .rawmem/events.jsonl in the current directory.",
    )


def add_query_store_args(parser: argparse.ArgumentParser) -> None:
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--ledger", help="Explicit active JSONL ledger path.")
    target.add_argument(
        "--local",
        action="store_true",
        help="Use the current directory's active ledger.",
    )
    target.add_argument(
        "--archive",
        help="Explicit sealed archive JSONL path. Archives are never searched implicitly.",
    )


def add_init_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("init", help="Create a local .rawmem store.")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Accepted for symmetry; init always creates .rawmem.",
    )
    parser.set_defaults(func=cmd_init)


def add_setup_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "setup", help="One-time local setup for broader background capture."
    )
    parser.add_argument(
        "--project-root", default=".", help="Project root to configure."
    )
    parser.add_argument(
        "--all", action="store_true", help="Enable config, scripts, and git hooks."
    )
    parser.add_argument(
        "--install-git-hooks", action="store_true", help="Install repo-local git hooks."
    )
    parser.add_argument(
        "--global",
        dest="global_setup",
        action="store_true",
        help="Write the global daemon config (~/.rawmem/config.json) and global git hooks. Requires --yes.",
    )
    parser.add_argument(
        "--install-global-git-hooks",
        action="store_true",
        help="Install ~/.rawmem/git-hooks and set git core.hooksPath for all repositories. Requires --yes.",
    )
    parser.add_argument(
        "--include-clipboard",
        action="store_true",
        help="Enable clipboard polling in the global daemon config. Off by default for privacy.",
    )
    parser.add_argument(
        "--disable-clipboard",
        action="store_true",
        help="Disable clipboard polling in the global daemon config without rewriting other settings.",
    )
    parser.add_argument(
        "--uninstall-global-git-hooks",
        action="store_true",
        help="Unset git core.hooksPath if it points at the rawmem hooks directory.",
    )
    parser.add_argument(
        "--install-startup",
        action="store_true",
        help="Register the rawmem daemon to start at logon (Windows scheduled task).",
    )
    parser.add_argument(
        "--uninstall-startup",
        action="store_true",
        help="Remove the rawmem daemon logon task.",
    )
    parser.add_argument(
        "--start-daemon",
        action="store_true",
        help="Start the registered daemon task right now.",
    )
    parser.add_argument(
        "--install-powershell-profile",
        action="store_true",
        help="Install the shell capture snippet into the current user's PowerShell profile.",
    )
    parser.add_argument(
        "--profile-path",
        help="PowerShell profile path. Defaults to CurrentUser profile.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite generated config/scripts blocks.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm user-profile writes for non-interactive setup.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned changes without writing anything.",
    )
    parser.set_defaults(func=cmd_setup)


def add_uninstall_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = sub.add_parser(
        "uninstall", help="Disable machine integrations while preserving captured data."
    )
    parser.add_argument(
        "--remove-home",
        action="store_true",
        help="Also delete ~/.rawmem, including the ledger. Requires --yes.",
    )
    parser.add_argument(
        "--profile-path",
        help="PowerShell profile path. Defaults to CurrentUser profile.",
    )
    parser.add_argument(
        "--yes", action="store_true", help="Confirm deletion of ~/.rawmem."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned changes without writing anything.",
    )
    parser.set_defaults(func=cmd_uninstall)


def add_config_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "config", help="Manage the global daemon config without installing hooks."
    )
    parser.add_argument(
        "--init", action="store_true", help="Create or refresh ~/.rawmem/config.json."
    )
    parser.add_argument(
        "--include-clipboard",
        action="store_true",
        help="Enable clipboard polling in the global daemon config.",
    )
    parser.add_argument(
        "--disable-clipboard",
        action="store_true",
        help="Disable clipboard polling in the global daemon config.",
    )
    parser.add_argument(
        "--show-browser-token",
        action="store_true",
        help="Print the browser capture token for the extension options page.",
    )
    parser.add_argument(
        "--rotate-browser-token",
        action="store_true",
        help="Rotate the browser capture token and print the new value.",
    )
    parser.add_argument(
        "--path", action="store_true", help="Print the global config path."
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the effective global config as JSON."
    )
    parser.set_defaults(func=cmd_config)


def add_doctor_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "doctor",
        help="Check config, storage, daemon, browser capture, and integrations.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return nonzero for warnings as well as failures.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable diagnostic results."
    )
    parser.add_argument(
        "--timeout", type=float, default=1.5, help="Local endpoint timeout in seconds."
    )
    parser.add_argument(
        "--status-max-age",
        type=float,
        default=30.0,
        help="Maximum healthy daemon status age in seconds.",
    )
    parser.set_defaults(func=cmd_doctor)


def add_capture_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = sub.add_parser("capture", help="Append a raw evidence event.")
    add_common_store_args(parser)
    parser.add_argument(
        "--source",
        default="manual",
        help="Event source, e.g. codex, claude, browser, git.",
    )
    parser.add_argument(
        "--type",
        dest="event_type",
        default="note",
        help="Event type, e.g. task_note, bug_fix.",
    )
    parser.add_argument(
        "--project", help="Project name. Defaults to git root or cwd name."
    )
    parser.add_argument("--cwd", help="Working directory to store in the event.")
    parser.add_argument("--summary", help="Short event summary.")
    parser.add_argument("--text", help="Raw text to store.")
    parser.add_argument(
        "--stdin", action="store_true", help="Read raw text from stdin."
    )
    parser.add_argument(
        "--tag", action="append", default=[], help="Tag. Can be repeated."
    )
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="Artifact path. Can be repeated.",
    )
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        help="Payload field as KEY=VALUE. Can be repeated.",
    )
    parser.add_argument("--privacy", default="local_only", help="Privacy scope label.")
    parser.add_argument(
        "--review-not-required",
        action="store_true",
        help="Mark the event as not requiring review before derived memory use.",
    )
    parser.set_defaults(func=cmd_capture)


def add_ingest_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("ingest", help="Append one or more adapter JSON events.")
    add_common_store_args(parser)
    parser.add_argument(
        "--file", help="JSON file to ingest. Use stdin when omitted or with --stdin."
    )
    parser.add_argument("--stdin", action="store_true", help="Read JSON from stdin.")
    parser.add_argument("--cwd", help="Working directory to store in the event.")
    parser.set_defaults(func=cmd_ingest)


def add_clip_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("clip", help="Capture clipboard/stdin text as a raw event.")
    add_common_store_args(parser)
    parser.add_argument("--source", default="clipboard", help="Event source.")
    parser.add_argument(
        "--type", dest="event_type", default="clipboard_clip", help="Event type."
    )
    parser.add_argument("--project", help="Project name.")
    parser.add_argument("--cwd", help="Working directory to store in the event.")
    parser.add_argument(
        "--text", help="Text to capture instead of reading stdin/clipboard."
    )
    parser.add_argument("--stdin", action="store_true", help="Read text from stdin.")
    parser.add_argument("--url", help="Optional source URL.")
    parser.add_argument("--title", help="Optional source title.")
    parser.add_argument(
        "--tag", action="append", default=[], help="Tag. Can be repeated."
    )
    parser.set_defaults(func=cmd_clip)


def add_run_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("run", help="Run a command and append a command_run event.")
    add_common_store_args(parser)
    parser.add_argument("--source", default="terminal", help="Event source.")
    parser.add_argument("--project", help="Project name.")
    parser.add_argument("--cwd", help="Command working directory.")
    parser.add_argument(
        "--tag", action="append", default=[], help="Tag. Can be repeated."
    )
    parser.add_argument(
        "--shell", action="store_true", help="Run through the platform shell."
    )
    parser.add_argument(
        "--no-save-output",
        action="store_true",
        help="Do not save full stdout/stderr artifacts.",
    )
    parser.add_argument(
        "command_args", nargs=argparse.REMAINDER, help="Command after --."
    )
    parser.set_defaults(func=cmd_run)


def add_git_snapshot_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = sub.add_parser(
        "git-snapshot", help="Append current git state as evidence."
    )
    add_common_store_args(parser)
    parser.add_argument("--source", default="git", help="Event source.")
    parser.add_argument("--project", help="Project name.")
    parser.add_argument("--cwd", help="Git working directory.")
    parser.add_argument(
        "--tag", action="append", default=[], help="Tag. Can be repeated."
    )
    parser.add_argument(
        "--save-diff",
        action="store_true",
        help="Save full unstaged diff as an artifact.",
    )
    parser.set_defaults(func=cmd_git_snapshot)


def add_watch_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "watch", help="Poll a project tree and capture file-change batches."
    )
    add_common_store_args(parser)
    parser.add_argument("--root", default=".", help="Root directory to watch.")
    parser.add_argument("--project", help="Project name.")
    parser.add_argument(
        "--interval", type=float, default=5.0, help="Polling interval in seconds."
    )
    parser.add_argument("--once", action="store_true", help="Scan once and exit.")
    parser.add_argument("--state", help="Watch state JSON path.")
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="Extra ignore glob. Can be repeated.",
    )
    parser.set_defaults(func=cmd_watch)


def add_daemon_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "daemon",
        help="Run all background capture surfaces (tailers, clipboard, watch, serve) in one process.",
    )
    parser.add_argument(
        "--once", action="store_true", help="Run one capture pass and exit."
    )
    parser.add_argument(
        "--status", action="store_true", help="Show daemon status and exit."
    )
    parser.add_argument(
        "--no-serve",
        action="store_true",
        help="Disable the localhost capture endpoint.",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="On first run, ingest existing history instead of starting from the end of files.",
    )
    parser.add_argument("--log-file", help="Append daemon logs to this file.")
    parser.set_defaults(func=cmd_daemon)


def add_sync_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "sync", help="Run the passive tailers once (no server, no loop)."
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="On first run, ingest existing history instead of starting from the end of files.",
    )
    parser.set_defaults(func=cmd_sync)


def add_serve_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "serve", help="Run a localhost capture endpoint for browser/tool adapters."
    )
    add_common_store_args(parser)
    parser.add_argument("--host", default="127.0.0.1", help="Listen host.")
    parser.add_argument("--port", type=int, default=8765, help="Listen port.")
    parser.add_argument("--cwd", help="Working directory to store in events.")
    parser.add_argument(
        "--token",
        help="Browser capture token. Defaults to daemon.serve.token in global config.",
    )
    parser.add_argument(
        "--no-token",
        action="store_true",
        help="Disable token checks for this foreground server.",
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        help="Allowed CORS origin or prefix. Can be repeated.",
    )
    parser.set_defaults(func=cmd_serve)


def add_bookmarklet_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = sub.add_parser(
        "bookmarklet", help="Print a browser bookmarklet for selected-text capture."
    )
    parser.add_argument(
        "--endpoint", default="http://127.0.0.1:8765/capture", help="Capture endpoint."
    )
    parser.add_argument(
        "--token",
        help="Browser capture token. Defaults to daemon.serve.token in global config.",
    )
    parser.add_argument(
        "--no-token",
        action="store_true",
        help="Generate a bookmarklet without an auth header.",
    )
    parser.set_defaults(func=cmd_bookmarklet)


def add_verify_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "verify", help="Verify JSON events and the complete ledger hash chain."
    )
    add_query_store_args(parser)
    parser.add_argument(
        "--json", action="store_true", help="Print the versioned verification payload."
    )
    parser.set_defaults(func=cmd_verify)


def add_export_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "export", help="Incrementally export events using rawmem.cursor.v1."
    )
    add_query_store_args(parser)
    cursor = parser.add_mutually_exclusive_group()
    cursor.add_argument(
        "--cursor-file", help="Read and atomically update a cursor JSON file."
    )
    cursor.add_argument("--after-cursor", help="Inline rawmem.cursor.v1 JSON object.")
    parser.add_argument(
        "--limit", type=int, help="Maximum matching events in this batch."
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=8 * 1024 * 1024,
        help="Maximum bytes scanned per batch.",
    )
    parser.add_argument(
        "--source", action="append", default=[], help="Allowed source. Can be repeated."
    )
    parser.add_argument(
        "--type",
        dest="event_types",
        action="append",
        default=[],
        help="Allowed event type. Can be repeated.",
    )
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        help="Allowed project. Can be repeated.",
    )
    parser.add_argument("--output", help="Optional JSONL file for exported events.")
    parser.add_argument(
        "--events-only",
        action="store_true",
        help="Print event JSONL instead of the batch envelope.",
    )
    parser.add_argument(
        "--projection",
        choices=EVENT_PROJECTIONS,
        help="Event fields to return. Explicit archive queries default to metadata.",
    )
    parser.set_defaults(func=cmd_export)


def add_seal_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "seal",
        help="Atomically seal the active ledger as a read-only archive and start a linked ledger.",
    )
    add_common_store_args(parser)
    parser.add_argument(
        "--destination", help="Explicit archive JSONL path (same filesystem required)."
    )
    parser.add_argument(
        "--yes", action="store_true", help="Confirm the sealed-archive transition."
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the versioned seal result."
    )
    parser.set_defaults(func=cmd_seal)


def add_rotate_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("rotate", help="Compatibility alias for `seal`.")
    add_common_store_args(parser)
    parser.add_argument("--destination", help="Explicit archive JSONL path.")
    parser.add_argument("--yes", action="store_true", help="Confirm the rotation.")
    parser.set_defaults(func=cmd_rotate)


def add_archives_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = sub.add_parser(
        "archives", help="List the derived, metadata-only archive registry."
    )
    add_common_store_args(parser)
    parser.add_argument(
        "--json", action="store_true", help="Print the versioned archive registry."
    )
    parser.set_defaults(func=cmd_archives)


def add_tail_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("tail", help="Show recent events.")
    add_query_store_args(parser)
    parser.add_argument(
        "--limit", type=int, default=10, help="Number of events to show."
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON events.")
    parser.add_argument("--source", help="Only show events from this source.")
    parser.add_argument(
        "--type", dest="event_type", help="Only show events of this type."
    )
    parser.add_argument("--project", help="Only show events for this project.")
    parser.add_argument(
        "--projection",
        choices=EVENT_PROJECTIONS,
        help="Event fields to return. Explicit archive queries default to metadata.",
    )
    parser.set_defaults(func=cmd_tail)


def add_path_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("path", help="Print the resolved ledger path.")
    add_common_store_args(parser)
    parser.set_defaults(func=cmd_path)


def cmd_init(args: argparse.Namespace) -> int:
    ledger_path = init_local_store()
    print(str(ledger_path))
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    if args.include_clipboard and args.disable_clipboard:
        raise ValueError(
            "--include-clipboard and --disable-clipboard cannot be used together"
        )
    if args.dry_run:
        for action in plan_setup_actions(args):
            print(f"dry_run: {action}")
        return 0

    actions: list[str] = []
    clipboard_requested = args.include_clipboard or args.disable_clipboard
    global_requested = (
        args.global_setup
        or args.install_global_git_hooks
        or args.uninstall_global_git_hooks
        or args.install_startup
        or args.uninstall_startup
        or args.start_daemon
    )
    if clipboard_requested and not args.global_setup:
        actions.append(
            "global_config="
            f"{write_global_config(include_clipboard=args.include_clipboard, disable_clipboard=args.disable_clipboard)}"
        )
        if not (args.all or args.install_git_hooks or args.install_powershell_profile):
            for action in actions:
                print(action)
            return 0
    if args.global_setup:
        if not args.yes:
            raise ValueError(
                "--global writes machine-wide rawmem config and Git hooks; pass --yes to confirm"
            )
        actions.append(
            "global_config="
            f"{write_global_config(force=args.force, include_clipboard=args.include_clipboard, disable_clipboard=args.disable_clipboard)}"
        )
    if args.install_global_git_hooks or args.global_setup:
        if not args.yes:
            raise ValueError(
                "--install-global-git-hooks changes Git behavior for all repositories; pass --yes to confirm"
            )
        actions.extend(install_global_git_hooks(force=args.force))
    if args.uninstall_global_git_hooks:
        actions.extend(uninstall_global_git_hooks())
    if args.install_startup:
        if not args.yes:
            raise ValueError(
                "--install-startup registers a logon task; pass --yes to confirm"
            )
        actions.append(install_startup_task())
    if args.uninstall_startup:
        actions.append(uninstall_startup_task())
    if args.start_daemon:
        actions.append(start_startup_task())
    if global_requested and not (
        args.all or args.install_git_hooks or args.install_powershell_profile
    ):
        for action in actions:
            print(action)
        return 0

    root = Path(args.project_root).resolve()
    install_hooks = args.install_git_hooks or args.all
    actions += setup_project(
        root,
        install_git_hooks=install_hooks,
        write_scripts=True,
        force=args.force,
    )
    if args.install_powershell_profile:
        if not args.yes:
            raise ValueError(
                "--install-powershell-profile modifies a user profile; pass --yes to confirm"
            )
        profile = (
            Path(args.profile_path).expanduser()
            if args.profile_path
            else default_powershell_profile()
        )
        installed = install_powershell_profile(profile, force=args.force)
        actions.append(f"powershell_profile={installed}")
    for action in actions:
        print(action)
    return 0


def plan_setup_actions(args: argparse.Namespace) -> list[str]:
    actions: list[str] = []
    task_name = startup_task_name()
    clipboard_requested = args.include_clipboard or args.disable_clipboard
    global_requested = (
        args.global_setup
        or args.install_global_git_hooks
        or args.uninstall_global_git_hooks
        or args.install_startup
        or args.uninstall_startup
        or args.start_daemon
    )
    config_path = global_config_path()
    if clipboard_requested and not args.global_setup:
        state = "enabled" if args.include_clipboard else "disabled"
        actions.append(f"update global config {config_path}: clipboard={state}")
        if not (args.all or args.install_git_hooks or args.install_powershell_profile):
            return actions
    if args.global_setup:
        actions.append(f"write global config {config_path}")
    if args.install_global_git_hooks or args.global_setup:
        hooks_dir = global_git_hooks_dir().resolve()
        desired = hooks_dir.as_posix()
        current = git_config_get_global("core.hooksPath")
        if current and current != desired and not args.force:
            raise ValueError(
                f"core.hooksPath is already set to '{current}'; rerun with --force to replace it"
            )
        actions.append(
            f"write global hook runner {hooks_dir / 'rawmem_git_hook_runner.py'}"
        )
        actions.extend(
            f"write global git hook {hooks_dir / hook}" for hook in DEFAULT_GIT_HOOKS
        )
        actions.append(f"set git core.hooksPath={desired}")
    if args.uninstall_global_git_hooks:
        expected = global_git_hooks_dir().resolve().as_posix()
        current = git_config_get_global("core.hooksPath")
        if current == expected:
            actions.append("unset git core.hooksPath")
        else:
            actions.append(
                f"skip git core.hooksPath: current value is '{current or ''}'"
            )
    if args.install_startup:
        actions.append(f"register Windows startup task {task_name}")
    if args.uninstall_startup:
        state = "remove" if startup_task_exists() else "skip missing"
        actions.append(f"{state} Windows startup task {task_name}")
    if args.start_daemon:
        actions.append(f"start Windows startup task {task_name}")
    if global_requested and not (
        args.all or args.install_git_hooks or args.install_powershell_profile
    ):
        return actions

    root = Path(args.project_root).resolve()
    rawmem_dir = root / ".rawmem"
    actions.append(f"create local store {rawmem_dir / 'events.jsonl'}")
    config = rawmem_dir / "config.json"
    if args.force or not config.exists():
        actions.append(f"write project config {config}")
    script_dir = rawmem_dir / "scripts"
    actions.extend(
        [
            f"write PowerShell snippet {script_dir / 'rawmem-powershell-profile.ps1'}",
            f"write watch script {script_dir / 'start-watch.ps1'}",
            f"write browser bookmarklet {script_dir / 'browser-bookmarklet.txt'}",
        ]
    )
    if args.install_git_hooks or args.all:
        git_dir = root / ".git"
        if not git_dir.exists():
            raise ValueError(f"Not a git repository: {root}")
        actions.append(
            f"write repo hook runner {git_dir / 'hooks' / 'rawmem_git_hook_runner.py'}"
        )
        actions.extend(
            f"write repo git hook {git_dir / 'hooks' / hook}"
            for hook in DEFAULT_GIT_HOOKS
        )
    if args.install_powershell_profile:
        profile = (
            Path(args.profile_path).expanduser()
            if args.profile_path
            else default_powershell_profile()
        )
        actions.append(f"update PowerShell profile {profile}")
    return actions


def cmd_uninstall(args: argparse.Namespace) -> int:
    if args.remove_home and not (args.yes or args.dry_run):
        raise ValueError("--remove-home deletes the ledger and requires --yes")
    profile = (
        Path(args.profile_path).expanduser()
        if args.profile_path
        else default_powershell_profile()
    )
    home = global_config_path().parent
    if args.dry_run:
        task_name = startup_task_name()
        task_state = "stop and remove" if startup_task_exists() else "skip missing"
        actions = [
            f"{task_state} Windows startup task {task_name}",
            f"unset git core.hooksPath only if it points to {global_git_hooks_dir().resolve().as_posix()}",
            f"remove rawmem block from PowerShell profile {profile}",
            f"{'delete' if args.remove_home else 'preserve'} rawmem home {home}",
        ]
        for action in actions:
            print(f"dry_run: {action}")
        return 0

    actions = [stop_startup_task(), uninstall_startup_task()]
    actions.extend(uninstall_global_git_hooks())
    actions.append(uninstall_powershell_profile(profile))
    if args.remove_home:
        actions.append(remove_rawmem_home(home))
    else:
        actions.append(f"preserved_rawmem_home={home}")
    for action in actions:
        print(action)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    if args.timeout <= 0:
        raise ValueError("--timeout must be greater than zero")
    if args.status_max_age <= 0:
        raise ValueError("--status-max-age must be greater than zero")
    checks = run_diagnostics(timeout=args.timeout, status_max_age=args.status_max_age)
    if args.json:
        print(
            json.dumps(
                [check.as_dict() for check in checks], ensure_ascii=False, indent=2
            )
        )
    else:
        print(render_diagnostics(checks))
    return diagnostics_exit_code(checks, strict=args.strict)


def cmd_config(args: argparse.Namespace) -> int:
    if args.include_clipboard and args.disable_clipboard:
        raise ValueError(
            "--include-clipboard and --disable-clipboard cannot be used together"
        )
    needs_write = (
        args.init
        or args.include_clipboard
        or args.disable_clipboard
        or args.rotate_browser_token
        or args.show_browser_token
    )
    path = global_config_path()
    if needs_write:
        write_global_config(
            include_clipboard=args.include_clipboard,
            disable_clipboard=args.disable_clipboard,
            rotate_browser_token=args.rotate_browser_token,
        )
    if args.path:
        print(path)
    config = load_global_config(path)
    if args.show_browser_token or args.rotate_browser_token:
        print(ensure_capture_token(config))
    if args.json:
        print(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True))
    if not (
        args.path or args.show_browser_token or args.rotate_browser_token or args.json
    ):
        print(f"global_config={path}")
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve() if args.cwd else Path.cwd()
    ledger_path = resolve_ledger_path(args.ledger, local=args.local, cwd=cwd)
    raw_text = args.text or ""
    if args.stdin:
        stdin_text = sys.stdin.read()
        raw_text = f"{raw_text}\n{stdin_text}".strip() if raw_text else stdin_text
    payload = parse_key_value_pairs(args.field)
    artifacts = [file_artifact(path) for path in args.artifact]
    event = build_event(
        source=args.source,
        event_type=args.event_type,
        project=args.project,
        cwd=cwd,
        summary=args.summary,
        raw_text=raw_text,
        tags=args.tag,
        artifacts=artifacts,
        payload=payload,
        privacy_scope=args.privacy,
        review_required=not args.review_not_required,
    )
    saved = append_cli_event(ledger_path, event)
    print_result(saved, ledger_path)
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve() if args.cwd else Path.cwd()
    ledger_path = resolve_ledger_path(args.ledger, local=args.local, cwd=cwd)
    if args.stdin or not args.file:
        raw = sys.stdin.read()
    else:
        raw = Path(args.file).read_text(encoding="utf-8")
    payload = json.loads(raw)
    payloads = payload if isinstance(payload, list) else [payload]
    count = 0
    last_saved: dict[str, Any] | None = None
    for item in payloads:
        event = event_from_adapter_payload(item, cwd=cwd)
        last_saved = append_cli_event(ledger_path, event)
        count += 1
    if last_saved:
        print(f"{count} event(s) -> {ledger_path}; last={last_saved['event_id']}")
    else:
        print(f"0 event(s) -> {ledger_path}")
    return 0


def cmd_clip(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve() if args.cwd else Path.cwd()
    ledger_path = resolve_ledger_path(args.ledger, local=args.local, cwd=cwd)
    text = args.text
    if text is None and (args.stdin or not sys.stdin.isatty()):
        text = sys.stdin.read()
    if text is None:
        text = read_clipboard()
    payload = {}
    if args.url:
        payload["url"] = args.url
    if args.title:
        payload["title"] = args.title
    event = build_event(
        source=args.source,
        event_type=args.event_type,
        project=args.project,
        cwd=cwd,
        summary=args.title or summarize(text),
        raw_text=text or "",
        tags=args.tag,
        payload=payload,
    )
    saved = append_cli_event(ledger_path, event)
    print_result(saved, ledger_path)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    command_args = normalize_remainder(args.command_args)
    if not command_args:
        raise ValueError("run requires a command after --")
    cwd = Path(args.cwd).resolve() if args.cwd else Path.cwd()
    ledger_path = resolve_ledger_path(args.ledger, local=args.local, cwd=cwd)
    start = time.perf_counter()
    result = subprocess.run(
        command_args if not args.shell else " ".join(command_args),
        cwd=cwd,
        shell=args.shell,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    duration_ms = int((time.perf_counter() - start) * 1000)

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    payload = {
        "command": command_args,
        "shell": args.shell,
        "exit_code": result.returncode,
        "duration_ms": duration_ms,
        "stdout_tail": tail_text(result.stdout),
        "stderr_tail": tail_text(result.stderr),
    }
    raw_text = f"$ {' '.join(command_args)}\nexit_code={result.returncode}"
    event = build_event(
        source=args.source,
        event_type="command_run",
        project=args.project,
        cwd=cwd,
        summary=summarize(raw_text),
        raw_text=raw_text,
        tags=args.tag,
        payload=payload,
    )
    if not args.no_save_output:
        output_dir = artifact_dir_for(ledger_path, event["event_id"])
        output_dir.mkdir(parents=True, exist_ok=True)
        if result.stdout:
            stdout_path = output_dir / "stdout.txt"
            stdout_path.write_text(result.stdout, encoding="utf-8")
            event["artifacts"].append(file_artifact(stdout_path, kind="stdout"))
        if result.stderr:
            stderr_path = output_dir / "stderr.txt"
            stderr_path.write_text(result.stderr, encoding="utf-8")
            event["artifacts"].append(file_artifact(stderr_path, kind="stderr"))
    saved = append_cli_event(ledger_path, event)

    print_result(
        saved, ledger_path, stream=sys.stderr if result.returncode else sys.stdout
    )
    return result.returncode


def cmd_git_snapshot(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve() if args.cwd else Path.cwd()
    ledger_path = resolve_ledger_path(args.ledger, local=args.local, cwd=cwd)
    root = git(["rev-parse", "--show-toplevel"], cwd).strip()
    branch = git(["branch", "--show-current"], cwd).strip()
    head = git_optional(["rev-parse", "HEAD"], cwd, default="unborn").strip()
    status = git(["status", "--short", "--branch"], cwd)
    name_status = git(["diff", "--name-status"], cwd)
    stat = git(["diff", "--stat"], cwd)
    cached_stat = git(["diff", "--cached", "--stat"], cwd)
    payload = {
        "git_root": root,
        "branch": branch,
        "head": head,
        "status_short": status,
        "diff_name_status": name_status,
        "diff_stat": stat,
        "cached_diff_stat": cached_stat,
    }
    raw_text = "\n".join(
        [
            f"git_root={root}",
            f"branch={branch}",
            f"head={head}",
            "",
            status,
            name_status,
            stat,
        ]
    ).strip()
    event = build_event(
        source=args.source,
        event_type="git_snapshot",
        project=args.project,
        cwd=root or cwd,
        summary=f"Git snapshot for {Path(root).name if root else cwd.name} on {branch or 'detached'}",
        raw_text=raw_text,
        tags=args.tag,
        payload=payload,
    )
    if args.save_diff:
        event_dir = artifact_dir_for(ledger_path, event["event_id"])
        event_dir.mkdir(parents=True, exist_ok=True)
        diff_path = event_dir / "diff.patch"
        diff_path.write_text(git(["diff"], cwd), encoding="utf-8")
        event["artifacts"].append(file_artifact(diff_path, kind="git_diff"))
    saved = append_cli_event(ledger_path, event)
    print_result(saved, ledger_path)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    policy = CapturePolicy.from_config(load_global_config().get("privacy"))
    if args.once:
        saved = watch_once(
            root=root,
            ledger_path=args.ledger,
            local=args.local,
            project=args.project,
            state_path=args.state,
            ignore_globs=None if not args.ignore else args.ignore,
            event_policy=lambda event: policy.apply(event).event,
        )
        if saved:
            print_result(
                saved, resolve_ledger_path(args.ledger, local=args.local, cwd=root)
            )
        else:
            print("no changes")
        return 0
    watch_loop(
        root=root,
        ledger_path=args.ledger,
        local=args.local,
        project=args.project,
        interval_seconds=args.interval,
        state_path=args.state,
        ignore_globs=None if not args.ignore else args.ignore,
        event_policy=lambda event: policy.apply(event).event,
    )
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    if args.status:
        status = read_status()
        if status is None:
            print("no daemon status found; is the daemon running?")
            return 1
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    return run_daemon(
        once=args.once,
        serve=not args.no_serve,
        backfill=args.backfill,
        log_file=args.log_file,
    )


def cmd_sync(args: argparse.Namespace) -> int:
    return run_daemon(once=True, serve=False, backfill=args.backfill)


def cmd_serve(args: argparse.Namespace) -> int:
    config = load_global_config()
    policy = CapturePolicy.from_config(config.get("privacy"))
    serve_cfg = (config.get("daemon") or {}).get("serve") or {}
    require_token = not args.no_token
    token = args.token or serve_cfg.get("token")
    if require_token and (not isinstance(token, str) or not token.strip()):
        raise ValueError(
            "capture token missing; run `rawmem config --init` or pass --token/--no-token"
        )
    serve_capture(
        host=args.host,
        port=args.port,
        ledger_path=args.ledger,
        local=args.local,
        cwd=args.cwd,
        token=token if isinstance(token, str) else None,
        require_token=require_token,
        allowed_origins=args.allow_origin or serve_cfg.get("allowed_origins"),
        event_policy=lambda event: policy.apply(event).event,
    )
    return 0


def cmd_bookmarklet(args: argparse.Namespace) -> int:
    token = None
    if not args.no_token:
        config = load_global_config()
        serve_cfg = (config.get("daemon") or {}).get("serve") or {}
        token = args.token or serve_cfg.get("token")
    print(
        build_bookmarklet(
            args.endpoint, token=token if isinstance(token, str) else None
        )
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    ledger_path, archive_query = resolve_query_target(args)
    if archive_query:
        status = verify_sealed_archive(ledger_path)
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
        elif status["valid"]:
            print(
                f"verified sealed archive: {status['accepted_breakpoint_count']} recorded breakpoint(s), "
                f"ledger_id={status['ledger_id']}"
            )
        else:
            print(f"FAILED sealed archive verification: {ledger_path}")
        return 0 if status["valid"] else 1

    assert_active_ledger(ledger_path)
    result = verify_ledger(ledger_path)
    if args.json:
        print(
            json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        )
    elif result.valid:
        print(
            f"verified {result.event_count} event(s), {result.byte_size} bytes, "
            f"ledger_id={result.ledger_id}, last={result.last_event_id or '-'}"
        )
    else:
        print(f"FAILED: {len(result.errors)} error(s) in {ledger_path}")
        for error in result.errors:
            print(
                f"line {error.get('line', '?')}: {error.get('code')}: {error.get('message')}"
            )
    return 0 if result.valid else 1


def cmd_export(args: argparse.Namespace) -> int:
    ledger_path, archive_query = resolve_query_target(args)
    cursor: LedgerCursor | dict[str, Any] | None = None
    if args.cursor_file:
        cursor = load_cursor(args.cursor_file)
    elif args.after_cursor:
        value = json.loads(args.after_cursor)
        if not isinstance(value, dict):
            raise ValueError("--after-cursor must be a JSON object")
        cursor = value
    projection = args.projection or ("metadata" if archive_query else "full")
    reader = iter_archive_events if archive_query else iter_events
    batch = reader(
        ledger_path,
        after_cursor=cursor,
        sources=args.source or None,
        event_types=args.event_types or None,
        projects=args.project or None,
        limit=args.limit,
        max_bytes=args.max_bytes,
        projection=projection,
    )
    if (
        args.cursor_file
        and batch.cursor_status == "ok"
        and batch.chain_status != "failed"
    ):
        save_cursor(args.cursor_file, batch.next_cursor)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8", newline="\n") as handle:
            for event in batch.events:
                handle.write(
                    json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
                )
    if args.events_only:
        for warning in batch.integrity_warnings:
            report_error(
                "rawmem: archive integrity warning: "
                f"{warning.get('code')} at byte {warning.get('byte_offset')} (recorded)"
            )
        for event in batch.events:
            print(json.dumps(event, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(batch.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if batch.cursor_status == "ok" and batch.chain_status != "failed" else 1


def cmd_seal(args: argparse.Namespace) -> int:
    if not args.yes:
        raise ValueError("ledger sealing requires --yes")
    ledger_path = resolve_ledger_path(args.ledger, local=args.local)
    result = seal_ledger(ledger_path, destination=args.destination)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            f"sealed {result['archived_bytes']} bytes -> {result['archived_ledger']}; "
            f"breakpoints={result['breakpoint_count']}; active={result['new_ledger']}"
        )
    return 0


def cmd_rotate(args: argparse.Namespace) -> int:
    if not args.yes:
        raise ValueError("ledger rotation requires --yes")
    ledger_path = resolve_ledger_path(args.ledger, local=args.local)
    result = seal_ledger(ledger_path, destination=args.destination)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_archives(args: argparse.Namespace) -> int:
    ledger_path = resolve_ledger_path(args.ledger, local=args.local)
    registry = list_archives(ledger_path)
    if args.json:
        print(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for item in registry["archives"]:
            print(
                f"{item.get('sealed_at')} {item.get('archive_id')} "
                f"events={item.get('event_count')} breaks={item.get('breakpoint_count')} "
                f"{item.get('archive_path')}"
            )
    return 0


def cmd_tail(args: argparse.Namespace) -> int:
    if args.limit <= 0:
        return 0
    ledger_path, archive_query = resolve_query_target(args)
    projection = args.projection or ("metadata" if archive_query else "full")
    integrity_warnings: list[dict[str, Any]] = []
    if archive_query:
        events, integrity_warnings = read_archive_tail(
            ledger_path,
            limit=args.limit,
            source=args.source,
            event_type=args.event_type,
            project=args.project,
            projection=projection,
        )
    else:
        events = read_events(ledger_path)
        if args.source:
            events = [event for event in events if event.get("source") == args.source]
        if args.event_type:
            events = [
                event for event in events if event.get("event_type") == args.event_type
            ]
        if args.project:
            events = [event for event in events if event.get("project") == args.project]
        events = [project_event(event, projection) for event in events[-args.limit :]]
    for warning in integrity_warnings:
        report_error(
            "rawmem: archive integrity warning: "
            f"{warning.get('code')} at byte {warning.get('byte_offset')} (recorded)"
        )
    for event in events[-args.limit :]:
        if args.json:
            print(json.dumps(event, ensure_ascii=False, sort_keys=True))
        else:
            summary = event.get("summary") or ""
            print(
                f"{event.get('ts')} {event.get('event_id')} "
                f"{event.get('source')}/{event.get('event_type')} "
                f"{event.get('project')}: {summary}"
            )
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    print(resolve_ledger_path(args.ledger, local=args.local))
    return 0


def resolve_query_target(args: argparse.Namespace) -> tuple[Path, bool]:
    archive = getattr(args, "archive", None)
    if archive:
        return Path(archive).expanduser(), True
    return resolve_ledger_path(args.ledger, local=args.local), False


def read_archive_tail(
    path: Path,
    *,
    limit: int,
    source: str | None,
    event_type: str | None,
    project: str | None,
    projection: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cursor: LedgerCursor | None = None
    selected: list[dict[str, Any]] = []
    integrity_warnings: list[dict[str, Any]] = []
    while True:
        batch = iter_archive_events(
            path,
            after_cursor=cursor,
            sources=[source] if source else None,
            event_types=[event_type] if event_type else None,
            projects=[project] if project else None,
            max_bytes=8 * 1024 * 1024,
            projection=projection,
        )
        if batch.cursor_status != "ok" or batch.chain_status == "failed":
            raise ValueError(
                "sealed archive query failed: " + "; ".join(batch.warnings)
            )
        selected.extend(batch.events)
        if len(selected) > limit:
            selected = selected[-limit:]
        integrity_warnings.extend(batch.integrity_warnings)
        if batch.next_cursor.byte_offset >= batch.ledger_size:
            break
        if cursor is not None and batch.next_cursor.byte_offset <= cursor.byte_offset:
            raise ValueError("sealed archive query made no progress")
        cursor = batch.next_cursor
    return selected, integrity_warnings


def normalize_remainder(items: list[str]) -> list[str]:
    if items and items[0] == "--":
        return items[1:]
    return items


def tail_text(text: str, *, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def read_clipboard() -> str:
    if sys.platform.startswith("win"):
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode == 0:
            return result.stdout
    raise ValueError(
        "No text provided and clipboard capture is not available on this platform"
    )


def git_optional(args: list[str], cwd: Path, *, default: str = "") -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return default
    return result.stdout


def print_result(
    event: dict[str, Any], ledger_path: Path, *, stream: Any = sys.stdout
) -> None:
    print(f"{event['event_id']} -> {ledger_path}", file=stream)
