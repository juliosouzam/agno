"""Basic durable agent with DBOS.

`DBOSAgent` wraps a normal Agno agent so that:
  - `run` / `arun` execute as DBOS workflows (checkpointed, crash-recoverable), and
  - every model request and tool call executes as a DBOS step.

Run:
    pip install "agno[dbos]"
    export OPENAI_API_KEY=...
    python cookbook/integrations/dbos/01_basic_durable_agent.py

DBOS uses SQLite here for a zero-setup demo. Use Postgres in production by pointing
`system_database_url` at your Postgres instance.
"""

from agno.agent import Agent
from agno.integrations.dbos import DBOSAgent
from agno.models.openai import OpenAIResponses
from dbos import DBOS

# 1. Configure DBOS (must happen before wrapping and before DBOS.launch()).
DBOS(
    config={"name": "agno-basic", "system_database_url": "sqlite:///dbos_basic.sqlite"}
)

# 2. A normal Agno agent. A stable `name` is REQUIRED for durable identity.
agent = Agent(
    model=OpenAIResponses(id="gpt-5.5"),
    name="geography-agent",
    instructions="You are an expert in geography. Answer concisely.",
)

# 3. Wrap it. Must be constructed BEFORE DBOS.launch() so DBOS can register the
#    workflow and step functions for crash recovery.
dbos_agent = DBOSAgent(agent)

if __name__ == "__main__":
    # 4. Launch DBOS, then run. The run is a durable workflow; the model call is a step.
    DBOS.launch()

    result = dbos_agent.run("What is the capital of Mexico?")
    print(result.content)
