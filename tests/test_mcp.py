from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from rawmem.ledger import append_event, build_event
from rawmem.mcp_service import RawmemMCPService, TOOL_NAMES


def _append_fixture(ledger: Path, text: str = "Synthetic evidence") -> None:
    append_event(
        ledger,
        build_event(
            source="synthetic-agent",
            event_type="agent_user_turn",
            project="fictional-project",
            cwd=ledger.parent,
            summary=text,
            raw_text=text,
            tags=["synthetic"],
            payload={"fixture": True},
        ),
    )


class RawmemMCPServiceTests(unittest.TestCase):
    def test_summary_default_and_explicit_full_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            _append_fixture(ledger)
            service = RawmemMCPService(ledger)
            summary = service.recent(limit=1)
            self.assertTrue(summary["ok"])
            self.assertNotIn("raw_text", summary["events"][0])
            denied = service.recent(limit=1, projection="full")
            self.assertEqual(denied["error"]["code"], "scope_denied")
            full = RawmemMCPService(ledger, scopes="read:summary,read:full").recent(
                limit=1, projection="full"
            )
            self.assertEqual(full["events"][0]["raw_text"], "Synthetic evidence")

    def test_status_is_verified_read_only_and_path_free(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "private-location" / "events.jsonl"
            _append_fixture(ledger)
            before = sorted(
                str(path.relative_to(Path(tmp))) for path in Path(tmp).rglob("*")
            )
            payload = RawmemMCPService(ledger).status()
            after = sorted(
                str(path.relative_to(Path(tmp))) for path in Path(tmp).rglob("*")
            )
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["read_only"])
            self.assertEqual(before, after)
            self.assertNotIn(str(ledger), json.dumps(payload))

    def test_recent_filters_and_budgets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            _append_fixture(ledger, "first")
            _append_fixture(ledger, "second")
            service = RawmemMCPService(ledger)
            payload = service.recent(source="synthetic-agent", limit=1)
            self.assertEqual(payload["events"][0]["summary"], "second")
            self.assertEqual(
                service.recent(limit=101)["error"]["code"], "invalid_limit"
            )


class RawmemMCPTransportTests(unittest.TestCase):
    def test_registered_tools_and_real_stdio(self) -> None:
        try:
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client
        except ImportError:
            self.skipTest("mcp optional extra is not installed")

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            _append_fixture(ledger)

            def payload(result) -> dict:
                structured = getattr(result, "structuredContent", None)
                if isinstance(structured, dict):
                    return structured
                for item in getattr(result, "content", []):
                    text = getattr(item, "text", "")
                    if text:
                        return json.loads(text)
                raise AssertionError("MCP tool result did not contain JSON")

            async def run() -> None:
                parameters = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "rawmem.mcp_server", "--ledger", str(ledger)],
                )
                async with stdio_client(parameters) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        self.assertEqual(
                            {item.name for item in tools.tools}, set(TOOL_NAMES)
                        )
                        status = payload(await session.call_tool("rawmem_status", {}))
                        self.assertTrue(status["verification"]["valid"])
                        recent = payload(
                            await session.call_tool("rawmem_recent", {"limit": 1})
                        )
                        self.assertEqual(recent["returned"], 1)
                        denied = payload(
                            await session.call_tool(
                                "rawmem_recent", {"limit": 1, "projection": "full"}
                            )
                        )
                        self.assertEqual(denied["error"]["code"], "scope_denied")

            asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
