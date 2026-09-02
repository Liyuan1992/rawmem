"""Release, MCP Registry, and MCPB metadata checks."""

import json
from pathlib import Path
import re
import unittest

import rawmem


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.7.1"


class ReleaseMetadataTests(unittest.TestCase):
    def test_versions_are_consistent(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(
            r'^version\s*=\s*"([^"]+)"\s*$', pyproject, re.MULTILINE
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), EXPECTED_VERSION)
        self.assertEqual(rawmem.__version__, EXPECTED_VERSION)

    def test_registry_and_mcpb_metadata_are_read_only(self) -> None:
        registry = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
        manifest = json.loads(
            (ROOT / "mcpb" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(registry["name"], "io.github.Liyuan1992/rawmem")
        self.assertEqual(registry["version"], EXPECTED_VERSION)
        self.assertEqual(manifest["version"], EXPECTED_VERSION)
        package = registry["packages"][0]
        self.assertEqual(package["registryType"], "mcpb")
        self.assertEqual(package["version"], EXPECTED_VERSION)
        self.assertRegex(package["fileSha256"], r"^[0-9a-f]{64}$")
        args = manifest["server"]["mcp_config"]["args"]
        scopes = args[args.index("--scopes") + 1].split(",")
        self.assertEqual(scopes, ["read:summary"])
        self.assertTrue((ROOT / "mcpb" / "src" / "server.py").is_file())


if __name__ == "__main__":
    unittest.main()
