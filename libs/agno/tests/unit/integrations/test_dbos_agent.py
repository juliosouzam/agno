"""Unit tests for the DBOS durable-agent wrapper mechanics.

These do not require a live LLM or a running DBOS server — they exercise the Agno-side
surgery (identity validation, model interception, tool-hook injection, exclusions) and
the durable base contract.
"""

import pytest

pytest.importorskip("dbos")

from dbos import DBOS  # noqa: E402

from agno.agent import Agent  # noqa: E402
from agno.integrations.durable.base import DurableExecutionError  # noqa: E402
from agno.models.base import Model  # noqa: E402
from agno.models.response import ModelResponse  # noqa: E402


class _FakeModel(Model):
    def invoke(self, *args, **kwargs) -> ModelResponse:
        return ModelResponse(role="assistant", content="ok")

    async def ainvoke(self, *args, **kwargs) -> ModelResponse:
        return self.invoke()

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
    # A single DBOS instance for the module; no launch needed for these wiring tests.
    DBOS(config={"name": "test", "system_database_url": "sqlite:///:memory:"})
    yield
    DBOS.destroy()


@pytest.fixture(autouse=True)
def _clear_registry():
    from agno.integrations.durable import base

    base._REGISTERED_DURABLE_IDS.clear()
    yield
    base._REGISTERED_DURABLE_IDS.clear()


def _tool(city: str) -> str:
    """A trivial tool."""
    return city


def test_requires_stable_name():
    from agno.integrations.dbos import DBOSAgent

    agent = Agent(model=_FakeModel(id="m"))  # no name/id
    with pytest.raises(DurableExecutionError):
        DBOSAgent(agent)


def test_wraps_without_mutating_original():
    from agno.integrations.dbos import DBOSAgent

    original_model = _FakeModel(id="m")
    agent = Agent(model=original_model, name="a1", retries=5)

    d = DBOSAgent(agent)

    # Original agent untouched: retries preserved, model is a different instance,
    # and the original model's invoke is NOT overridden as an instance attribute.
    assert agent.retries == 5
    assert agent.model is original_model
    assert "invoke" not in original_model.__dict__  # not instance-overridden
    # Copy has retries zeroed, an isolated model, and an overridden invoke.
    assert d.agent.retries == 0
    assert d.agent.model is not original_model
    assert "invoke" in d.agent.model.__dict__  # instance-overridden durable invoke
    assert d.agent_id == "a1"


def test_tool_hooks_injected():
    from agno.integrations.dbos import DBOSAgent

    agent = Agent(model=_FakeModel(id="m"), name="a2", tools=[_tool])
    d = DBOSAgent(agent)

    # One durable hook appended to the copy's tool_hooks.
    assert len(d.agent.tool_hooks or []) == 1


def test_wrap_tools_false_skips_hooks():
    from agno.integrations.dbos import DBOSAgent

    agent = Agent(model=_FakeModel(id="m"), name="a3", tools=[_tool])
    d = DBOSAgent(agent, wrap_tools=False)

    assert not d.agent.tool_hooks


def test_duplicate_identity_rejected():
    from agno.integrations.dbos import DBOSAgent

    DBOSAgent(Agent(model=_FakeModel(id="m"), name="dup"))
    with pytest.raises(DurableExecutionError):
        DBOSAgent(Agent(model=_FakeModel(id="m"), name="dup"))


def test_passthrough_attribute_access():
    from agno.integrations.dbos import DBOSAgent

    agent = Agent(model=_FakeModel(id="m"), name="passthrough-agent")
    d = DBOSAgent(agent)

    # dbos_agent.<agent attr> resolves via __getattr__.
    assert d.name == "passthrough-agent"


def test_all_model_slots_wrapped():
    from agno.integrations.dbos import DBOSAgent

    main = _FakeModel(id="main")
    parser = _FakeModel(id="parser")
    output = _FakeModel(id="output")
    agent = Agent(
        model=main,
        parser_model=parser,
        output_model=output,
        name="multi-model",
    )
    original_parser_invoke = parser.invoke

    d = DBOSAgent(agent)

    # Every model slot on the copy has its invoke replaced.
    assert d.agent.model.invoke is not main.invoke
    assert d.agent.parser_model.invoke is not original_parser_invoke
    assert d.agent.output_model.invoke is not output.invoke


def test_tool_hook_adapts_to_async_func():
    """The single hook returns an awaitable when func is a coroutine function."""
    import asyncio

    from agno.integrations.dbos import DBOSAgent

    agent = Agent(model=_FakeModel(id="m"), name="hook-agent", tools=[_tool])
    d = DBOSAgent(agent)
    hook = d._make_tool_hook()

    async def _async_next(**kwargs):
        return "async-result"

    # Outside a workflow -> passthrough, but still an awaitable for an async func.
    result = hook("some_tool", _async_next, {"x": 1})
    assert asyncio.iscoroutine(result)
    assert asyncio.run(result) == "async-result"

    # Sync func -> plain value.
    assert hook("other_tool", lambda **kw: kw["x"], {"x": 7}) == 7
