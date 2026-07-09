from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rawmem.setup_tools import generate_global_git_hook, install_global_git_hooks


class GlobalGitHookTests(unittest.TestCase):
    def test_hook_snapshots_then_chains_to_repo_hook(self) -> None:
        script = generate_global_git_hook("pre-push")
        self.assertTrue(script.startswith("#!/bin/sh"))
        self.assertIn("git-snapshot", script)
        self.assertIn("--tag 'pre-push'", script)
        self.assertIn('exec "$repo_hooks/pre-push" "$@"', script)
        self.assertIn("</dev/null", script)  # must not eat pre-push stdin
        self.assertIn("RAWMEM_DISABLE", script)

    def test_install_writes_executable_hooks_without_touching_git_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hooks_dir = Path(tmp) / "git-hooks"
            actions = install_global_git_hooks(
                ["post-commit", "pre-push"], hooks_dir=hooks_dir, configure_git=False
            )
            self.assertEqual(len(actions), 2)
            self.assertTrue((hooks_dir / "post-commit").exists())
            content = (hooks_dir / "pre-push").read_text(encoding="utf-8")
            self.assertIn("rawmem global git hook: pre-push", content)


if __name__ == "__main__":
    unittest.main()
