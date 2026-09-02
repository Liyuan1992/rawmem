"""Run a path-free, synthetic DeepSeek Harness MCP interoperability demo."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from rawmem.ledger import append_event, build_event


def _payload(result: object) -> dict:
    structured = getattr(
        result, "structuredContent", getattr(result, "structured_content", None)
    )
    if isinstance(structured, dict):
        return structured
    for item in getattr(result, "content", []):
        text = getattr(item, "text", "")
        if text:
            return json.loads(text)
    raise RuntimeError("MCP result did not contain JSON")


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="rawmem-dsh-demo-") as temporary:
        ledger = Path(temporary) / "events.jsonl"
        for summary in (
            "A fictional API timeout was reproduced.",
            "The fictional retry test passed after the fix.",
        ):
            append_event(
                ledger,
                build_event(
                    source="deepseek-harness",
                    event_type="agent_user_turn",
                    project="fictional-retry-demo",
                    cwd=ledger.parent,
                    summary=summary,
                    raw_text=summary,
                    tags=["synthetic"],
                    payload={"fixture": True},
                ),
            )

        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "rawmem.mcp_server",
                "--ledger",
                str(ledger),
                "--scopes",
                "read:summary",
            ],
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                status = _payload(await session.call_tool("rawmem_status", {}))
                recent = _payload(
                    await session.call_tool("rawmem_recent", {"limit": 2})
                )
                denied = _payload(
                    await session.call_tool(
                        "rawmem_recent", {"limit": 1, "projection": "full"}
                    )
                )

        output = {
            "server": "rawmem",
            "tools": sorted(tool.name for tool in tools.tools),
            "read_only": status["read_only"],
            "chain_valid": status["verification"]["valid"],
            "recent_summaries": [event["summary"] for event in recent["events"]],
            "raw_text_exposed": any("raw_text" in event for event in recent["events"]),
            "full_projection": denied["error"]["code"],
            "authority": "raw evidence only; no approval or promotion tool",
        }
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
