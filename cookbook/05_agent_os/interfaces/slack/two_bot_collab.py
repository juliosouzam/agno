"""
Two-Bot Collaboration Demo
==========================

Demonstrates two bots working together in Slack:
  - Slack Agent (Orchestrator): Receives user requests, delegates to specialist
  - Mustafa's Context (Specialist): Handles delegated research tasks

Both bots share the same ngrok tunnel with different webhook paths.

Setup:
  1. Configure both Slack apps' Event Subscriptions:
       Slack Agent: https://<ngrok>/slack/orchestrator
       Mustafa's Context: https://<ngrok>/slack/specialist
  2. Environment variables:
       SLACK_TOKEN (Slack Agent)
       SLACK_SIGNING_SECRET (Slack Agent)
       CONTEXT_BOT_TOKEN (Mustafa's Context)
       CONTEXT_SIGNING_SECRET (Mustafa's Context)
  3. Run: ngrok http 7777
  4. Run this cookbook

Flow:
  User: @Slack Agent research Python async patterns
  Slack Agent: I'll delegate this. @mustafa_s_context please research...
  mustafa_s_context: Here's what I found about Python async patterns...
"""

from os import getenv

from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.os.app import AgentOS
from agno.os.interfaces.slack import Slack
from agno.tools.duckduckgo import DuckDuckGoTools

# Mustafa's Context bot user ID for @mentions
SPECIALIST_USER_ID = "U0AS83RUHS4"  # mustafa_s_context

orchestrator = Agent(
    name="Orchestrator",
    model=OpenAIResponses(id="gpt-4.1-mini"),
    instructions=[
        "You are a coordinator that delegates research tasks to a specialist.",
        f"When asked to research something, @mention the specialist: <@{SPECIALIST_USER_ID}>",
        f"Format: 'I'll ask our research specialist. <@{SPECIALIST_USER_ID}> please research: <task>'",
        "For simple greetings or non-research tasks, respond directly.",
    ],
    markdown=True,
)

specialist = Agent(
    name="Research Specialist",
    model=OpenAIResponses(id="gpt-4.1-mini"),
    tools=[DuckDuckGoTools()],
    instructions=[
        "You are a research specialist that gathers information.",
        "When @mentioned by the Orchestrator, research the requested topic.",
        "Provide concise, factual responses with sources when available.",
        "Only respond when @mentioned - ignore other messages.",
    ],
    markdown=True,
)

# Check if we have the specialist's credentials
specialist_token = getenv("CONTEXT_BOT_TOKEN")
specialist_secret = getenv("CONTEXT_SIGNING_SECRET")

interfaces = [
    # Orchestrator (Slack Agent) - receives user @mentions
    Slack(
        agent=orchestrator,
        token=getenv("SLACK_TOKEN"),
        signing_secret=getenv("SLACK_SIGNING_SECRET"),
        prefix="/slack/orchestrator",
        streaming=False,
        reply_to_mentions_only=True,
        respond_to_bot_messages=True,
    ),
]

# Only add specialist if we have its credentials
if specialist_token and specialist_secret:
    interfaces.append(
        Slack(
            agent=specialist,
            token=specialist_token,
            signing_secret=specialist_secret,
            prefix="/slack/specialist",
            streaming=False,
            reply_to_mentions_only=True,
            respond_to_bot_messages=True,
        )
    )
else:
    print("\nWARNING: CONTEXT_BOT_TOKEN or CONTEXT_SIGNING_SECRET not set")
    print("Only the orchestrator will be available.")
    print("To enable two-bot collaboration:")
    print("  export CONTEXT_BOT_TOKEN=xoxb-...")
    print("  export CONTEXT_SIGNING_SECRET=...")
    print("  Configure Mustafa's Context Event Subscription URL:")
    print("    https://<ngrok>/slack/specialist\n")

agent_os = AgentOS(
    agents=[orchestrator, specialist]
    if (specialist_token and specialist_secret)
    else [orchestrator],
    interfaces=interfaces,
)
app = agent_os.get_app()


if __name__ == "__main__":
    print("\n=== Two-Bot Collaboration Demo ===")
    print("Orchestrator: Slack Agent (SLACK_TOKEN)")
    specialist_status = (
        "ENABLED" if (specialist_token and specialist_secret) else "DISABLED"
    )
    print(f"Specialist: Mustafa's Context (CONTEXT_BOT_TOKEN) - {specialist_status}")
    print("\nWebhook paths:")
    print("  Slack Agent: https://<ngrok>/slack/orchestrator")
    if specialist_token and specialist_secret:
        print("  Mustafa's Context: https://<ngrok>/slack/specialist")
    print()

    agent_os.serve(app="two_bot_collab:app", reload=True, port=7777)
