from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .ledger import append_event, build_event, resolve_ledger_path


DEFAULT_ALLOWED_ORIGINS = [
    "http://127.0.0.1",
    "http://localhost",
    "chrome-extension://",
    "moz-extension://",
]


def build_bookmarklet(endpoint: str = "http://127.0.0.1:8765/capture", *, token: str | None = None) -> str:
    headers = "{'Content-Type':'application/json'}"
    if token:
        headers = "{'Content-Type':'application/json','X-Rawmem-Token':" + json.dumps(token) + "}"
    script = (
        "(()=>{"
        "const text=(window.getSelection&&String(window.getSelection()))||'';"
        "const payload={source:'browser',event_type:'web_clip',summary:document.title,"
        "raw_text:text||document.title,tags:['browser','bookmarklet'],"
        "payload:{url:location.href,title:document.title,host:location.host}};"
        f"fetch('{endpoint}',{{method:'POST',headers:{headers},body:JSON.stringify(payload)}})"
        ".then(()=>alert('rawmem captured')).catch(e=>alert('rawmem failed: '+e));"
        "})()"
    )
    return "javascript:" + script


def create_capture_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    ledger_path: str | Path | None = None,
    local: bool = False,
    cwd: str | Path | None = None,
    token: str | None = None,
    require_token: bool = True,
    allowed_origins: list[str] | None = None,
    event_policy: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> ThreadingHTTPServer:
    base_cwd = Path(cwd or Path.cwd()).resolve()
    ledger = resolve_ledger_path(ledger_path, local=local, cwd=base_cwd)
    origins = allowed_origins or DEFAULT_ALLOWED_ORIGINS

    class Handler(BaseHTTPRequestHandler):
        server_version = "rawmem-capture/0.6"

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/health":
                self._json({"ok": True, "auth": "required" if require_token else "disabled"})
                return
            if path == "/check":
                if not self._authorized():
                    self._json({"ok": False, "error": "unauthorized"}, status=401)
                    return
                self._json({"ok": True, "authorized": True})
                return
            if path == "/bookmarklet":
                if not self._authorized():
                    self._json({"ok": False, "error": "unauthorized"}, status=401)
                    return
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    build_bookmarklet(f"http://{host}:{port}/capture", token=token).encode("utf-8")
                )
                return
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/capture":
                self.send_error(404)
                return
            if not self._authorized(parsed.query):
                self._json({"ok": False, "error": "unauthorized"}, status=401)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                self.send_error(413, "payload too large")
                return
            try:
                body = self.rfile.read(length).decode("utf-8")
                payload = json.loads(body or "{}")
                event = event_from_adapter_payload(payload, cwd=base_cwd)
                if event_policy is not None:
                    event = event_policy(event)
                    if event is None:
                        self._json({"ok": False, "error": "capture_policy_rejected"}, status=403)
                        return
                saved = append_event(ledger, event)
            except Exception as exc:  # pragma: no cover - defensive HTTP boundary
                self.send_error(400, str(exc))
                return
            self._json({"ok": True, "event_id": saved["event_id"]})

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _cors(self) -> None:
            origin = self.headers.get("Origin")
            if origin and self._origin_allowed(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Rawmem-Token")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")

        def _json(self, data: dict[str, Any], *, status: int = 200) -> None:
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

        def _origin_allowed(self, origin: str) -> bool:
            for item in origins:
                if item.endswith("://") and origin.startswith(item):
                    return True
                if origin == item or origin.startswith(item + ":"):
                    return True
            return False

        def _authorized(self, query: str = "") -> bool:
            if not require_token:
                return True
            if not token:
                return False
            provided = self.headers.get("X-Rawmem-Token") or ""
            if not provided and query:
                provided = (parse_qs(query).get("token") or [""])[0]
            return secrets.compare_digest(provided, token)

    server = ThreadingHTTPServer((host, port), Handler)
    server.rawmem_ledger = ledger  # type: ignore[attr-defined]
    return server


def serve_capture(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    ledger_path: str | Path | None = None,
    local: bool = False,
    cwd: str | Path | None = None,
    token: str | None = None,
    require_token: bool = True,
    allowed_origins: list[str] | None = None,
    event_policy: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> None:
    server = create_capture_server(
        host=host,
        port=port,
        ledger_path=ledger_path,
        local=local,
        cwd=cwd,
        token=token,
        require_token=require_token,
        allowed_origins=allowed_origins,
        event_policy=event_policy,
    )
    print(f"rawmem capture server listening on http://{host}:{port}")
    print(f"ledger: {server.rawmem_ledger}")  # type: ignore[attr-defined]
    print(f"auth: {'required' if require_token else 'disabled'}")
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
