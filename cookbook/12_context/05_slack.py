"""
Slack Context Provider
Exposes query_<id> (read) and update_<id> (write) tools for Slack workspace access.
Requires: OPENAI_API_KEY, SLACK_BOT_TOKEN
"""

from __future__ import annotations

import asyncio

from agno.agent import Agent
from agno.context.slack import SlackContextProvider
from agno.models.openai import OpenAIResponses

slack = SlackContextProvider(model=OpenAIResponses(id="gpt-5.4-mini"))

agent = Agent(
    model=OpenAIResponses(id="gpt-5.4"),
    tools=slack.get_tools(),
    instructions=slack.instructions(),
    markdown=True,
)


async def main() -> None:
    await agent.aprint_response(
        "Find the 3 most recent messages in the #agents channel. "
        "For each, show the author and a one-line quote."
    )

    print()
    await agent.aprint_response("Post the message 'Hello from agno.context' to #agents.")


if __name__ == "__main__":
    asyncio.run(main())
