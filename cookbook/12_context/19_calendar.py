"""
Google Calendar Context Provider
Exposes query_calendar (read) and update_calendar (write) for calendar access.
Requires: OPENAI_API_KEY + Google OAuth or Service Account credentials
"""

from __future__ import annotations

import asyncio

from agno.agent import Agent
from agno.context.calendar import GoogleCalendarContextProvider
from agno.models.openai import OpenAIResponses


async def demo_read_only():
    print("Demo 1: Read-Only Calendar Access\n")

    calendar = GoogleCalendarContextProvider(
        model=OpenAIResponses(id="gpt-5.4-mini"),
        read=True,
        write=False,
    )

    agent = Agent(
        model=OpenAIResponses(id="gpt-5.4"),
        tools=calendar.get_tools(),
        instructions=calendar.instructions(),
        markdown=True,
    )

    await agent.aprint_response(
        "What meetings do I have this week? "
        "For each meeting, tell me the day, time, title, and who's attending. "
        "Highlight any conflicts or back-to-back meetings.",
        stream=True,
    )


async def demo_read_write():
    print("\nDemo 2: Read-Write Calendar Access\n")

    calendar = GoogleCalendarContextProvider(
        model=OpenAIResponses(id="gpt-5.4-mini"),
        read=True,
        write=True,
    )

    agent = Agent(
        model=OpenAIResponses(id="gpt-5.4"),
        tools=calendar.get_tools(),
        instructions=calendar.instructions(),
        markdown=True,
    )

    await agent.aprint_response(
        "Find a 30-minute slot tomorrow afternoon when I'm free, "
        "and create a meeting called 'Weekly Planning' at that time.",
        stream=True,
    )


async def main():
    await demo_read_only()
    await demo_read_write()


if __name__ == "__main__":
    asyncio.run(main())
