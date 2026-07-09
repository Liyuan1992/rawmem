from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .ledger import append_event, build_event, resolve_ledger_path


def build_bookmarklet(endpoint: str = "http://127.0.0.1:8765/capture") -> str:
    script = (
        "(()=>{"
        "const text=(window.getSelection&&String(window.getSelection()))||'';"
        "const payload={source:'browser',event_type:'web_clip',summary:document.title,"
        "raw_text:text||document.title,tags:['browser','bookmarklet'],"
        "payload:{url:location.href,title:document.title,host:location.host}};"
        f"fetch('{endpoint}',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}})"
        ".then(()=>alert('rawmem captured')).catch(e=>alert('rawmem failed: '+e));"
        "})()"
    )
    return "javascript:" + script


def serve_capture(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    ledger_path: str | Path | None = None,
    local: bool = False,
    cwd: str | Path | None = None,
) -> None:
    base_cwd = Path(cwd or Path.cwd()).resolve()
    ledger = resolve_ledger_path(ledger_path, local=local, cwd=base_cwd)

    class Handler(BaseHTTPRequestHandler):
        server_version = "rawmem-capture/0.1"

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/health":
                self._json({"ok": True, "ledger": str(ledger)})
                return
            if path == "/bookmarklet":
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(build_bookmarklet(f"http://{host}:{port}/capture").encode("utf-8"))
                return
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path != "/capture":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                self.send_error(413, "payload too large")
                return
            try:
                body = self.rfile.read(length).decode("utf-8")
                payload = json.loads(body or "{}")
                event = event_from_adapter_payload(payload, cwd=base_cwd)
                saved = append_event(ledger, event)
            except Exception as exc:  # pragma: no cover - defensive HTTP boundary
                self.send_error(400, str(exc))
                return
            self._json({"ok": True, "event_id": saved["event_id"], "ledger": str(ledger)})

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")

        def _json(self, data: dict[str, Any]) -> None:
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"rawmem capture server listening on http://{host}:{port}")
    print(f"ledger: {ledger}")
    server.serve_forever()


def event_from_adapter_payload(payload: dict[str, Any], *, cwd: str | Path | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("adapter payload must be a JSON object")
    source = str(payload.get("source") or "adapter")
    event_type = str(payload.get("event_type") or payload.get("type") or "adapter_event")
    tags = payload.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    raw_payload = payload.get("payload")
    if raw_payload is None:
        known = {
            "schema",
            "event_id",
            "ts",
            "source",
            "event_type",
            "type",
            "project",
            "cwd",
            "summary",
            "raw_text",
            "text",
            "tags",
            "artifacts",
            "payload",
            "privacy",
            "previous_hash",
            "content_hash",
        }
        raw_payload = {key: value for key, value in payload.items() if key not in known}
    privacy = payload.get("privacy") or {}
    return build_event(
        source=source,
        event_type=event_type,
        project=payload.get("project"),
        cwd=payload.get("cwd") or cwd,
        summary=payload.get("summary"),
        raw_text=payload.get("raw_text") or payload.get("text") or "",
        tags=tags,
        artifacts=payload.get("artifacts") or [],
        payload=raw_payload,
        privacy_scope=privacy.get("scope", "local_only") if isinstance(privacy, dict) else "local_only",
        review_required=privacy.get("review_required", True) if isinstance(privacy, dict) else True,
    )
