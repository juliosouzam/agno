"""
Browser Automation with BrowserbaseTools (SDK)
Cloud-hosted browsers with session recording and CSS selectors.
Requires: BROWSERBASE_API_KEY, BROWSERBASE_PROJECT_ID, OPENAI_API_KEY
"""

import asyncio
from os import getenv

from agno.agent import Agent
from agno.models.openai import OpenAIResponses


async def main() -> None:
    if not getenv("BROWSERBASE_API_KEY") or not getenv("BROWSERBASE_PROJECT_ID"):
        print("Missing BROWSERBASE_API_KEY or BROWSERBASE_PROJECT_ID")
        return

    from agno.tools.browserbase import BrowserbaseTools

    browser_tools = BrowserbaseTools()

    agent = Agent(
        model=OpenAIResponses(id="gpt-5.5"),
        tools=[browser_tools],
        instructions=browser_tools.instructions,
        markdown=True,
    )

    await agent.aprint_response(
        "Go to https://example.com, tell me what you see, "
        "get the session URL for the recording, then close the session."
    )


if __name__ == "__main__":
    asyncio.run(main())
