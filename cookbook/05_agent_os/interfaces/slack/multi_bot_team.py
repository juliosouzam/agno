"""
Multi-Bot Team in Slack
=======================

Demonstrates multiple specialized bots collaborating in Slack threads.
Each bot is a separate Slack app with its own identity, creating visible
collaboration where users can see which specialist is working.

Architecture:
  - Orchestrator Bot: Receives user requests, delegates to specialists
  - Research Bot: Handles web search and information gathering
  - Code Bot: Handles code-related tasks

Setup:
  1. Create 3 Slack apps in the same workspace
  2. Set environment variables for each:
       ORCHESTRATOR_TOKEN, ORCHESTRATOR_SIGNING_SECRET
       RESEARCH_BOT_TOKEN, RESEARCH_BOT_SIGNING_SECRET
       CODE_BOT_TOKEN, CODE_BOT_SIGNING_SECRET
  3. Each bot needs Event Subscriptions to different endpoints:
       Orchestrator: https://<tunnel>/slack/orchestrator
       Research Bot: https://<tunnel>/slack/research
       Code Bot: https://<tunnel>/slack/code
  4. All bots must have respond_to_bot_messages=True to hear each other

Flow:
  1. User @mentions Orchestrator: "Search for Python async patterns"
  2. Orchestrator analyzes and @mentions Research Bot in thread
  3. Research Bot responds with findings in same thread
  4. User sees the full collaboration unfold

Loop Safety:
  - Each bot has echo guard (ignores own messages)
  - Orchestrator only delegates once per user request
  - Specialists only respond to Orchestrator mentions, not each other
"""

from os import getenv

from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.os.app import AgentOS
from agno.os.interfaces.slack import Slack
from agno.tools.duckduckgo import DuckDuckGoTools

# Orchestrator: Routes requests to appropriate specialists
orchestrator = Agent(
    name="Orchestrator",
    model=OpenAIResponses(id="gpt-4.1-mini"),
    instructions=[
        "You are a team coordinator that delegates tasks to specialists.",
        "When you receive a request:",
        "  - For research/search tasks: @mention Research Bot and ask it to help",
        "  - For code tasks: @mention Code Bot and ask it to help",
        "  - For general questions: Answer directly",
        "Always explain what you're doing before delegating.",
        "Format: 'I'll ask our research specialist to help. @Research Bot: <task>'",
    ],
    markdown=True,
)

# Research Bot: Handles information gathering
research_bot = Agent(
    name="Research Bot",
    model=OpenAIResponses(id="gpt-4.1-mini"),
    tools=[DuckDuckGoTools()],
    instructions=[
        "You are a research specialist.",
        "When the Orchestrator asks you to research something:",
        "  1. Search for relevant information",
        "  2. Summarize findings concisely",
        "  3. Cite sources when available",
        "Only respond when @mentioned by the Orchestrator.",
    ],
    markdown=True,
)

# Code Bot: Handles code-related tasks
code_bot = Agent(
    name="Code Bot",
    model=OpenAIResponses(id="gpt-4.1-mini"),
    instructions=[
        "You are a coding specialist.",
        "When the Orchestrator asks you about code:",
        "  1. Provide clear, well-commented examples",
        "  2. Explain the approach",
        "  3. Mention any gotchas or best practices",
        "Only respond when @mentioned by the Orchestrator.",
    ],
    markdown=True,
)

# Each bot needs its own Slack interface with different credentials
agent_os = AgentOS(
    agents=[orchestrator, research_bot, code_bot],
    interfaces=[
        # Orchestrator receives all user mentions
        Slack(
            agent=orchestrator,
            token=getenv("ORCHESTRATOR_TOKEN"),
            signing_secret=getenv("ORCHESTRATOR_SIGNING_SECRET"),
            path="/slack/orchestrator",
            streaming=False,
            reply_to_mentions_only=True,
            respond_to_bot_messages=True,
        ),
        # Research Bot responds to Orchestrator's @mentions
        Slack(
            agent=research_bot,
            token=getenv("RESEARCH_BOT_TOKEN"),
            signing_secret=getenv("RESEARCH_BOT_SIGNING_SECRET"),
            path="/slack/research",
            streaming=False,
            reply_to_mentions_only=True,
            respond_to_bot_messages=True,
        ),
        # Code Bot responds to Orchestrator's @mentions
        Slack(
            agent=code_bot,
            token=getenv("CODE_BOT_TOKEN"),
            signing_secret=getenv("CODE_BOT_SIGNING_SECRET"),
            path="/slack/code",
            streaming=False,
            reply_to_mentions_only=True,
            respond_to_bot_messages=True,
        ),
    ],
)
app = agent_os.get_app()


if __name__ == "__main__":
    # Verify all tokens are set
    required = [
        "ORCHESTRATOR_TOKEN",
        "ORCHESTRATOR_SIGNING_SECRET",
        "RESEARCH_BOT_TOKEN",
        "RESEARCH_BOT_SIGNING_SECRET",
        "CODE_BOT_TOKEN",
        "CODE_BOT_SIGNING_SECRET",
    ]
    missing = [k for k in required if not getenv(k)]
    if missing:
        print("Missing environment variables:")
        for k in missing:
            print(f"  - {k}")
        print("\nSet these and restart.")
    else:
        agent_os.serve(app="multi_bot_team:app", reload=True)
