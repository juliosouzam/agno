"""Durable tools with DBOS.

By default DBOSAgent auto-wraps every tool call as a durable DBOS step, so a tool's
result is checkpointed and not recomputed on recovery. This example shows:
  - a normal tool that becomes a durable step automatically,
  - excluding a tool from durability with `non_durable_tools`,
  - per-tool retry configuration with `tool_step_config`.

Run:
    pip install "agno[dbos]"
    export OPENAI_API_KEY=...
    python cookbook/integrations/dbos/02_durable_tools.py
"""

from agno.agent import Agent
from agno.integrations.dbos import DBOSAgent, DBOSStepConfig
from agno.models.openai import OpenAIResponses
from dbos import DBOS

DBOS(
    config={"name": "agno-tools", "system_database_url": "sqlite:///dbos_tools.sqlite"}
)


def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    return f"The weather in {city} is sunny, 24 degrees Celsius."


def log_event(message: str) -> str:
    """Write a log line. Cheap and side-effect-free; no need to checkpoint it."""
    print(f"[log] {message}")
    return "logged"


agent = Agent(
    model=OpenAIResponses(id="gpt-5.5"),
    name="weather-agent",
    tools=[get_weather, log_event],
    instructions="Use the tools to answer weather questions, and log what you did.",
)

# get_weather is auto-wrapped as a durable step with 3 retries; log_event is excluded.
dbos_agent = DBOSAgent(
    agent,
    non_durable_tools=["log_event"],
    tool_step_config={
        "get_weather": DBOSStepConfig(retries_allowed=True, max_attempts=3),
    },
)

if __name__ == "__main__":
    DBOS.launch()

    result = dbos_agent.run("What's the weather in Tokyo? Log that you checked it.")
    print(result.content)
