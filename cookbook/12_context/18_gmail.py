"""
Gmail Context Provider
Exposes query_gmail (read) and update_gmail (write) for email access.
Requires: OPENAI_API_KEY + Google OAuth or Service Account credentials
"""

from __future__ import annotations

import asyncio

from agno.agent import Agent
from agno.context.gmail import GmailContextProvider
from agno.models.openai import OpenAIResponses


async def demo_read_only():
    print("Demo 1: Read-Only Gmail Access\n")

    gmail = GmailContextProvider(
        model=OpenAIResponses(id="gpt-5.4-mini"),
        read=True,
        write=False,
    )

    agent = Agent(
        model=OpenAIResponses(id="gpt-5.4"),
        tools=gmail.get_tools(),
        instructions=gmail.instructions(),
        markdown=True,
    )

    await agent.aprint_response(
        "Find my unread emails from the last 3 days. "
        "Group them by sender and summarize what each person is asking about.",
        stream=True,
    )


async def demo_read_write():
    print("\nDemo 2: Read-Write Gmail Access\n")

    gmail = GmailContextProvider(
        model=OpenAIResponses(id="gpt-5.4-mini"),
        read=True,
        write=True,
    )

    agent = Agent(
        model=OpenAIResponses(id="gpt-5.4"),
        tools=gmail.get_tools(),
        instructions=gmail.instructions(),
        markdown=True,
    )

    await agent.aprint_response(
        "Find the most recent email thread where I haven't replied yet. "
        "Draft a brief follow-up response and save it as a draft.",
        stream=True,
    )


async def main():
    await demo_read_only()
    await demo_read_write()


if __name__ == "__main__":
    asyncio.run(main())
