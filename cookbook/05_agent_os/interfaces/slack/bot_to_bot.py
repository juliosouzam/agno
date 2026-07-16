"""
Bot-to-Bot Team Communication
=============================

Demonstrates a Team with multiple Slack bots as members. The team
coordinates two bots where one delegates tasks to the other.

Architecture:
  - Team (via Slack Agent interface): Receives user requests, coordinates
  - Dash (member): Specialist bot that handles delegated tasks

Setup:
  1. Two Slack apps in the same workspace:
     - Slack Agent: SLACK_AGENT_BOT_TOKEN, SLACK_AGENT_SIGNING_SECRET
     - Dash: DASH_BOT_TOKEN, DASH_SIGNING_SECRET
  2. Configure Event Subscriptions:
     - Slack Agent -> .../slack/slack-agent/events
     - Dash -> .../slack/dash/events
  3. ngrok: ngrok http 7777

Flow:
  1. User @mentions Slack Agent with a task
  2. Team delegates to Dash using <@USER_ID> mention format
  3. Dash (respond_to_bot_messages=True) receives the bot message
  4. Dash responds in thread

Mention Format: Use <@USER_ID> for real @mentions (e.g., <@U0AQXMJ3FUP>).
"""

from os import getenv

from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.os.app import AgentOS
from agno.os.interfaces.slack import Slack
from agno.team import Team
from agno.tools.duckduckgo import DuckDuckGoTools

# Dash's Slack user ID for proper @mentions
DASH_USER_ID = "U0AQXMJ3FUP"

# ---------------------------------------------------------------------------
# Team Member: Dash (Specialist)
# ---------------------------------------------------------------------------

dash = Agent(
    name="Dash",
    model=OpenAIResponses(id="gpt-4.1-mini"),
    tools=[DuckDuckGoTools()],
    instructions=[
        "You are Dash, a specialist assistant.",
        "Complete tasks directly and concisely.",
        "Search for information if needed.",
    ],
    markdown=True,
)

# ---------------------------------------------------------------------------
# The Team
# ---------------------------------------------------------------------------

team = Team(
    name="Research Team",
    model=OpenAIResponses(id="gpt-4.1-mini"),
    members=[dash],
    instructions=[
        "You are a team coordinator.",
        f"To delegate tasks to Dash, use the Slack mention format: <@{DASH_USER_ID}>",
        f"Example: '<@{DASH_USER_ID}> please help with: [task]'",
        "Always delegate - do not answer questions yourself.",
    ],
    markdown=True,
    show_members_responses=True,
)

# ---------------------------------------------------------------------------
# AgentOS with Slack interfaces
# ---------------------------------------------------------------------------

agent_os = AgentOS(
    teams=[team],
    agents=[dash],
    interfaces=[
        # Team interface - receives user messages
        Slack(
            team=team,
            prefix="/slack/slack-agent",
            token=getenv("SLACK_AGENT_BOT_TOKEN"),
            signing_secret=getenv("SLACK_AGENT_SIGNING_SECRET"),
            reply_to_mentions_only=True,
            respond_to_bot_messages=False,
        ),
        # Dash interface - receives delegated tasks from team
        Slack(
            agent=dash,
            prefix="/slack/dash",
            token=getenv("DASH_BOT_TOKEN"),
            signing_secret=getenv("DASH_SIGNING_SECRET"),
            reply_to_mentions_only=True,
            respond_to_bot_messages=True,
        ),
    ],
)
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="bot_to_bot:app", reload=True)
