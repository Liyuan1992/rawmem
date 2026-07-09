from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

from .config import DEFAULT_GIT_HOOKS, config_path_for, default_config, save_config
from .ledger import init_local_store
from .web_capture import build_bookmarklet


PROFILE_BEGIN = "# >>> rawmem shell capture >>>"
PROFILE_END = "# <<< rawmem shell capture <<<"
HOOK_BEGIN = "# >>> rawmem git hook >>>"
HOOK_END = "# <<< rawmem git hook <<<"


def setup_project(
    project_root: str | Path,
    *,
    local: bool = True,
    install_git_hooks: bool = False,
    write_scripts: bool = True,
    force: bool = False,
) -> list[str]:
    root = Path(project_root).resolve()
    ledger_path = init_local_store(root)
    actions = [f"local_store={ledger_path}"]

    cfg_path = config_path_for(root)
    if force or not cfg_path.exists():
        save_config(cfg_path, default_config(root, local=local))
        actions.append(f"config={cfg_path}")

    if write_scripts:
        script_dir = root / ".rawmem" / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        profile_script = script_dir / "rawmem-powershell-profile.ps1"
        profile_script.write_text(generate_powershell_profile_snippet(), encoding="utf-8")
        actions.append(f"powershell_profile_snippet={profile_script}")

        watch_script = script_dir / "start-watch.ps1"
        watch_script.write_text(generate_watch_script(root), encoding="utf-8")
        actions.append(f"watch_script={watch_script}")

        bookmarklet_path = script_dir / "browser-bookmarklet.txt"
        bookmarklet_path.write_text(build_bookmarklet(), encoding="utf-8")
        actions.append(f"browser_bookmarklet={bookmarklet_path}")

    if install_git_hooks:
        installed = install_hooks(root, DEFAULT_GIT_HOOKS)
        actions.extend(f"git_hook={path}" for path in installed)

    return actions


def install_hooks(project_root: str | Path, hooks: Iterable[str]) -> list[Path]:
    root = Path(project_root).resolve()
    git_dir = root / ".git"
    if not git_dir.exists():
        raise ValueError(f"Not a git repository: {root}")
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    installed: list[Path] = []
    for hook_name in hooks:
        target = hooks_dir / hook_name
        block = generate_git_hook_block(hook_name)
        upsert_marked_block(target, block, begin=HOOK_BEGIN, end=HOOK_END, executable=True)
        installed.append(target)
    return installed


def generate_git_hook_block(hook_name: str) -> str:
    python_path = Path(sys.executable).as_posix()
    src_path = Path(__file__).resolve().parents[1].as_posix()
    return "\n".join(
        [
            "#!/bin/sh",
            HOOK_BEGIN,
            f"RAWMEM_HOOK_NAME='{hook_name}'",
            "RAWMEM_ROOT=\"$(git rev-parse --show-toplevel 2>/dev/null)\"",
            "[ -z \"$RAWMEM_ROOT\" ] && exit 0",
            "cd \"$RAWMEM_ROOT\" || exit 0",
            f"RAWMEM_PYTHON='{python_path}'",
            f"RAWMEM_SRC='{src_path}'",
            "PYTHONPATH=\"$RAWMEM_SRC${PYTHONPATH:+:$PYTHONPATH}\" \"$RAWMEM_PYTHON\" -m rawmem git-snapshot --local --source git-hook --tag \"$RAWMEM_HOOK_NAME\" >/dev/null 2>&1 || true",
            HOOK_END,
            "",
        ]
    )


def generate_powershell_profile_snippet() -> str:
    python_path = str(Path(sys.executable))
    src_path = str(Path(__file__).resolve().parents[1])
    return "\n".join(
        [
            PROFILE_BEGIN,
            "$script:RawmemLastHistoryId = $script:RawmemLastHistoryId -as [int]",
            "$script:RawmemOriginalPrompt = if (Test-Path Function:\\prompt) { (Get-Command prompt).ScriptBlock } else { { \"PS $($executionContext.SessionState.Path.CurrentLocation)> \" } }",
            f"$script:RawmemPython = {powershell_quote(python_path)}",
            f"$script:RawmemSrc = {powershell_quote(src_path)}",
            "function global:prompt {",
            "  try {",
            "    if ($env:RAWMEM_DISABLE -ne '1') {",
            "      $history = Get-History -Count 1 -ErrorAction SilentlyContinue",
            "      if ($history -and $history.Id -ne $script:RawmemLastHistoryId) {",
            "        $script:RawmemLastHistoryId = $history.Id",
            "        $cmdText = [string]$history.CommandLine",
            "        if ($cmdText -and $cmdText -notmatch 'rawmem') {",
            "          $payload = @{",
            "            source = 'powershell'",
            "            event_type = 'shell_command'",
            "            raw_text = $cmdText",
            "            summary = $cmdText",
            "            tags = @('shell')",
            "            payload = @{ shell = 'powershell'; exit_code = $global:LASTEXITCODE; cwd = (Get-Location).Path }",
            "          } | ConvertTo-Json -Depth 6 -Compress",
            "          $oldPythonPath = $env:PYTHONPATH",
            "          $env:PYTHONPATH = if ($oldPythonPath) { \"$script:RawmemSrc;$oldPythonPath\" } else { $script:RawmemSrc }",
            "          $payload | & $script:RawmemPython -m rawmem ingest --stdin 2>$null | Out-Null",
            "          $env:PYTHONPATH = $oldPythonPath",
            "        }",
            "      }",
            "    }",
            "  } catch {}",
            "  & $script:RawmemOriginalPrompt",
            "}",
            PROFILE_END,
            "",
        ]
    )


def generate_watch_script(project_root: str | Path) -> str:
    python_path = str(Path(sys.executable))
    src_path = str(Path(__file__).resolve().parents[1])
    root = str(Path(project_root).resolve())
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$env:PYTHONPATH = {powershell_quote(src_path)}",
            f"Set-Location {powershell_quote(root)}",
            f"& {powershell_quote(python_path)} -m rawmem watch --local --project {powershell_quote(Path(root).name)}",
            "",
        ]
    )


def install_powershell_profile(profile_path: str | Path, *, force: bool = False) -> Path:
    path = Path(profile_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force and PROFILE_BEGIN in path.read_text(encoding="utf-8", errors="replace"):
        return path
    upsert_marked_block(path, generate_powershell_profile_snippet(), begin=PROFILE_BEGIN, end=PROFILE_END)
    return path


def upsert_marked_block(
    path: str | Path,
    block: str,
    *,
    begin: str,
    end: str,
    executable: bool = False,
) -> None:
    target = Path(path)
    existing = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
    if begin in existing and end in existing:
        prefix = existing.split(begin, 1)[0].rstrip()
        suffix = existing.split(end, 1)[1].lstrip()
        content = "\n\n".join(part for part in [prefix, block.strip(), suffix] if part)
    else:
        content = "\n\n".join(part for part in [existing.rstrip(), block.strip()] if part)
    target.write_text(content + "\n", encoding="utf-8")
    if executable:
        try:
            target.chmod(target.stat().st_mode | 0o111)
        except OSError:
            pass


def default_powershell_profile() -> Path:
    user_home = Path.home()
    return user_home / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1"


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
