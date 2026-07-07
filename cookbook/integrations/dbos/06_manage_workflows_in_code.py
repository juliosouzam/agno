"""Manage durable agent runs directly in code — no Conductor console needed.

Everything the Conductor UI does (list, inspect steps, resume, fork, cancel) is a plain
DBOS API you can call from Python. The durability guarantee itself is in the engine and
needs neither the console nor these calls — but these let you build your own ops surface.

This example uses a fake model (no API key). It:
  1. runs a durable agent,
  2. lists workflows and prints each one's steps,
  3. forks a completed run from a step (the code equivalent of the console's Fork),
  4. shows how you'd resume an interrupted run.

Run:
    python cookbook/integrations/dbos/06_manage_workflows_in_code.py
"""

from dbos import DBOS, SetWorkflowID

from agno.agent import Agent
from agno.integrations.dbos import DBOSAgent
from agno.models.base import Model
from agno.models.response import ModelResponse


def get_weather(city: str) -> str:
    """Weather for a city."""
    return f"{city}: 21 C, clear."


class FakeModel(Model):
    def invoke(self, *args, **kwargs) -> ModelResponse:
        messages = kwargs.get("messages", [])
        if not any(getattr(m, "role", None) == "tool" for m in messages):
            return ModelResponse(
                role="assistant",
                content=None,
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "Tokyo"}',
                        },
                    }
                ],
            )
        return ModelResponse(role="assistant", content="It is 21 C and clear in Tokyo.")

    async def ainvoke(self, *args, **kwargs) -> ModelResponse:
        return self.invoke(*args, **kwargs)

    def invoke_stream(self, *args, **kwargs):
        raise NotImplementedError

    def ainvoke_stream(self, *args, **kwargs):
        raise NotImplementedError

    def _parse_provider_response(self, response, **kwargs):
        return response

    def _parse_provider_response_delta(self, delta):
        return delta


DBOS(
    config={
        "name": "agno-manage",
        "system_database_url": "sqlite:///dbos_manage.sqlite",
    }
)

agent = Agent(
    model=FakeModel(id="fake"), name="ops-agent", tools=[get_weather], telemetry=False
)
dbos_agent = DBOSAgent(agent)


if __name__ == "__main__":
    DBOS.launch()

    # 1. Run a durable agent with a known workflow id.
    with SetWorkflowID("weather-run-1"):
        result = dbos_agent.run("What's the weather in Tokyo?")
    print("Answer:", result.content)

    # 2. List workflows for this agent (the console's Workflows table, in code).
    print("\n--- Workflows ---")
    for wf in DBOS.list_workflows(name="agno.ops-agent.run"):
        print(f"  {wf.workflow_id}  status={wf.status}")

    # 3. Inspect the steps of a workflow (the console's step waterfall, in code).
    print("\n--- Steps of weather-run-1 ---")
    for step in DBOS.list_workflow_steps("weather-run-1"):
        print(f"  #{step['function_id']}  {step['function_name']}")

    # 4. Fork the run from step 1 (the console's Fork). Reuses checkpoints before
    #    the fork point and re-executes from there under a NEW workflow id.
    print("\n--- Fork from step 1 ---")
    handle = DBOS.fork_workflow("weather-run-1", start_step=1)
    forked = handle.get_result()
    print(f"  forked workflow id: {handle.workflow_id}")
    print(f"  forked answer: {forked.content}")

    # 5. Resume: for a workflow that was interrupted/cancelled, this re-drives it from
    #    its last completed step. (No-op here since our run already SUCCEEDED — shown
    #    for reference; it is the code equivalent of the console's Resume button.)
    #    DBOS.resume_workflow("some-interrupted-workflow-id")

    # 6. Get a single workflow's status.
    status = DBOS.get_workflow_status("weather-run-1")
    print(f"\nweather-run-1 final status: {status.status if status else 'unknown'}")
