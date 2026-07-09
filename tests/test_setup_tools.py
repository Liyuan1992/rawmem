from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from rawmem.cli import main
from rawmem.ledger import read_events
from rawmem.setup_tools import generate_global_git_hook, install_global_git_hooks


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
