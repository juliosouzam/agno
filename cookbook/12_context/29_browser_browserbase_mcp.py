"""
Browser Automation with Browserbase MCP (Stagehand)
====================================================

BrowserbaseMCPBackend wraps Browserbase's MCP server which uses Stagehand
for AI-powered browser automation. Actions use natural language instead
of CSS selectors.

Unlike BrowserbaseBackend (SDK), this approach lets you say:
- `act("click the login button")` instead of `click("#login-btn")`
- `extract("get all product prices")` instead of parsing HTML

Requires:
    BROWSERBASE_API_KEY
    BROWSERBASE_PROJECT_ID
    GEMINI_API_KEY (for Stagehand's internal LLM)
    Node.js 18+
"""

import asyncio

from agno.agent import Agent
from agno.context.browser import BrowserbaseMCPBackend, BrowserContextProvider
from agno.models.openai import OpenAIResponses


async def main() -> None:
    # BrowserbaseMCPBackend uses Stagehand for semantic actions
    browser = BrowserContextProvider(
        backend=BrowserbaseMCPBackend(
            # API keys default to env vars
            # context_id="my-session" for persistent cookies
        ),
        model=OpenAIResponses(id="gpt-5.5"),
    )

    status = browser.status()
    print(f"\nbrowser.status() = {status}\n")

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

        # Stagehand handles element finding via natural language
        prompt = "Go to https://news.ycombinator.com and extract the top 3 story titles"
        print(f"> {prompt}\n")
        await agent.aprint_response(prompt)
    finally:
        await browser.aclose()


if __name__ == "__main__":
    asyncio.run(main())
