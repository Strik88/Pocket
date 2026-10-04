"""Start the real MCP server over stdio, like Claude Desktop does, and call its tools."""

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from pocket_bridge import sync
from pocket_bridge.config import save_settings

from .conftest import make_transport


def test_mcp_tools_over_stdio(settings):
    sync.run_sync(settings, transport=make_transport())
    settings.auto_sync = False  # no network in tests
    save_settings(settings)

    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "pocket_bridge", "mcp"],
            env={**os.environ},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name for t in (await session.list_tools()).tools}
                assert {"search_transcripts", "get_transcript", "list_clients", "get_client_dossier", "assign_recording", "sync_now"} <= tools

                async def call(tool, **args):
                    res = await session.call_tool(tool, args)
                    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")

                assert "Acme" in await call("list_clients")
                assert "rec_beta" in await call("search_transcripts", query="facturatie")
                assert "Offerte sturen" in await call("get_transcript", recording="Kickoff Acme")
                assert "Offerte sturen" in await call("open_action_items", client="Acme")
                assert "Klantdossier: Acme" in await call("get_client_dossier", client="acme")
                assert "Moved" in await call("assign_recording", recording="rec_misc", client="Acme")
                assert "Kickoff Acme" in await call("list_recordings", client="Acme")
                assert "saved" in await call("add_client", name="Delta", keywords=["delta"])

    asyncio.run(run())
