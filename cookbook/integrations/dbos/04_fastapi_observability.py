"""Durable Agno agent as a long-running FastAPI service (Conductor-observable).

A batch script exits as soon as it finishes, so DBOS Conductor marks its executor
"Dead". To watch runs live in the Conductor UI (workflow list, per-step graph, status),
the process must stay alive — a web server does exactly that, and it keeps heartbeating
to Conductor so the app shows as "Available".

Setup:
    pip install "agno[dbos]" fastapi uvicorn
    export OPENAI_API_KEY=...
    export DBOS_CONDUCTOR_KEY=...      # from the DBOS Conductor console (Settings > API Keys)
    python cookbook/integrations/dbos/04_fastapi_observability.py

Then:
    curl -X POST localhost:8000/ask -H 'content-type: application/json' \
        -d '{"question": "What is the capital of Mexico?"}'

Open the Conductor console: the app "agno-observability" now shows Available, and each
/ask call appears under Workflows with its model/tool steps and status.
"""

import os

from dbos import DBOS
from fastapi import FastAPI
from pydantic import BaseModel

from agno.agent import Agent
from agno.integrations.dbos import DBOSAgent
from agno.models.openai import OpenAIResponses

app = FastAPI()

# Passing `app` registers DBOS's lifecycle with FastAPI, so DBOS launches when the
# server starts and stays connected to Conductor for the life of the process.
DBOS(
    fastapi=app,
    config={
        "name": "agno-observability",
        "system_database_url": "sqlite:///dbos_observability.sqlite",
        "conductor_key": os.environ.get("DBOS_CONDUCTOR_KEY"),
    },
)

agent = Agent(
    model=OpenAIResponses(id="gpt-5.5"),
    name="observability-agent",
    instructions="You are an expert in geography. Answer concisely.",
)
dbos_agent = DBOSAgent(agent)


class AskRequest(BaseModel):
    question: str


@app.post("/ask")
def ask(req: AskRequest):
    # Each call runs as a durable DBOS workflow and shows up in the Conductor UI.
    result = dbos_agent.run(req.question)
    return {"answer": result.content, "workflow_id": DBOS.workflow_id}


if __name__ == "__main__":
    import uvicorn

    # No DBOS.launch() here — FastAPI integration launches DBOS on startup.
    uvicorn.run(app, host="0.0.0.0", port=8000)
