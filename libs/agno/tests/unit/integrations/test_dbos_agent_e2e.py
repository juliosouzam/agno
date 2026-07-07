"""End-to-end DBOS durable-agent tests (launched DBOS, in-memory system DB, fake model).

Regression coverage for the tool-argument-fidelity bug: when the model fires several
calls to the SAME tool with DIFFERENT arguments (sequentially or concurrently), each
durable step must receive its own arguments — not a cached first-call's args.
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("dbos")

from dbos import DBOS  # noqa: E402

from agno.agent import Agent  # noqa: E402
from agno.models.base import Model  # noqa: E402
from agno.models.response import ModelResponse  # noqa: E402


def _weather(city: str) -> str:
    """Return the city name back so callers can tell calls apart."""
    temps = {"Tokyo": 22, "Paris": 17}
    return f"{city}:{temps.get(city, 0)}"


class _TwoCityModel(Model):
    """Turn 1: two get_weather calls (Tokyo + Paris). Turn 2: echo the tool results."""

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
                        "function": {"name": "_weather", "arguments": '{"city": "Tokyo"}'},
                    },
                    {
                        "id": "c2",
                        "type": "function",
                        "function": {"name": "_weather", "arguments": '{"city": "Paris"}'},
                    },
                ],
            )
        tool_results = [str(getattr(m, "content", "")) for m in messages if getattr(m, "role", None) == "tool"]
        return ModelResponse(role="assistant", content="|".join(sorted(tool_results)))

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


@pytest.fixture(scope="module", autouse=True)
def _dbos():
    from agno.integrations.durable import base

    base._REGISTERED_DURABLE_IDS.clear()
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "e2e.sqlite"
    DBOS(config={"name": "e2e", "system_database_url": f"sqlite:///{db_path}"})
    DBOS.launch()
    yield
    DBOS.destroy()
    base._REGISTERED_DURABLE_IDS.clear()


def _build(name):
    from agno.integrations.dbos import DBOSAgent

    agent = Agent(model=_TwoCityModel(id="fake"), name=name, tools=[_weather], telemetry=False)
    return DBOSAgent(agent)


def test_sync_same_tool_distinct_args():
    d = _build("wx-sync")
    result = d.run("Compare Tokyo and Paris.")
    # Each tool call kept its own city; both distinct results are present.
    assert result.content == "Paris:17|Tokyo:22"


def test_async_same_tool_distinct_args():
    d = _build("wx-async")
    result = asyncio.run(d.arun("Compare Tokyo and Paris."))
    # Concurrent async tools each received their own arguments and were awaited.
    assert result.content == "Paris:17|Tokyo:22"
