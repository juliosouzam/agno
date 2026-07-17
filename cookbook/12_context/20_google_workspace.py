"""
Google Workspace Multi-Provider
Combines GDrive, Gmail, and Calendar for cross-service workflows.
Requires: OPENAI_API_KEY + Google OAuth or Service Account credentials
"""

from __future__ import annotations

import asyncio

from agno.agent import Agent
from agno.context.calendar import GoogleCalendarContextProvider
from agno.context.gdrive import GoogleDriveContextProvider
from agno.context.gmail import GmailContextProvider
from agno.models.openai import OpenAIResponses

sub_model = OpenAIResponses(id="gpt-5.4-mini")

gdrive = GoogleDriveContextProvider(model=sub_model)
gmail = GmailContextProvider(model=sub_model, read=True, write=True)
calendar = GoogleCalendarContextProvider(model=sub_model, read=True, write=True)

all_tools = gdrive.get_tools() + gmail.get_tools() + calendar.get_tools()
combined_instructions = "\n\n".join(
    [
        gdrive.instructions(),
        gmail.instructions(),
        calendar.instructions(),
    ]
)

agent = Agent(
    model=OpenAIResponses(id="gpt-5.4"),
    tools=all_tools,
    instructions=combined_instructions,
    markdown=True,
)


async def demo_meeting_prep():
    print("Demo 1: Meeting Preparation Workflow\n")

    await agent.aprint_response(
        "Help me prepare for my next meeting. "
        "Find the meeting on my calendar, then search for any recent emails "
        "from the attendees, and look for related documents in Google Drive. "
        "Give me a briefing with the key context I need.",
        stream=True,
    )


async def demo_follow_up():
    print("\nDemo 2: Follow-Up Workflow\n")

    await agent.aprint_response(
        "What needs my attention today? "
        "Check my unread emails and today's calendar. "
        "For any meeting that just happened, draft a follow-up email "
        "summarizing action items if the email thread suggests there were any.",
        stream=True,
    )


async def demo_morning_briefing():
    print("\nDemo 3: Morning Briefing\n")

    await agent.aprint_response(
        "Give me a quick morning briefing: "
        "What meetings do I have today? "
        "Any urgent unread emails? "
        "Any recently shared documents I should review?",
        stream=True,
    )


async def main():
    await demo_meeting_prep()
    await demo_follow_up()
    await demo_morning_briefing()


if __name__ == "__main__":
    asyncio.run(main())
