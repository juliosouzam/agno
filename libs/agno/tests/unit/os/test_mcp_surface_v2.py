"""Unit tests for the v2.7 MCP surface: run-lifecycle tools, read-only session tools
backed by the shared service layer, tool annotations, and the app wiring fixes.
"""

import pytest

pytest.importorskip("fastmcp")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from fastmcp import Client  # noqa: E402

import agno.os.mcp as mcp_mod  # noqa: E402
from agno.agent import Agent  # noqa: E402
from agno.os import AgentOS  # noqa: E402
from agno.os.mcp import build_mcp_server  # noqa: E402
from agno.os.services.sessions import SessionNotFoundError, get_session_runs, get_sessions_page  # noqa: E402
from agno.run.agent import RunOutput  # noqa: E402
from agno.run.base import RunStatus  # noqa: E402
from agno.run.requirement import RunRequirement  # noqa: E402
from agno.workflow.step import Step  # noqa: E402
from agno.workflow.workflow import Workflow  # noqa: E402


def _agent() -> Agent:
    return Agent(id="demo-agent", name="Demo Agent")


# ==================== Tool annotations ====================


async def test_annotations_mark_reads_and_destructive_tools():
    """Clients use readOnlyHint/destructiveHint for permission UX; reads and the one
    destructive tool must be distinguishable."""
    os = AgentOS(agents=[_agent()], enable_mcp_server=True)
    async with Client(build_mcp_server(os)) as client:
        tools = {t.name: t for t in await client.list_tools()}

    assert tools["get_agentos_config"].annotations.readOnlyHint is True
    assert tools["get_sessions"].annotations.readOnlyHint is True
    assert tools["get_session_runs"].annotations.readOnlyHint is True
    assert tools["cancel_run"].annotations.destructiveHint is True
    assert tools["run_agent"].annotations.readOnlyHint is False


async def test_config_payload_is_compact():
    """get_agentos_config is a discovery payload: ids and summaries, not the full config."""
    os = AgentOS(agents=[_agent()], enable_mcp_server=True)
    os.get_app()  # populates db discovery (os.dbs), as at serve time
    async with Client(build_mcp_server(os)) as client:
        result = await client.call_tool("get_agentos_config", {})

    structured = result.structured_content or {}
    payload = structured.get("result", structured)
    assert payload["agents"][0]["id"] == "demo-agent"
    for heavy_key in ("session", "memory", "knowledge", "evals", "metrics", "traces"):
        assert heavy_key not in payload


# ==================== continue_run / cancel_run ====================


async def test_continue_run_requires_exactly_one_component():
    os = AgentOS(agents=[_agent()], enable_mcp_server=True)
    async with Client(build_mcp_server(os)) as client:
        result = await client.call_tool("continue_run", {"run_id": "r1"}, raise_on_error=False)
        assert result.is_error
        result = await client.call_tool(
            "continue_run",
            {"run_id": "r1", "agent_id": "demo-agent", "team_id": "demo-team"},
            raise_on_error=False,
        )
        assert result.is_error


async def test_continue_run_threads_identity_and_parses_requirements(monkeypatch):
    monkeypatch.setattr(mcp_mod, "_resolve_user_id", lambda caller: "jwt-alice")
    agent = _agent()
    captured = {}

    async def fake_acontinue_run(*, run_id, session_id, user_id, requirements, stream=False):
        captured.update(run_id=run_id, session_id=session_id, user_id=user_id, requirements=requirements, stream=stream)
        return RunOutput(run_id=run_id, session_id=session_id, content="resumed", status=RunStatus.completed)

    agent.acontinue_run = fake_acontinue_run  # type: ignore[method-assign]
    os = AgentOS(agents=[agent], enable_mcp_server=True)

    requirement_dict = {"tool_execution": {"tool_name": "send_email"}, "confirmation": True}
    async with Client(build_mcp_server(os)) as client:
        result = await client.call_tool(
            "continue_run",
            {
                "run_id": "run-9",
                "session_id": "sess-9",
                "agent_id": "demo-agent",
                "requirements": [requirement_dict],
            },
        )

    assert captured["run_id"] == "run-9"
    assert captured["user_id"] == "jwt-alice"
    assert captured["stream"] is False  # pinned so a stream=True agent can't return an iterator
    assert isinstance(captured["requirements"][0], RunRequirement)
    assert result.content[0].text == "resumed"
    assert (result.structured_content or {})["status"] == "COMPLETED"


