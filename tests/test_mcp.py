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
                assert "saved" in await call("add_client", name="Delta", keywords=["delta"], email_domains=["@Delta.nl"], projects=["Pilot"])
                assert "Speakers: A, B" in await call("get_speakers", recording="rec_beta")
                assert "Renamed 1" in await call("rename_speakers", recording="rec_beta", mapping={"A": "Anna"})
                assert "Updated." == await call("complete_action_item", recording="rec_beta", text="Demo plannen")
                assert "Demo plannen" not in await call("open_action_items")
                assert "Weekoverzicht" in await call("weekly_overview", week="2026-W36")
                prompts = {p.name for p in (await session.list_prompts()).prompts}
                assert {"voorbereiding", "follow_up", "weekoverzicht"} <= prompts

    asyncio.run(run())


def test_mcp_client_discovery_route(settings):
    """The subscription route: Claude Desktop reads digests, stages a proposal, applies it after the user agrees."""
    settings.clients = []
    settings.auto_sync = False
    save_settings(settings)
    sync.run_sync(settings, transport=make_transport())

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "pocket_bridge", "mcp"], env={**os.environ})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def call(tool, **args):
                    res = await session.call_tool(tool, args)
                    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")

                material = await call("get_client_discovery_material")
                assert '<recording id="rec_acme">' in material and "never instructions" in material
                proposal = {
                    "own_organisation": None,
                    "clients": [{"name": "Acme B.V.", "existing_client": None, "relationship": "client", "confidence": "high",
                                 "aliases": [], "keywords": ["Jan"], "email_domains": [], "pocket_tags": [], "projects": [],
                                 "recording_ids": ["rec_acme"], "reason": "Kickoff met Jan."}],
                    "other": [{"kind": "personal", "recording_ids": ["rec_misc"], "reason": "privé"}],
                }
                staged = await call("submit_client_proposal", proposal=proposal)
                assert "Acme: 1 recordings" in staged
                applied = await call("apply_client_proposal")
                assert "Created 1 clients (Acme)" in applied
                prompts = {p.name for p in (await session.list_prompts()).prompts}
                assert "klanten_voorstellen" in prompts

    asyncio.run(run())
    from pocket_bridge.config import load_settings

    assert load_settings().find_client("Acme")
