"""
Browser Context Provider with Playwright MCP
=============================================

BrowserContextProvider wraps a ContextBackend for browser automation.
PlaywrightMCPBackend runs Playwright's official MCP server via stdio.

The MCP server exposes browser tools using an accessibility tree, which
is more token-efficient than vision-based approaches (~1/4 the tokens).

Requires:
    OPENAI_API_KEY
    Node.js 18+ (npx downloads @playwright/mcp on first run)
"""

import asyncio

from agno.agent import Agent
from agno.context.browser import BrowserContextProvider, PlaywrightMCPBackend
from agno.models.openai import OpenAIResponses


async def main() -> None:
    # PlaywrightMCPBackend starts the browser via npx @playwright/mcp
    # headless=True runs without a visible window
    browser = BrowserContextProvider(
        backend=PlaywrightMCPBackend(headless=True),
        model=OpenAIResponses(id="gpt-5.5"),
    )

    await browser.asetup()
    try:
        print(f"\nbrowser.status() = {browser.status()}\n")

        agent = Agent(
            model=OpenAIResponses(id="gpt-5.5"),
            tools=browser.get_tools(),
            instructions=browser.instructions(),
            markdown=True,
        )

        prompt = "Go to https://news.ycombinator.com and tell me the top 3 stories"
        print(f"> {prompt}\n")
        await agent.aprint_response(prompt)
    finally:
        await browser.aclose()


if __name__ == "__main__":
    asyncio.run(main())
