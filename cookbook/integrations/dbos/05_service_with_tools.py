"""Durable Agno service with tool calls and many runs — rich Conductor observability.

This long-running FastAPI service exposes a weather agent that uses tools, so each run
produces MULTIPLE durable steps (model calls + tool calls) visible in the Conductor UI.
A /demo endpoint fires several runs at once so the dashboard fills with workflows.

Setup:
    pip install "agno[dbos]" fastapi uvicorn
    export OPENAI_API_KEY=...
    export DBOS_CONDUCTOR_KEY=...      # key for the app named below, from the Conductor console
    python cookbook/integrations/dbos/05_service_with_tools.py

Try it:
    # one run with tool calls
    curl -X POST localhost:8000/ask -H 'content-type: application/json' \
        -d '{"question": "Compare the weather in Tokyo and Paris, then convert Tokyo to Fahrenheit."}'

    # fire a batch of runs to populate the dashboard
    curl -X POST localhost:8000/demo

In the Conductor console (app below), each run appears under Workflows. Click a workflow
and "Show Workflow Steps" to see the model steps and tool steps
(agno.weather-agent.model.gpt-5.5, agno.weather-agent.tool.get_weather, ...).

IMPORTANT: the `name` here must match an app registered in your Conductor console, and
DBOS_CONDUCTOR_KEY must be that app's key — otherwise you get HTTP 403 on connect.
"""

import os

from dbos import DBOS
from fastapi import FastAPI
from pydantic import BaseModel

from agno.agent import Agent
from agno.integrations.dbos import DBOSAgent
from agno.models.openai import OpenAIResponses

app = FastAPI()

DBOS(
    fastapi=app,
    config={
        "name": "agno-basic",  # match your registered Conductor app
        "system_database_url": "sqlite:///dbos_tools_service.sqlite",
        "conductor_key": os.environ.get("DBOS_CONDUCTOR_KEY"),
    },
)


# --- Tools: each call becomes its own durable DBOS step ---
def get_weather(city: str) -> str:
    """Get the current weather for a city, in Celsius."""
    fake = {"Tokyo": 22, "Paris": 17, "New York": 19, "Cairo": 33}
    temp = fake.get(city, 20)
    return f"The weather in {city} is clear, {temp} degrees Celsius."


def celsius_to_fahrenheit(celsius: float) -> str:
    """Convert a temperature in Celsius to Fahrenheit."""
    return f"{celsius} C is {celsius * 9 / 5 + 32:.1f} F."


agent = Agent(
    model=OpenAIResponses(id="gpt-5.5"),
    name="weather-agent",
    tools=[get_weather, celsius_to_fahrenheit],
    instructions="Use the tools to answer weather questions. Be concise.",
)
dbos_agent = DBOSAgent(agent)


class AskRequest(BaseModel):
    question: str


@app.post("/ask")
def ask(req: AskRequest):
    """One durable run. Watch its model + tool steps in Conductor."""
    result = dbos_agent.run(req.question)
    return {"answer": result.content, "workflow_id": DBOS.workflow_id}


DEMO_QUESTIONS = [
    "What's the weather in Tokyo?",
    "What's the weather in Paris, in Fahrenheit?",
    "Compare the weather in New York and Cairo.",
    "Is it warmer in Tokyo or Paris right now?",
    "Give me Cairo's weather converted to Fahrenheit.",
]


@app.post("/demo")
def demo():
    """Fire several durable runs to populate the Conductor dashboard."""
    results = []
    for question in DEMO_QUESTIONS:
        result = dbos_agent.run(question)
        results.append({"question": question, "answer": result.content})
    return {"runs": len(results), "results": results}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
