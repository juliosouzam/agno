"""Durable background runs and queues — all in code, no console.

DBOS.start_workflow launches a durable run in the background and hands you a handle to
await later. A DBOS Queue fans many runs out with controlled concurrency. Both survive a
crash: on restart, in-flight runs resume from their last checkpoint.

This example uses a fake model (no API key). It:
  1. starts a run in the background and retrieves its result via a handle,
  2. enqueues several runs on a concurrency-limited queue and collects all results.

Run:
    python cookbook/integrations/dbos/07_background_and_queues.py
"""

from dbos import DBOS, Queue

from agno.agent import Agent
from agno.integrations.dbos import DBOSAgent
from agno.models.base import Model
from agno.models.response import ModelResponse


class FakeModel(Model):
    def invoke(self, *args, **kwargs) -> ModelResponse:
        # Echo the last user message so each run has a distinct answer.
        messages = kwargs.get("messages", [])
        last_user = next(
            (
                str(getattr(m, "content", ""))
                for m in reversed(messages)
                if getattr(m, "role", None) == "user"
            ),
            "",
        )
        return ModelResponse(role="assistant", content=f"Answered: {last_user}")

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
        "name": "agno-queues",
        "system_database_url": "sqlite:///dbos_queues.sqlite",
    }
)

agent = Agent(model=FakeModel(id="fake"), name="batch-agent", telemetry=False)
dbos_agent = DBOSAgent(agent)

# A queue that runs at most 2 durable agent runs concurrently.
queue = Queue("agent-runs", concurrency=2)


if __name__ == "__main__":
    DBOS.launch()

    # 1. Background run: start it, do other work, then await the result via the handle.
    print("--- Background run ---")
    handle = DBOS.start_workflow(dbos_agent.run_workflow, "What is 2 + 2?")
    print("  started; workflow id:", handle.workflow_id)
    print("  result:", handle.get_result().content)

    # 2. Fan out several runs on the queue and collect all results.
    print("\n--- Queue fan-out ---")
    questions = [f"Question {i}?" for i in range(5)]
    handles = [queue.enqueue(dbos_agent.run_workflow, q) for q in questions]
    for h in handles:
        print("  ", h.get_result().content)
