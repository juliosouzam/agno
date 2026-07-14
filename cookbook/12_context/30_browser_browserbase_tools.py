"""
Browser Automation with BrowserbaseTools (SDK)
===============================================

BrowserbaseTools provides direct access to Browserbase's cloud browser
via the Python SDK. Unlike BrowserbaseMCPBackend (Stagehand), this uses
traditional CSS selectors for deterministic, replayable automation.

Features:
- Cloud-hosted browsers (no local Chromium needed)
- Session recording via rrweb (viewable in dashboard)
- Persistent contexts for login state
- Ad blocking and CAPTCHA solving

Requires:
    BROWSERBASE_API_KEY
    BROWSERBASE_PROJECT_ID
    OPENAI_API_KEY
    pip install browserbase playwright
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

    # BrowserbaseTools with session recording enabled by default
    browser_tools = BrowserbaseTools(
        # API keys default to env vars
        # context_id="my-session" for persistent cookies
        # block_ads=True,
    )

    print(f"Tools available: {[t.__name__ for t in browser_tools.tools]}")

    agent = Agent(
        model=OpenAIResponses(id="gpt-5.5"),
        tools=[browser_tools],
        instructions=browser_tools.instructions,
        markdown=True,
    )

    prompt = "Go to https://example.com, tell me what you see, get the session URL for the recording, then close the session."
    print(f"\n> {prompt}\n")
    await agent.aprint_response(prompt)


if __name__ == "__main__":
    asyncio.run(main())
