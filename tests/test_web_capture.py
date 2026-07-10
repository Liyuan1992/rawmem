from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from rawmem.ledger import read_events
from rawmem.web_capture import create_capture_server


class CaptureServerSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ledger = Path(self._tmp.name) / "events.jsonl"
        self.server = create_capture_server(
            host="127.0.0.1",
            port=0,
            ledger_path=self.ledger,
            token="secret-token",
            require_token=True,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def test_capture_requires_token(self) -> None:
        payload = json.dumps({"source": "unit", "raw_text": "blocked"}).encode("utf-8")
        request = Request(
            self.base_url + "/capture",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(read_events(self.ledger), [])

    def test_capture_accepts_valid_token_without_exposing_ledger_path(self) -> None:
        payload = json.dumps({"source": "unit", "raw_text": "saved"}).encode("utf-8")
        request = Request(
            self.base_url + "/capture",
            data=payload,
            headers={"Content-Type": "application/json", "X-Rawmem-Token": "secret-token"},
            method="POST",
        )
        response = json.loads(urlopen(request, timeout=5).read().decode("utf-8"))
        self.assertTrue(response["ok"])
        self.assertNotIn("ledger", response)
        events = read_events(self.ledger)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["raw_text"], "saved")

        health = json.loads(urlopen(self.base_url + "/health", timeout=5).read().decode("utf-8"))
        self.assertEqual(health, {"ok": True, "auth": "required"})

        check_request = Request(
            self.base_url + "/check",
            headers={"X-Rawmem-Token": "secret-token"},
            method="GET",
        )
        check = json.loads(urlopen(check_request, timeout=5).read().decode("utf-8"))
        self.assertEqual(check, {"ok": True, "authorized": True})

    def test_connection_check_rejects_wrong_token(self) -> None:
        request = Request(
            self.base_url + "/check",
            headers={"X-Rawmem-Token": "wrong-token"},
            method="GET",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 401)

    def test_cors_does_not_allow_arbitrary_web_origins(self) -> None:
        request = Request(
            self.base_url + "/capture",
            headers={
                "Origin": "https://example.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, X-Rawmem-Token",
            },
            method="OPTIONS",
        )
        response = urlopen(request, timeout=5)
        self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))

        deceptive = Request(
            self.base_url + "/capture",
            headers={
                "Origin": "http://localhost.evil.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, X-Rawmem-Token",
            },
            method="OPTIONS",
        )
        deceptive_response = urlopen(deceptive, timeout=5)
        self.assertIsNone(deceptive_response.headers.get("Access-Control-Allow-Origin"))

        allowed = Request(
            self.base_url + "/capture",
            headers={
                "Origin": "chrome-extension://extension-id",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, X-Rawmem-Token",
            },
            method="OPTIONS",
        )
        allowed_response = urlopen(allowed, timeout=5)
        self.assertEqual(
            allowed_response.headers.get("Access-Control-Allow-Origin"),
            "chrome-extension://extension-id",
        )


class CaptureServerPolicyTests(unittest.TestCase):
    def test_capture_policy_can_reject_before_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            server = create_capture_server(
                host="127.0.0.1",
                port=0,
                ledger_path=ledger,
                token="secret-token",
                require_token=True,
                event_policy=lambda _event: None,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            try:
                request = Request(
                    f"http://{host}:{port}/capture",
                    data=json.dumps({"source": "unit", "raw_text": "blocked"}).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "X-Rawmem-Token": "secret-token",
                    },
                    method="POST",
                )
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=5)
                self.assertEqual(caught.exception.code, 403)
                self.assertEqual(read_events(ledger), [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
