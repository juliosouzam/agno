"""
Browser Context Provider with Playwright MCP
Requires: OPENAI_API_KEY, Node.js 18+
"""

import asyncio

from agno.agent import Agent
from agno.context.browser import BrowserContextProvider, PlaywrightMCPBackend
from agno.models.openai import OpenAIResponses


async def main() -> None:
    browser = BrowserContextProvider(
        backend=PlaywrightMCPBackend(headless=True),
        model=OpenAIResponses(id="gpt-5.5"),
    )

    await browser.asetup()
    try:
        agent = Agent(
            model=OpenAIResponses(id="gpt-5.5"),
            tools=browser.get_tools(),
            instructions=browser.instructions(),
            markdown=True,
        )

        await agent.aprint_response(
            "Go to https://news.ycombinator.com and tell me the top 3 stories"
        )
    finally:
        await browser.aclose()


if __name__ == "__main__":
    asyncio.run(main())
