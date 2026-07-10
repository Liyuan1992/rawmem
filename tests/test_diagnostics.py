from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from rawmem.cli import main
from rawmem.config import load_global_config, write_global_config
from rawmem.diagnostics import diagnostics_exit_code, read_recent_events, run_diagnostics
from rawmem.web_capture import create_capture_server


class DiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.home = self.base / "rawmem-home"
        self.git_config = self.base / "gitconfig"
        self._old_home = os.environ.get("RAWMEM_HOME")
        self._old_git_config = os.environ.get("GIT_CONFIG_GLOBAL")
        os.environ["RAWMEM_HOME"] = str(self.home)
        os.environ["GIT_CONFIG_GLOBAL"] = str(self.git_config)

    def tearDown(self) -> None:
        restore_env("RAWMEM_HOME", self._old_home)
        restore_env("GIT_CONFIG_GLOBAL", self._old_git_config)
        self._tmp.cleanup()

    def test_configured_but_stopped_install_has_warnings_not_failures(self) -> None:
        write_global_config()
        checks = run_diagnostics(timeout=0.05)
        self.assertFalse(any(check.status == "FAIL" for check in checks))
        self.assertEqual(diagnostics_exit_code(checks), 0)
        self.assertEqual(diagnostics_exit_code(checks, strict=True), 1)

    def test_invalid_config_is_a_hard_failure(self) -> None:
        self.home.mkdir(parents=True)
        (self.home / "config.json").write_text("{broken", encoding="utf-8")
        checks = run_diagnostics(timeout=0.05)
        config_check = next(check for check in checks if check.name == "config")
        self.assertEqual(config_check.status, "FAIL")
        self.assertEqual(diagnostics_exit_code(checks), 1)

    def test_doctor_json_does_not_print_browser_token(self) -> None:
        write_global_config()
        token = load_global_config()["daemon"]["serve"]["token"]
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["doctor", "--json", "--timeout", "0.05"])
        self.assertEqual(code, 0)
        self.assertNotIn(token, out.getvalue())
        json.loads(out.getvalue())

    def test_token_handshake_passes_against_running_server(self) -> None:
        self.home.mkdir(parents=True)
        server = create_capture_server(
            host="127.0.0.1",
            port=0,
            ledger_path=self.home / "events.jsonl",
            token="doctor-token",
            require_token=True,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _, port = server.server_address
            config_path = self.home / "config.json"
            config = {
                "schema": "rawmem.config.v2",
                "ledger": str(self.home / "events.jsonl"),
                "daemon": {
                    "serve": {
                        "enabled": True,
                        "host": "127.0.0.1",
                        "port": port,
                        "require_token": True,
                        "token": "doctor-token",
                    }
                },
            }
            config_path.write_text(json.dumps(config), encoding="utf-8")
            checks = run_diagnostics(timeout=1)
            handshake = next(check for check in checks if check.name == "token handshake")
            self.assertEqual(handshake.status, "PASS")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_recent_event_reader_only_returns_tail(self) -> None:
        ledger = self.base / "events.jsonl"
        ledger.write_text(
            "\n".join(json.dumps({"index": index}) for index in range(20)) + "\n",
            encoding="utf-8",
        )
        self.assertEqual([event["index"] for event in read_recent_events(ledger, limit=3)], [17, 18, 19])


def restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
