from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from .config import DEFAULT_GIT_HOOKS, config_path_for, default_config, save_config
from .ledger import default_home, init_local_store
from .web_capture import build_bookmarklet


PROFILE_BEGIN = "# >>> rawmem shell capture >>>"
PROFILE_END = "# <<< rawmem shell capture <<<"
HOOK_BEGIN = "# >>> rawmem git hook >>>"
HOOK_END = "# <<< rawmem git hook <<<"


def global_git_hooks_dir() -> Path:
    return default_home() / "git-hooks"


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
    runner_path = hooks_dir / "rawmem_git_hook_runner.py"
    runner_path.write_text(generate_python_runner(), encoding="utf-8")
    installed: list[Path] = []
    for hook_name in hooks:
        target = hooks_dir / hook_name
        block = generate_git_hook_block(hook_name, runner_path=runner_path)
        upsert_marked_block(target, block, begin=HOOK_BEGIN, end=HOOK_END, executable=True)
        installed.append(target)
    return installed


def generate_python_runner() -> str:
    package_parent = Path(__file__).resolve().parents[1]
    return "\n".join(
        [
            "import sys",
            "",
            f"sys.path.insert(0, {str(package_parent)!r})",
            "",
            "from rawmem.cli import main",
            "",
            "raise SystemExit(main(sys.argv[1:]))",
            "",
        ]
    )


def generate_git_hook_block(hook_name: str, *, runner_path: str | Path | None = None) -> str:
    python_path = Path(sys.executable).as_posix()
    runner = Path(runner_path).as_posix() if runner_path else "rawmem_git_hook_runner.py"
    return "\n".join(
        [
            "#!/bin/sh",
            HOOK_BEGIN,
            f"RAWMEM_HOOK_NAME='{hook_name}'",
            "RAWMEM_ROOT=\"$(git rev-parse --show-toplevel 2>/dev/null)\"",
            "[ -z \"$RAWMEM_ROOT\" ] && exit 0",
            "cd \"$RAWMEM_ROOT\" || exit 0",
            f"RAWMEM_PYTHON={sh_quote(python_path)}",
            f"RAWMEM_RUNNER={sh_quote(runner)}",
            "\"$RAWMEM_PYTHON\" \"$RAWMEM_RUNNER\" git-snapshot --local --source git-hook --tag \"$RAWMEM_HOOK_NAME\" </dev/null >/dev/null 2>&1 || true",
            HOOK_END,
            "",
        ]
    )


def generate_global_git_hook(hook_name: str, *, runner_path: str | Path | None = None) -> str:
    """Global hook: snapshot to the global ledger, then chain to repo hooks.

    The chain target must come from `--git-dir`, NOT `--git-path hooks`:
    the latter resolves through core.hooksPath and would return this very
    directory, making the hook exec itself forever. A guard env var stops
    any other accidental re-entry.
    """
    python_path = Path(sys.executable).as_posix()
    runner = Path(runner_path).as_posix() if runner_path else "rawmem_git_hook_runner.py"
    return "\n".join(
        [
            "#!/bin/sh",
            f"# rawmem global git hook: {hook_name}",
            "[ \"$RAWMEM_HOOK_GUARD\" = \"1\" ] && exit 0",
            "RAWMEM_HOOK_GUARD=1",
            "export RAWMEM_HOOK_GUARD",
            "if [ \"$RAWMEM_DISABLE\" != \"1\" ]; then",
            f"  {sh_quote(python_path)} {sh_quote(runner)} git-snapshot"
            f" --source git-hook --tag {sh_quote(hook_name)} </dev/null >/dev/null 2>&1 || true",
            "fi",
            "git_dir=\"$(git rev-parse --git-dir 2>/dev/null)\"",
            f"if [ -n \"$git_dir\" ] && [ -x \"$git_dir/hooks/{hook_name}\" ]; then",
            f"  exec \"$git_dir/hooks/{hook_name}\" \"$@\"",
            "fi",
            "exit 0",
            "",
        ]
    )


def install_global_git_hooks(
    hooks: Iterable[str] = DEFAULT_GIT_HOOKS,
    *,
    hooks_dir: str | Path | None = None,
    configure_git: bool = True,
    force: bool = False,
) -> list[str]:
    """Write hooks into ~/.rawmem/git-hooks and point core.hooksPath at it.

    Existing repo-local hooks keep working: each global hook chains to the
    repository's own .git/hooks/<name> when present. Repos that set a local
    core.hooksPath (e.g. husky) override the global value and are unaffected.
    """
    target_dir = Path(hooks_dir) if hooks_dir else global_git_hooks_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    runner_path = target_dir / "rawmem_git_hook_runner.py"
    runner_path.write_text(generate_python_runner(), encoding="utf-8")
    actions: list[str] = []
    actions.append(f"global_git_hook_runner={runner_path}")
    for hook_name in hooks:
        hook_path = target_dir / hook_name
        hook_path.write_text(
            generate_global_git_hook(hook_name, runner_path=runner_path),
            encoding="utf-8",
            newline="\n",
        )
        try:
            hook_path.chmod(hook_path.stat().st_mode | 0o111)
        except OSError:
            pass
        actions.append(f"global_git_hook={hook_path}")
    if configure_git:
        desired = target_dir.resolve().as_posix()
        current = git_config_get_global("core.hooksPath")
        if current and current != desired and not force:
            raise ValueError(
                f"core.hooksPath is already set to '{current}'; rerun with --force to replace it"
            )
        run_command(["git", "config", "--global", "core.hooksPath", desired])
        actions.append(f"git_config_core.hooksPath={desired}")
    return actions


