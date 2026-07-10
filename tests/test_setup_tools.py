from __future__ import annotations

import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from rawmem.cli import main
from rawmem.config import load_global_config
from rawmem.ledger import read_events
from rawmem.setup_tools import (
    PROFILE_BEGIN,
    PROFILE_END,
    generate_global_git_hook,
    install_global_git_hooks,
    remove_rawmem_home,
    uninstall_powershell_profile,
)


class GlobalGitHookTests(unittest.TestCase):
    def test_hook_snapshots_then_chains_to_repo_hook(self) -> None:
        script = generate_global_git_hook("pre-push")
        self.assertTrue(script.startswith("#!/bin/sh"))
        self.assertIn("git-snapshot", script)
        self.assertIn("--tag 'pre-push'", script)
        self.assertIn('exec "$git_dir/hooks/pre-push" "$@"', script)
        self.assertIn("</dev/null", script)  # must not eat pre-push stdin
        self.assertIn("RAWMEM_DISABLE", script)
        self.assertIn("rawmem_git_hook_runner.py", script)
        # --git-path hooks resolves through core.hooksPath back to this very
        # directory and caused infinite self-exec; it must never come back.
        self.assertNotIn("--git-path", script)
        self.assertIn("RAWMEM_HOOK_GUARD", script)

    def test_install_writes_executable_hooks_without_touching_git_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hooks_dir = Path(tmp) / "git-hooks"
            actions = install_global_git_hooks(
                ["post-commit", "pre-push"], hooks_dir=hooks_dir, configure_git=False
            )
            self.assertEqual(len(actions), 3)
            self.assertTrue((hooks_dir / "rawmem_git_hook_runner.py").exists())
            self.assertTrue((hooks_dir / "post-commit").exists())
            content = (hooks_dir / "pre-push").read_text(encoding="utf-8")
            self.assertIn("rawmem global git hook: pre-push", content)

    def test_global_setup_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_home = os.environ.get("RAWMEM_HOME")
            old_git_config = os.environ.get("GIT_CONFIG_GLOBAL")
            os.environ["RAWMEM_HOME"] = str(Path(tmp) / "rawmem-home")
            os.environ["GIT_CONFIG_GLOBAL"] = str(Path(tmp) / "gitconfig")
            try:
                self.assertEqual(main(["setup", "--global"]), 1)
            finally:
                restore_env("RAWMEM_HOME", old_home)
                restore_env("GIT_CONFIG_GLOBAL", old_git_config)

    def test_setup_clipboard_toggle_does_not_install_global_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rawmem_home = base / "rawmem-home"
            git_config = base / "gitconfig"
            old_home = os.environ.get("RAWMEM_HOME")
            old_git_config = os.environ.get("GIT_CONFIG_GLOBAL")
            os.environ["RAWMEM_HOME"] = str(rawmem_home)
            os.environ["GIT_CONFIG_GLOBAL"] = str(git_config)
            try:
                self.assertEqual(main(["setup", "--disable-clipboard"]), 0)
                config = load_global_config(rawmem_home / "config.json")
                self.assertFalse(config["daemon"]["tailers"]["clipboard"]["enabled"])
                self.assertIsInstance(config["daemon"]["serve"]["token"], str)
                self.assertFalse(git_config.exists())
                self.assertFalse((rawmem_home / "git-hooks").exists())
            finally:
                restore_env("RAWMEM_HOME", old_home)
                restore_env("GIT_CONFIG_GLOBAL", old_git_config)

    def test_global_setup_dry_run_does_not_write_or_require_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rawmem_home = base / "rawmem-home"
            git_config = base / "gitconfig"
            old_home = os.environ.get("RAWMEM_HOME")
            old_git_config = os.environ.get("GIT_CONFIG_GLOBAL")
            os.environ["RAWMEM_HOME"] = str(rawmem_home)
            os.environ["GIT_CONFIG_GLOBAL"] = str(git_config)
            try:
                out = io.StringIO()
                with redirect_stdout(out):
                    code = main(["setup", "--global", "--install-startup", "--dry-run"])
                self.assertEqual(code, 0)
                self.assertIn("dry_run: write global config", out.getvalue())
                self.assertIn("register Windows startup task", out.getvalue())
                self.assertFalse(rawmem_home.exists())
                self.assertFalse(git_config.exists())
            finally:
                restore_env("RAWMEM_HOME", old_home)
                restore_env("GIT_CONFIG_GLOBAL", old_git_config)

    def test_project_setup_dry_run_does_not_create_local_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["setup", "--project-root", tmp, "--dry-run"])
            self.assertEqual(code, 0)
            self.assertIn("create local store", out.getvalue())
            self.assertFalse((Path(tmp) / ".rawmem").exists())

    def test_uninstall_dry_run_preserves_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rawmem_home = Path(tmp) / "rawmem-home"
            rawmem_home.mkdir()
            old_home = os.environ.get("RAWMEM_HOME")
            os.environ["RAWMEM_HOME"] = str(rawmem_home)
            try:
                out = io.StringIO()
                with redirect_stdout(out):
                    code = main(["uninstall", "--dry-run"])
                self.assertEqual(code, 0)
                self.assertIn(f"preserve rawmem home {rawmem_home}", out.getvalue())
                self.assertTrue(rawmem_home.exists())
            finally:
                restore_env("RAWMEM_HOME", old_home)

    def test_uninstall_command_preserves_home_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rawmem_home = Path(tmp) / "rawmem-home"
            rawmem_home.mkdir()
            (rawmem_home / "events.jsonl").write_text("private", encoding="utf-8")
            old_home = os.environ.get("RAWMEM_HOME")
            os.environ["RAWMEM_HOME"] = str(rawmem_home)
            try:
                with (
                    mock.patch("rawmem.cli.stop_startup_task", return_value="startup stopped"),
                    mock.patch("rawmem.cli.uninstall_startup_task", return_value="startup removed"),
                    mock.patch("rawmem.cli.uninstall_global_git_hooks", return_value=["hooks removed"]),
                    mock.patch("rawmem.cli.uninstall_powershell_profile", return_value="profile cleaned"),
                ):
                    self.assertEqual(main(["uninstall"]), 0)
                self.assertTrue(rawmem_home.exists())
                self.assertTrue((rawmem_home / "events.jsonl").exists())
            finally:
                restore_env("RAWMEM_HOME", old_home)

    def test_uninstall_command_can_remove_home_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rawmem_home = Path(tmp) / "rawmem-home"
            rawmem_home.mkdir()
            old_home = os.environ.get("RAWMEM_HOME")
            os.environ["RAWMEM_HOME"] = str(rawmem_home)
            try:
                with (
                    mock.patch("rawmem.cli.stop_startup_task", return_value="startup stopped"),
                    mock.patch("rawmem.cli.uninstall_startup_task", return_value="startup removed"),
                    mock.patch("rawmem.cli.uninstall_global_git_hooks", return_value=["hooks removed"]),
                    mock.patch("rawmem.cli.uninstall_powershell_profile", return_value="profile cleaned"),
                ):
                    self.assertEqual(main(["uninstall", "--remove-home", "--yes"]), 0)
                self.assertFalse(rawmem_home.exists())
            finally:
                restore_env("RAWMEM_HOME", old_home)

    def test_uninstall_remove_home_requires_confirmation(self) -> None:
        self.assertEqual(main(["uninstall", "--remove-home"]), 1)

    def test_remove_rawmem_home_deletes_only_requested_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rawmem_home = base / "rawmem-home"
            rawmem_home.mkdir()
            (rawmem_home / "events.jsonl").write_text("private", encoding="utf-8")
            sibling = base / "keep.txt"
            sibling.write_text("keep", encoding="utf-8")
            action = remove_rawmem_home(rawmem_home)
            self.assertIn("rawmem_home_removed=", action)
            self.assertFalse(rawmem_home.exists())
            self.assertTrue(sibling.exists())

    def test_uninstall_powershell_profile_removes_only_rawmem_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "profile.ps1"
            profile.write_text(
                f"Write-Host before\n{PROFILE_BEGIN}\nrawmem code\n{PROFILE_END}\nWrite-Host after\n",
                encoding="utf-8",
            )
            action = uninstall_powershell_profile(profile)
            content = profile.read_text(encoding="utf-8")
            self.assertIn("powershell_profile_block_removed=", action)
            self.assertIn("Write-Host before", content)
            self.assertIn("Write-Host after", content)
            self.assertNotIn(PROFILE_BEGIN, content)

    def test_config_command_can_rotate_browser_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rawmem_home = Path(tmp) / "rawmem-home"
            old_home = os.environ.get("RAWMEM_HOME")
            os.environ["RAWMEM_HOME"] = str(rawmem_home)
            try:
                self.assertEqual(main(["config", "--init"]), 0)
                first = load_global_config(rawmem_home / "config.json")["daemon"]["serve"]["token"]
                self.assertEqual(main(["config", "--rotate-browser-token"]), 0)
                second = load_global_config(rawmem_home / "config.json")["daemon"]["serve"]["token"]
                self.assertNotEqual(first, second)
            finally:
                restore_env("RAWMEM_HOME", old_home)

    def test_global_git_hook_records_real_commit_and_chains_repo_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rawmem_home = base / "rawmem-home"
            repo = base / "repo"
            git_config = base / "gitconfig"
            old_home = os.environ.get("RAWMEM_HOME")
            old_git_config = os.environ.get("GIT_CONFIG_GLOBAL")
            os.environ["RAWMEM_HOME"] = str(rawmem_home)
            os.environ["GIT_CONFIG_GLOBAL"] = str(git_config)
            try:
                self.assertEqual(main(["setup", "--global", "--yes"]), 0)
                subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
                marker = base / "local-hook-ran.txt"
                local_hook = repo / ".git" / "hooks" / "post-commit"
                local_hook.write_text(
                    "#!/bin/sh\n"
                    f"echo local > '{marker.as_posix()}'\n",
                    encoding="utf-8",
                    newline="\n",
                )
                try:
                    local_hook.chmod(local_hook.stat().st_mode | 0o111)
                except OSError:
                    pass
                (repo / "a.txt").write_text("hello", encoding="utf-8")
                subprocess.run(
                    ["git", "-C", str(repo), "add", "a.txt"],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(repo),
                        "-c",
                        "user.name=rawmem-test",
                        "-c",
                        "user.email=rawmem@example.test",
                        "commit",
                        "-m",
                        "smoke",
                    ],
                    check=True,
                    capture_output=True,
                )
                events = read_events(rawmem_home / "events.jsonl")
                self.assertTrue(marker.exists())
                self.assertTrue(
                    any(
                        event["source"] == "git-hook"
                        and event["event_type"] == "git_snapshot"
                        and "post-commit" in event["tags"]
                        for event in events
                    )
                )
            finally:
                restore_env("RAWMEM_HOME", old_home)
                restore_env("GIT_CONFIG_GLOBAL", old_git_config)


def restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
