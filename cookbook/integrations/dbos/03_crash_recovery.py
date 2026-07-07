"""Crash recovery with DBOS — no API key required.

This is the clearest way to SEE durable execution work. It uses a fake model (so no
LLM API key is needed) that counts how many times it is really invoked. The agent runs
a tool that crashes the process the first time. On the second run, DBOS recovers the
same workflow and resumes from the last completed step: the model call already made is
NOT repeated.

Run it TWICE:
    python cookbook/integrations/dbos/03_crash_recovery.py    # crashes mid-run
    python cookbook/integrations/dbos/03_crash_recovery.py    # recovers and finishes

Expected: the first model call is invoked exactly once across BOTH runs, proving the
completed model step was checkpointed and replayed from DBOS instead of re-executed.

Delete dbos_recovery.sqlite and counters/ to reset the demo.
"""

import os

from dbos import DBOS, SetWorkflowID

from agno.agent import Agent
from agno.integrations.dbos import DBOSAgent
from agno.models.base import Model
from agno.models.response import ModelResponse

COUNTER_DIR = "counters"
os.makedirs(COUNTER_DIR, exist_ok=True)
INVOKE_COUNT = os.path.join(COUNTER_DIR, "invoke_count.txt")
CRASH_FLAG = os.path.join(COUNTER_DIR, "crashed_once.txt")


def _bump(path: str) -> int:
    n = int(open(path).read() or "0") if os.path.exists(path) else 0
    n += 1
    open(path, "w").write(str(n))
    return n


def note_capital(city: str) -> str:
    """Record the capital. Crashes the process the FIRST time (after the model step)."""
    if not os.path.exists(CRASH_FLAG):
        open(CRASH_FLAG, "w").write("1")
        print("  Simulating a crash now (the model step is already checkpointed)...")
        os._exit(137)
    return f"noted: {city}"


class CountingFakeModel(Model):
    """A fake model that records real provider calls so we can prove exactly-once."""

    def invoke(self, *args, **kwargs) -> ModelResponse:
        n = _bump(INVOKE_COUNT)
        print(f"  REAL model invoke #{n}")
        if n == 1:
            return ModelResponse(
                role="assistant",
                content=None,
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "note_capital",
                            "arguments": '{"city": "Mexico City"}',
                        },
                    }
                ],
            )
        return ModelResponse(
            role="assistant", content="The capital of Mexico is Mexico City."
        )

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
        "name": "agno-recovery",
        "system_database_url": "sqlite:///dbos_recovery.sqlite",
    }
)

agent = Agent(
    model=CountingFakeModel(id="counting-fake"),
    name="recovery-agent",
    tools=[note_capital],
    telemetry=False,
)
dbos_agent = DBOSAgent(agent)

if __name__ == "__main__":
    DBOS.launch()

    # Fixed workflow id so the second run resumes the SAME workflow.
    with SetWorkflowID("mexico-capital-run"):
        result = dbos_agent.run("Note the capital of Mexico.")

    print("Output:", result.content)
    print("Total real model invokes across all runs:", open(INVOKE_COUNT).read())
    print(
        "(If this says 2 after the second run, the first call was replayed from a checkpoint.)"
    )
