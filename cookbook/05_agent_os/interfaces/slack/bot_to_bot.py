"""
Bot-to-Bot Communication
========================

Demonstrates how one Slack bot can receive and respond to messages from
another bot using the `respond_to_bot_messages` flag.

Use case: Bot A (orchestrator) can delegate tasks to Bot B (specialist).

Setup:
  1. Two Slack apps installed to the same workspace:
     - Receiver Bot: receives messages from other bots
     - Sender Bot: sends messages (can be any bot in the workspace)
  2. Event Subscription URL for Receiver:
       https://<tunnel>/slack/events
  3. Environment variables:
       SLACK_TOKEN (Receiver bot token)
       SLACK_SIGNING_SECRET (Receiver signing secret)
  4. ngrok: ngrok http 7777

Key flag: `respond_to_bot_messages=True`
  - Default (False): All bot messages are dropped (standard behavior)
  - True: Only the bot's OWN messages are dropped (echo guard),
          messages from OTHER bots are processed normally

Loop safety:
  - The echo guard prevents infinite loops from own messages
  - With reply_to_mentions_only=True, only mentions/DMs are processed
  - WARNING: Two bots with reply_to_mentions_only=False +
    respond_to_bot_messages=True in a shared channel WILL loop
"""

from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.os.app import AgentOS
from agno.os.interfaces.slack import Slack

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

receiver_agent = Agent(
    name="Bot Receiver",
    model=OpenAIResponses(id="gpt-4.1-mini"),
    instructions=[
        "You are a specialist bot that receives tasks from other bots.",
        "When another bot sends you a message, acknowledge it and respond helpfully.",
        "Always mention that you received a message from a peer bot.",
    ],
    markdown=True,
)

agent_os = AgentOS(
    agents=[receiver_agent],
    interfaces=[
        Slack(
            agent=receiver_agent,
            reply_to_mentions_only=True,
            respond_to_bot_messages=True,
        ),
    ],
)
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="bot_to_bot:app", reload=True)