async def test_continue_run_dispatches_workflow_step_requirements():
    workflow = Workflow(id="demo-wf", name="Demo WF", steps=[Step(agent=_agent())])
    captured = {}

    async def fake_acontinue_run(*, run_id, session_id, step_requirements, stream=False):
        captured.update(run_id=run_id, step_requirements=step_requirements, stream=stream)
        from agno.run.workflow import WorkflowRunOutput

        return WorkflowRunOutput(run_id=run_id, session_id=session_id, content="wf resumed")

    workflow.acontinue_run = fake_acontinue_run  # type: ignore[method-assign]
    os = AgentOS(workflows=[workflow], enable_mcp_server=True)

    step_requirement = {"step_id": "s1", "step_name": "approve", "step_type": "Step", "requires_confirmation": True}
    async with Client(build_mcp_server(os)) as client:
        result = await client.call_tool(
            "continue_run",
            {"run_id": "wf-run-9", "workflow_id": "demo-wf", "requirements": [step_requirement]},
        )

    assert captured["run_id"] == "wf-run-9"
    assert captured["step_requirements"][0].step_id == "s1"
    assert result.content[0].text == "wf resumed"


async def test_cancel_run_requires_exactly_one_component():
    os = AgentOS(agents=[_agent()], enable_mcp_server=True)
    async with Client(build_mcp_server(os)) as client:
        result = await client.call_tool("cancel_run", {"run_id": "r1"}, raise_on_error=False)
    assert result.is_error


async def test_cancel_run_requests_cancellation_on_the_named_component():
    agent = _agent()
    captured = {}

    async def fake_acancel_run(run_id):
        captured["run_id"] = run_id
        return True

    agent.acancel_run = fake_acancel_run  # type: ignore[method-assign]
    os = AgentOS(agents=[agent], enable_mcp_server=True)
    async with Client(build_mcp_server(os)) as client:
        result = await client.call_tool("cancel_run", {"run_id": "run-x", "agent_id": "demo-agent"})
    assert captured["run_id"] == "run-x"
    assert "cancellation requested" in result.content[0].text


async def test_continue_run_rejects_remote_component(monkeypatch):
    """Remote components can't carry resolved requirements downstream; continue must fail
    clearly (like the REST 400) rather than crash."""
    from agno.agent.remote import RemoteAgent

    remote = RemoteAgent(base_url="http://example.invalid", agent_id="remote-agent")
    monkeypatch.setattr(mcp_mod, "get_agent_by_id", lambda cid, agents: remote)
    os = AgentOS(agents=[_agent()], enable_mcp_server=True)
    async with Client(build_mcp_server(os)) as client:
        result = await client.call_tool(
            "continue_run",
            {"run_id": "r1", "session_id": "s1", "agent_id": "remote-agent"},
            raise_on_error=False,
        )
    assert result.is_error
    assert "remote" in result.content[0].text.lower()


async def test_lifecycle_ownership_gate_blocks_other_users(monkeypatch):
    """A scoped (non-admin) caller cannot cancel a run in a session they do not own."""
    monkeypatch.setattr(mcp_mod, "_scoped_caller_user_id", lambda: "user-b")
    agent = _agent()
    cancelled = {"called": False}

    async def fake_acancel_run(run_id):
        cancelled["called"] = True
        return True

    async def fake_aget_session(session_id, user_id):
        return None  # user-b owns no such session

    agent.acancel_run = fake_acancel_run  # type: ignore[method-assign]
    agent.aget_session = fake_aget_session  # type: ignore[method-assign]
    os = AgentOS(agents=[agent], enable_mcp_server=True)
    async with Client(build_mcp_server(os)) as client:
        result = await client.call_tool(
            "cancel_run",
            {"run_id": "run-a", "session_id": "sess-a", "agent_id": "demo-agent"},
            raise_on_error=False,
        )
    assert result.is_error
    assert cancelled["called"] is False  # never reached the cancellation registry


