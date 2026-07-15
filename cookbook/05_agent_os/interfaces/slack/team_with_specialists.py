"""
Team with Named Specialists in Slack
====================================

A single Slack bot backed by an Agno Team. The orchestrator delegates to
specialist agents internally, and their names appear in responses.

This is simpler than multi_bot_team.py (only 1 Slack app needed) while
still showing which specialist handled each part of the request.

Setup:
  1. One Slack app with Event Subscriptions
  2. Environment variables:
       SLACK_TOKEN
       SLACK_SIGNING_SECRET
  3. ngrok: ngrok http 7777

User sees:
  User: @TeamBot What's the weather and write me a haiku about it?

  TeamBot: I'll coordinate this request.

  [Research Agent]: The weather in NYC is 72F, partly cloudy...

  [Creative Agent]: Here's a haiku:
    Clouds drift lazily
    Warm breeze through the city streets
    Summer afternoon
"""

from os import getenv

from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.os.app import AgentOS
from agno.os.interfaces.slack import Slack
from agno.team import Team
from agno.tools.duckduckgo import DuckDuckGoTools

# Specialist agents
researcher = Agent(
    name="Research Agent",
    role="Information gatherer",
    model=OpenAIResponses(id="gpt-4.1-mini"),
    tools=[DuckDuckGoTools()],
    instructions=[
        "You research and gather factual information.",
        "Always cite your sources.",
        "Be concise but thorough.",
    ],
)

creative = Agent(
    name="Creative Agent",
    role="Creative writer",
    model=OpenAIResponses(id="gpt-4.1-mini"),
    instructions=[
        "You handle creative writing tasks: poems, stories, slogans.",
        "Be creative and engaging.",
        "Match the requested style or format.",
    ],
)

coder = Agent(
    name="Code Agent",
    role="Programming expert",
    model=OpenAIResponses(id="gpt-4.1-mini"),
    instructions=[
        "You write and explain code.",
        "Provide working examples with comments.",
        "Mention best practices and potential issues.",
    ],
)

# Team with coordinator mode
team = Team(
    name="Specialist Team",
    mode="coordinate",
    model=OpenAIResponses(id="gpt-4.1"),
    members=[researcher, creative, coder],
    instructions=[
        "You coordinate a team of specialists.",
        "Analyze each request and delegate to the right specialist(s).",
        "For multi-part requests, delegate each part appropriately.",
        "Combine responses into a coherent answer.",
    ],
    markdown=True,
)

agent_os = AgentOS(
    teams=[team],
    interfaces=[
        Slack(
            team=team,
            token=getenv("SLACK_TOKEN"),
            signing_secret=getenv("SLACK_SIGNING_SECRET"),
            streaming=True,
            reply_to_mentions_only=True,
        ),
    ],
)
app = agent_os.get_app()


if __name__ == "__main__":
    agent_os.serve(app="team_with_specialists:app", reload=True)
