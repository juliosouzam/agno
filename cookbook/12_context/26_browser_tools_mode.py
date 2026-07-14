"""
Browser Context Provider - Tools Mode (Direct Access)
======================================================

By default, BrowserContextProvider wraps browser tools in a sub-agent
that handles natural language requests. With mode=ContextMode.tools,
you get direct access to the raw browser tools instead.

Use tools mode when:
- You want fine-grained control over browser actions
- You're building a workflow that needs explicit tool calls
- You want to avoid the sub-agent overhead

Use default mode when:
- You want natural language browser automation
- The calling agent doesn't need to see individual browser operations

Requires:
    OPENAI_API_KEY
    Node.js 18+ (npx downloads @playwright/mcp on first run)
"""

import asyncio

from agno.agent import Agent
from agno.context.browser import BrowserContextProvider, PlaywrightMCPBackend
from agno.context.mode import ContextMode
from agno.models.openai import OpenAIResponses


async def main() -> None:
    # Tools mode exposes raw browser tools directly
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
        print()

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

        prompt = "Navigate to https://example.com and take a snapshot of the page"
        print(f"> {prompt}\n")
        await agent.aprint_response(prompt)
    finally:
        await browser.aclose()


if __name__ == "__main__":
    asyncio.run(main())