async def test_get_session_runs_history_is_trimmed():
    """History reads must not ship the message transcript / system prompt to the client."""
    session = {
        "session_id": "s1",
        "agent_id": "a-1",
        "runs": [
            {
                "run_id": "r1",
                "agent_id": "a-1",
                "run_input": "hello",
                "content": "hi there",
                "status": "COMPLETED",
                "messages": [{"role": "system", "content": "SECRET_PROMPT"}],
                "events": [{"event": "x"}],
            }
        ],
    }
    from agno.os.schema import RunSchema

    trimmed = mcp_mod._trim_session_run(RunSchema.from_dict(session["runs"][0]))
    assert trimmed["content"] == "hi there"
    assert trimmed["status"] == "COMPLETED"
    assert "messages" not in trimmed
    assert "events" not in trimmed
    assert "SECRET_PROMPT" not in str(trimmed)


# ==================== Session service ====================


class _FakeSyncDb:
    """Sync BaseDb-shaped stub: exercises the threadpool path in the service."""

    def __init__(self, session=None, sessions=None):
        self._session = session
        self._sessions = sessions or []

    def get_session(self, session_id, session_type, user_id, deserialize):
        return self._session

    def get_sessions(self, **kwargs):
        return self._sessions, len(self._sessions)


async def test_service_auto_detects_workflow_session_and_classifies_runs():
    session = {
        "session_id": "s1",
        "workflow_id": "wf-1",
        "runs": [
            {"run_id": "r1", "workflow_id": "wf-1", "content": "wf run"},
            {"run_id": "r2", "agent_id": "a-1", "content": "member agent run"},
        ],
    }
    runs = await get_session_runs(_FakeSyncDb(session=session), session_id="s1", session_type=None)  # type: ignore[arg-type]

    # workflow_id run renders as a workflow run, the bare agent run falls back to RunSchema
    assert runs[0].__class__.__name__ == "WorkflowRunSchema"
    assert runs[1].__class__.__name__ == "RunSchema"


async def test_service_raises_session_not_found():
    with pytest.raises(SessionNotFoundError):
        await get_session_runs(_FakeSyncDb(session=None), session_id="missing")  # type: ignore[arg-type]


async def test_service_lists_sessions_via_threadpool():
    from agno.db.base import SessionType

    db = _FakeSyncDb(sessions=[{"session_id": "s1", "session_type": "agent"}])
    sessions, total = await get_sessions_page(db, session_type=SessionType.AGENT)  # type: ignore[arg-type]
    assert total == 1
    assert sessions[0]["session_id"] == "s1"


# ==================== App wiring ====================


def test_resync_reuses_started_mcp_app_and_mount():
    """resync() must not replace the MCP app (a fresh one's lifespan never runs) and
    must not accumulate duplicate mounts."""
    os = AgentOS(agents=[_agent()], enable_mcp_server=True)
    app = os.get_app()
    mcp_app_before = os._mcp_app
    assert mcp_app_before is not None

    os.resync(app=app)

    assert os._mcp_app is mcp_app_before
    mounts = [r for r in app.router.routes if getattr(r, "app", None) is mcp_app_before]
    assert len(mounts) == 1


def test_home_route_works_with_mcp_enabled():
    os = AgentOS(agents=[_agent()], enable_mcp_server=True)
    app = os.get_app()
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "AgentOS" in response.text


def test_get_app_idempotent_with_base_app():
    base_app = FastAPI()
    os = AgentOS(agents=[_agent()], enable_mcp_server=True, base_app=base_app)
    first = os.get_app()
    route_count = len(first.router.routes)
    mcp_app_first = os._mcp_app

    second = os.get_app()

    assert second is first
    assert len(second.router.routes) == route_count
    assert os._mcp_app is mcp_app_first
    mounts = [r for r in second.router.routes if getattr(r, "app", None) is mcp_app_first]
    assert len(mounts) == 1
