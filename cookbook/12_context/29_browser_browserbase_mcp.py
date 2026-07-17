"""
Browser Automation with Browserbase MCP (Stagehand)
Uses Stagehand for AI-powered automation with natural language actions.
Requires: BROWSERBASE_API_KEY, BROWSERBASE_PROJECT_ID, GEMINI_API_KEY, Node.js 18+
"""

import asyncio

from agno.agent import Agent
from agno.context.browser import BrowserbaseMCPBackend, BrowserContextProvider
from agno.models.openai import OpenAIResponses


async def main() -> None:
    browser = BrowserContextProvider(
        backend=BrowserbaseMCPBackend(),
        model=OpenAIResponses(id="gpt-5.5"),
    )

    status = browser.status()
    if not status.ok:
        print(
            "Missing credentials. Set BROWSERBASE_API_KEY, BROWSERBASE_PROJECT_ID, GEMINI_API_KEY"
        )
        return

    await browser.asetup()
    try:
        agent = Agent(
            model=OpenAIResponses(id="gpt-5.5"),
            tools=browser.get_tools(),
            instructions=browser.instructions(),
            markdown=True,
        )

        await agent.aprint_response(
            "Go to https://news.ycombinator.com and extract the top 3 story titles"
        )
    finally:
        await browser.aclose()


if __name__ == "__main__":
    asyncio.run(main())
