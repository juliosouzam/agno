"""Unit tests for BrowserContextProvider."""

import pytest

from agno.context.browser import BrowserContextProvider, PlaywrightMCPBackend
from agno.context.mode import ContextMode


class TestPlaywrightMCPBackend:
    def test_status_shows_browser_and_mode(self):
        backend = PlaywrightMCPBackend()
        status = backend.status()
        assert status.ok is True
        assert "chromium" in status.detail
        assert "headless" in status.detail

    def test_default_browser_is_chromium(self):
        backend = PlaywrightMCPBackend()
        assert backend.browser == "chromium"

    def test_custom_browser(self):
        backend = PlaywrightMCPBackend(browser="firefox")
        assert backend.browser == "firefox"
        assert "firefox" in backend.status().detail

    def test_headless_default_true(self):
        backend = PlaywrightMCPBackend()
        assert backend.headless is True

    def test_headless_false(self):
        backend = PlaywrightMCPBackend(headless=False)
        assert backend.headless is False

    def test_default_include_tools_is_none(self):
        backend = PlaywrightMCPBackend()
        assert backend.include_tools is None

    def test_custom_include_tools(self):
        backend = PlaywrightMCPBackend(include_tools=["browser_navigate", "browser_snapshot"])
        assert backend.include_tools == ["browser_navigate", "browser_snapshot"]

    def test_tool_name_prefix(self):
        backend = PlaywrightMCPBackend(tool_name_prefix="pw_")
        assert backend.tool_name_prefix == "pw_"


class TestBrowserContextProvider:
    def test_default_id_and_name(self):
        backend = PlaywrightMCPBackend()
        provider = BrowserContextProvider(backend=backend)
        assert provider.id == "browser"
        assert provider.name == "Browser"

    def test_custom_id_and_name(self):
        backend = PlaywrightMCPBackend()
        provider = BrowserContextProvider(backend=backend, id="chrome", name="Chrome Browser")
        assert provider.id == "chrome"
        assert provider.name == "Chrome Browser"

    def test_query_tool_name(self):
        backend = PlaywrightMCPBackend()
        provider = BrowserContextProvider(backend=backend)
        assert provider.query_tool_name == "query_browser"

    def test_custom_id_changes_query_tool_name(self):
        backend = PlaywrightMCPBackend()
        provider = BrowserContextProvider(backend=backend, id="chrome")
        assert provider.query_tool_name == "query_chrome"

    def test_status_delegates_to_backend(self):
        backend = PlaywrightMCPBackend()
        provider = BrowserContextProvider(backend=backend)
        status = provider.status()
        assert status.ok is True
        assert "playwright-mcp" in status.detail

    def test_instructions_default_mode(self):
        backend = PlaywrightMCPBackend()
        provider = BrowserContextProvider(backend=backend)
        instructions = provider.instructions()
        assert "query_browser" in instructions

    def test_instructions_tools_mode(self):
        backend = PlaywrightMCPBackend()
        provider = BrowserContextProvider(backend=backend, mode=ContextMode.tools)
        instructions = provider.instructions()
        assert "browser tools" in instructions.lower()

    def test_default_tools_returns_query_tool(self):
        backend = PlaywrightMCPBackend()
        provider = BrowserContextProvider(backend=backend)
        tools = provider.get_tools()
        tool_names = [t.name for t in tools]
        assert tool_names == ["query_browser"]

    def test_all_tools_mode_returns_backend_tools(self):
        backend = PlaywrightMCPBackend()
        provider = BrowserContextProvider(backend=backend, mode=ContextMode.tools)
        tools = provider.get_tools()
        assert len(tools) == 1

    def test_sync_query_raises_not_implemented(self):
        backend = PlaywrightMCPBackend()
        provider = BrowserContextProvider(backend=backend)
        with pytest.raises(NotImplementedError, match="async-only"):
            provider.query("search something")

    @pytest.mark.asyncio
    async def test_aclose_clears_agent_cache(self):
        backend = PlaywrightMCPBackend()
        provider = BrowserContextProvider(backend=backend)
        _ = provider._ensure_agent()
        assert provider._agent is not None
        await provider.aclose()
        assert provider._agent is None