def uninstall_global_git_hooks() -> list[str]:
    current = git_config_get_global("core.hooksPath")
    expected = global_git_hooks_dir().resolve().as_posix()
    if current != expected:
        return [f"skipped: core.hooksPath is '{current or ''}', not '{expected}'"]
    run_command(["git", "config", "--global", "--unset", "core.hooksPath"])
    return ["git_config_core.hooksPath=unset"]


def git_config_get_global(key: str) -> str | None:
    result = subprocess.run(
        ["git", "config", "--global", "--get", key],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def run_command(args: list[str]) -> str:
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"{' '.join(args)} failed")
    return result.stdout


STARTUP_TASK_NAME = "rawmem-daemon"
STARTUP_TASK_ENV = "RAWMEM_STARTUP_TASK_NAME"


def startup_task_name(task_name: str | None = None) -> str:
    return task_name or os.environ.get(STARTUP_TASK_ENV) or STARTUP_TASK_NAME


def startup_task_exists(*, task_name: str | None = None) -> bool:
    if not sys.platform.startswith("win"):
        return False
    name = startup_task_name(task_name)
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.returncode == 0


def generate_daemon_launcher() -> str:
    """Launcher with the package path baked in.

    Scheduled tasks may start pythonw without the user site-packages on
    sys.path (so `-m rawmem` fails with "No module named rawmem"); an
    explicit sys.path entry makes startup independent of that environment.
    """
    package_parent = Path(__file__).resolve().parents[1]
    return "\n".join(
        [
            "import sys",
            "",
            f"sys.path.insert(0, {str(package_parent)!r})",
            "",
            "from rawmem.cli import main",
            "",
            'sys.exit(main(["daemon"]))',
            "",
        ]
    )


def install_startup_task(*, task_name: str | None = None) -> str:
    """Register the daemon to start at logon via a hidden pythonw process."""
    if not sys.platform.startswith("win"):
        raise ValueError("startup registration is currently Windows-only")
    name = startup_task_name(task_name)
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else exe
    launcher = default_home() / "daemon-launcher.pyw"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(generate_daemon_launcher(), encoding="utf-8")
    command = f'"{runner}" "{launcher}"'
    run_command(
        [
            "schtasks",
            "/Create",
            "/F",
            "/SC",
            "ONLOGON",
            "/TN",
            name,
            "/TR",
            command,
        ]
    )
    return f"startup_task={name} -> {command}"


def uninstall_startup_task(*, task_name: str | None = None) -> str:
    if not sys.platform.startswith("win"):
        return "skipped: startup task removal is Windows-only"
    name = startup_task_name(task_name)
    if not startup_task_exists(task_name=name):
        return f"skipped: startup task {name} not found"
    run_command(["schtasks", "/Delete", "/F", "/TN", name])
    return f"startup_task_removed={name}"


def stop_startup_task(*, task_name: str | None = None) -> str:
    if not sys.platform.startswith("win"):
        return "skipped: startup task stop is Windows-only"
    name = startup_task_name(task_name)
    if not startup_task_exists(task_name=name):
        return f"skipped: startup task {name} not found"
    result = subprocess.run(
        ["schtasks", "/End", "/TN", name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return f"skipped: startup task {name} was not running"
    return f"startup_task_stopped={name}"


def start_startup_task(*, task_name: str | None = None) -> str:
    name = startup_task_name(task_name)
    run_command(["schtasks", "/Run", "/TN", name])
    return f"startup_task_started={name}"


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


def uninstall_powershell_profile(profile_path: str | Path) -> str:
    path = Path(profile_path).expanduser()
    if not path.exists():
        return f"skipped: PowerShell profile not found: {path}"
    if not remove_marked_block(path, begin=PROFILE_BEGIN, end=PROFILE_END):
        return f"skipped: rawmem block not found in PowerShell profile: {path}"
    return f"powershell_profile_block_removed={path}"


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


def remove_marked_block(path: str | Path, *, begin: str, end: str) -> bool:
    target = Path(path)
    existing = target.read_text(encoding="utf-8", errors="replace")
    if begin not in existing or end not in existing:
        return False
    prefix, remainder = existing.split(begin, 1)
    _, suffix = remainder.split(end, 1)
    content = "\n\n".join(part.strip("\r\n") for part in (prefix, suffix) if part.strip())
    target.write_text(content + ("\n" if content else ""), encoding="utf-8")
    return True


def remove_rawmem_home(path: str | Path | None = None) -> str:
    target = (Path(path) if path else default_home()).expanduser().resolve()
    home = Path.home().resolve()
    anchor = Path(target.anchor).resolve()
    if target in {home, anchor} or len(target.parts) < 3:
        raise ValueError(f"refusing to remove unsafe rawmem home path: {target}")
    if not target.exists():
        return f"skipped: rawmem home not found: {target}"
    if not target.is_dir():
        raise ValueError(f"rawmem home is not a directory: {target}")
    shutil.rmtree(target)
    return f"rawmem_home_removed={target}"


def default_powershell_profile() -> Path:
    user_home = Path.home()
    return user_home / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1"


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
