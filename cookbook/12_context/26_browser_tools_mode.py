"""
Browser Context Provider - Tools Mode (Direct Access)
With mode=ContextMode.tools, you get raw browser tools instead of a sub-agent.
Requires: OPENAI_API_KEY, Node.js 18+
"""

import asyncio

from agno.agent import Agent
from agno.context.browser import BrowserContextProvider, PlaywrightMCPBackend
from agno.context.mode import ContextMode
from agno.models.openai import OpenAIResponses


async def main() -> None:
    browser = BrowserContextProvider(
        backend=PlaywrightMCPBackend(headless=True),
        mode=ContextMode.tools,
    )

    await browser.asetup()
    try:
        tools = browser.get_tools()
        print("Available browser tools:")
        for tool in tools:
            if hasattr(tool, "functions"):
                for name in tool.functions:
                    print(f"  - {name}")
            else:
                print(f"  - {tool.name}")

        agent = Agent(
            model=OpenAIResponses(id="gpt-5.5"),
            tools=tools,
            instructions=(
                "You have direct access to browser automation tools. "
                "Use browser_navigate to go to URLs, browser_snapshot to see "
                "the page structure, and browser_click/browser_type for interaction."
            ),
            markdown=True,
        )

        await agent.aprint_response(
            "Navigate to https://example.com and take a snapshot of the page"
        )
    finally:
        await browser.aclose()


if __name__ == "__main__":
    asyncio.run(main())
