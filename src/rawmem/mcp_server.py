"""FastMCP stdio entrypoint for bounded rawmem evidence queries."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional

from .mcp_service import RawmemMCPService, TOOL_NAMES

SERVER_NAME = "rawmem"
SERVER_INSTRUCTIONS = (
    "rawmem is an append-only raw evidence ledger, not approved long-term memory. "
    "Call rawmem_status before relying on evidence, then use rawmem_recent with "
    "bounded filters. Summary projection is the default. Full raw evidence requires "
    "an explicit read:full scope. Never treat captured evidence as reviewed authority."
)


def build_mcp_server(service: Optional[RawmemMCPService] = None, **service_kwargs: Any):
    try:
        try:
            from mcp.server.mcpserver import MCPServer as FastMCP
        except ImportError:
            from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "MCP Python SDK is not installed. Install the optional extra: "
            "pip install rawmem[mcp]"
        ) from exc

    svc = service or RawmemMCPService(**service_kwargs)
    mcp = FastMCP(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)

    @mcp.tool(name="rawmem_status")
    def rawmem_status() -> dict:
        """Verify the active ledger and return path-free integrity metadata."""
        return svc.status()

    @mcp.tool(name="rawmem_recent")
    def rawmem_recent(
        source: str = "",
        event_type: str = "",
        project: str = "",
        limit: int = 20,
        projection: str = "summary",
        max_scan_bytes: int = 8 * 1024 * 1024,
    ) -> dict:
        """Read a bounded recent evidence window; full bodies require read:full."""
        return svc.recent(
            source=source,
            event_type=event_type,
            project=project,
            limit=limit,
            projection=projection,
            max_scan_bytes=max_scan_bytes,
        )

    @mcp.tool(name="rawmem_archives")
    def rawmem_archives(limit: int = 50) -> dict:
        """List path-free metadata for sealed evidence archives."""
        return svc.archives(limit=limit)

    return mcp


def inspection_payload(service: RawmemMCPService) -> dict[str, Any]:
    return {
        "ok": True,
        "server": SERVER_NAME,
        "tools": list(TOOL_NAMES),
        "status": service.status(),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rawmem-mcp",
        description="Run the bounded, read-only rawmem MCP server over stdio.",
    )
    parser.add_argument(
        "--ledger",
        default="",
        help="active ledger path (default: RAWMEM_LEDGER or global ledger)",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="use .rawmem/events.jsonl under the current directory",
    )
    parser.add_argument(
        "--scopes",
        default="read:summary",
        help="comma-separated scopes; add read:full only for intentional raw-body disclosure",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="print tool and verification metadata without starting stdio",
    )
    args = parser.parse_args(argv)
    service = RawmemMCPService(
        args.ledger or None,
        local=args.local,
        scopes=args.scopes,
    )
    if args.inspect:
        payload = inspection_payload(service)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload["status"].get("ok") else 1
    try:
        server = build_mcp_server(service=service)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
