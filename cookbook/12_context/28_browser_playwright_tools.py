"""
Browser Automation with PlaywrightTools
Direct Playwright SDK access with video recording, console capture, etc.
Requires: OPENAI_API_KEY, playwright install chromium
"""

import asyncio

from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.tools.playwright import PlaywrightTools


async def main() -> None:
    browser_tools = PlaywrightTools(
        headless=True,
        record_video_dir="/tmp/playwright_recordings",
        enable_get_console_messages=True,
    )

    agent = Agent(
        model=OpenAIResponses(id="gpt-5.5"),
        tools=[browser_tools],
        instructions=browser_tools.instructions,
        markdown=True,
    )

    await agent.aprint_response(
        "Go to https://example.com and tell me what you see. Then close the session."
    )

    print("\nVideo saved to /tmp/playwright_recordings/")


if __name__ == "__main__":
    asyncio.run(main())
