"""
Browser Automation with PlaywrightTools
========================================

PlaywrightTools provides direct Playwright SDK access with video recording,
PDF generation, console capture, and network request logging.

Unlike PlaywrightMCPBackend (which uses the MCP server), this toolkit gives
you full control over the browser via Python, with sync and async variants.

Requires:
    OPENAI_API_KEY
    pip install playwright
    playwright install chromium
"""

import asyncio

from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.tools.playwright import PlaywrightTools


async def main() -> None:
    # PlaywrightTools with video recording enabled
    browser_tools = PlaywrightTools(
        headless=True,
        record_video_dir="/tmp/playwright_recordings",
        enable_get_console_messages=True,
    )

    print(f"Tools available: {[t.__name__ for t in browser_tools.tools]}")

    agent = Agent(
        model=OpenAIResponses(id="gpt-5.5"),
        tools=[browser_tools],
        instructions=browser_tools.instructions,
        markdown=True,
    )

    prompt = (
        "Go to https://example.com and tell me what you see. Then close the session."
    )
    print(f"\n> {prompt}\n")
    await agent.aprint_response(prompt)

    # Video is saved when session closes
    print("\nCheck /tmp/playwright_recordings/ for the video recording")


if __name__ == "__main__":
    asyncio.run(main())
