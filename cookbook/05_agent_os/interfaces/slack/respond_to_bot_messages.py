"""
Bot-to-Bot Communication
========================
Demonstrates how a Slack bot can receive messages from other bots
using the `respond_to_bot_messages` flag.

Use case: Bot orchestrator delegates tasks to specialist bots.

Setup:
  1. Slack app with bot token and signing secret
  2. Event Subscriptions URL: https://<tunnel>/slack/dash/events
  3. Environment: SLACK_DASH_BOT_TOKEN, SLACK_DASH_SIGNING_SECRET
  4. ngrok: ngrok http 7777

Key flag: respond_to_bot_messages=True
  - Default (False): All bot messages are dropped
  - True: Only own messages dropped (echo guard), peer bot messages processed
"""

from os import getenv

from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.os.app import AgentOS
from agno.os.interfaces.slack import Slack
from agno.tools.duckduckgo import DuckDuckGoTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    name="Bot Receiver",
    model=OpenAIResponses(id="gpt-4.1-mini"),
    tools=[DuckDuckGoTools()],
    instructions=["You receive messages from other bots and respond helpfully."],
    markdown=True,
)

# ---------------------------------------------------------------------------
# AgentOS with Slack
# ---------------------------------------------------------------------------

agent_os = AgentOS(
    agents=[agent],
    interfaces=[
        Slack(
            agent=agent,
            prefix="/slack/dash",
            token=getenv("SLACK_DASH_BOT_TOKEN"),
            signing_secret=getenv("SLACK_DASH_SIGNING_SECRET"),
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
    agent_os.serve(app="respond_to_bot_messages:app", reload=True)
