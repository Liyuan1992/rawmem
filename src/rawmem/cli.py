from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .ledger import (
    append_event,
    artifact_dir_for,
    build_event,
    file_artifact,
    init_local_store,
    parse_key_value_pairs,
    read_events,
    resolve_ledger_path,
    summarize,
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("rawmem: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"rawmem: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rawmem",
        description="Local-first raw evidence ledger.",
    )
    parser.add_argument("--version", action="version", version=f"rawmem {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)
    add_init_parser(sub)
    add_capture_parser(sub)
    add_run_parser(sub)
    add_git_snapshot_parser(sub)
    add_tail_parser(sub)
    add_path_parser(sub)
    return parser


def add_common_store_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ledger", help="Explicit JSONL ledger path.")
    parser.add_argument("--local", action="store_true", help="Use .rawmem/events.jsonl in the current directory.")


def add_init_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("init", help="Create a local .rawmem store.")
    parser.add_argument("--local", action="store_true", help="Accepted for symmetry; init always creates .rawmem.")
    parser.set_defaults(func=cmd_init)


def add_capture_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("capture", help="Append a raw evidence event.")
    add_common_store_args(parser)
    parser.add_argument("--source", default="manual", help="Event source, e.g. codex, claude, browser, git.")
    parser.add_argument("--type", dest="event_type", default="note", help="Event type, e.g. task_note, bug_fix.")
    parser.add_argument("--project", help="Project name. Defaults to git root or cwd name.")
    parser.add_argument("--cwd", help="Working directory to store in the event.")
    parser.add_argument("--summary", help="Short event summary.")
    parser.add_argument("--text", help="Raw text to store.")
    parser.add_argument("--stdin", action="store_true", help="Read raw text from stdin.")
    parser.add_argument("--tag", action="append", default=[], help="Tag. Can be repeated.")
    parser.add_argument("--artifact", action="append", default=[], help="Artifact path. Can be repeated.")
    parser.add_argument("--field", action="append", default=[], help="Payload field as KEY=VALUE. Can be repeated.")
    parser.add_argument("--privacy", default="local_only", help="Privacy scope label.")
    parser.add_argument(
        "--review-not-required",
        action="store_true",
        help="Mark the event as not requiring review before derived memory use.",
    )
    parser.set_defaults(func=cmd_capture)


def add_run_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("run", help="Run a command and append a command_run event.")
    add_common_store_args(parser)
    parser.add_argument("--source", default="terminal", help="Event source.")
    parser.add_argument("--project", help="Project name.")
    parser.add_argument("--cwd", help="Command working directory.")
    parser.add_argument("--tag", action="append", default=[], help="Tag. Can be repeated.")
    parser.add_argument("--shell", action="store_true", help="Run through the platform shell.")
    parser.add_argument("--no-save-output", action="store_true", help="Do not save full stdout/stderr artifacts.")
    parser.add_argument("command_args", nargs=argparse.REMAINDER, help="Command after --.")
    parser.set_defaults(func=cmd_run)


def add_git_snapshot_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("git-snapshot", help="Append current git state as evidence.")
    add_common_store_args(parser)
    parser.add_argument("--source", default="git", help="Event source.")
    parser.add_argument("--project", help="Project name.")
    parser.add_argument("--cwd", help="Git working directory.")
    parser.add_argument("--tag", action="append", default=[], help="Tag. Can be repeated.")
    parser.add_argument("--save-diff", action="store_true", help="Save full unstaged diff as an artifact.")
    parser.set_defaults(func=cmd_git_snapshot)


def add_tail_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("tail", help="Show recent events.")
    add_common_store_args(parser)
    parser.add_argument("--limit", type=int, default=10, help="Number of events to show.")
    parser.add_argument("--json", action="store_true", help="Print full JSON events.")
    parser.set_defaults(func=cmd_tail)


def add_path_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("path", help="Print the resolved ledger path.")
    add_common_store_args(parser)
    parser.set_defaults(func=cmd_path)


def cmd_init(args: argparse.Namespace) -> int:
    ledger_path = init_local_store()
    print(str(ledger_path))
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
    saved = append_event(ledger_path, event)
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
    saved = append_event(ledger_path, event)

    print_result(saved, ledger_path, stream=sys.stderr if result.returncode else sys.stdout)
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
    saved = append_event(ledger_path, event)
    print_result(saved, ledger_path)
    return 0


def cmd_tail(args: argparse.Namespace) -> int:
    ledger_path = resolve_ledger_path(args.ledger, local=args.local)
    events = read_events(ledger_path)
    if args.limit <= 0:
        return 0
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


def print_result(event: dict[str, Any], ledger_path: Path, *, stream: Any = sys.stdout) -> None:
    print(f"{event['event_id']} -> {ledger_path}", file=stream)
