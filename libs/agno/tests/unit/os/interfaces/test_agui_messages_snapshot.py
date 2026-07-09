"""Opt-in AG-UI MESSAGES_SNAPSHOT: session-history rehydration at run start.

With AGUI(emit_messages_snapshot=True), a run whose session already holds prior
turns emits ONE MessagesSnapshotEvent right after RUN_STARTED (and the initial
STATE_SNAPSHOT, when client state is sent), before any streamed traffic. Four
ANDed gates keep it safe for stateful clients (whose id-merge would otherwise
drop-and-reappend every bubble): flag on, fresh run (no trailing tool
messages), server history non-empty, and no assistant message in the payload.
The just-typed user message is echoed at the snapshot tail with a re-minted id.
Rehydration is best-effort: a failing session read logs and continues, never
RUN_ERROR. Flag off (the default) emits nothing.
"""

import pytest

pytest.importorskip("ag_ui", reason="ag_ui not installed")

from ag_ui.core import EventType
from ag_ui.core.types import AssistantMessage as AGUIAssistantMessage
from ag_ui.core.types import ToolMessage as AGUIToolMessage
from ag_ui.core.types import UserMessage as AGUIUserMessage

from agno.os.interfaces.agui.router import run_entity
from agno.workflow.step import Step
from agno.workflow.types import StepInput, StepOutput
from agno.workflow.workflow import Workflow

ET = EventType


class _RunInput:
    def __init__(self, thread_id="t1", run_id="r1", messages=None):
        self.thread_id = thread_id
        self.run_id = run_id
        self.messages = messages if messages is not None else [AGUIUserMessage(id="m1", content="hi")]
        self.forwarded_props = None
        self.state = None
        self.context = []
        self.tools = []


def _noop(step_input: StepInput) -> StepOutput:
    return StepOutput(content="step output one")


def _wf(tmp_path, with_db=True):
    kwargs = dict(name="w", steps=[Step(name="s", executor=_noop)])
    if with_db:
        from agno.db.sqlite import SqliteDb

        kwargs["db"] = SqliteDb(db_file=str(tmp_path / "wf.db"))
    return Workflow(**kwargs)


async def _run(entity, ri, **kw):
    return [e async for e in run_entity(entity, ri, **kw)]


def _snapshots(events):
    return [e for e in events if e.type == ET.MESSAGES_SNAPSHOT]


# ====== the rehydration path ======


@pytest.mark.asyncio
async def test_two_run_workflow_rehydrates_history(tmp_path):
    wf = _wf(tmp_path)

    # run 1: fresh thread -> no history yet -> no snapshot even with the flag on
    first = await _run(wf, _RunInput(run_id="r1"), emit_messages_snapshot=True)
    assert _snapshots(first) == []
    assert any(e.type == ET.RUN_FINISHED for e in first)

    # run 2, same thread: exactly one snapshot, before all streamed traffic
    ri2 = _RunInput(run_id="r2", messages=[AGUIUserMessage(id="m2", content="hi again")])
    second = await _run(wf, ri2, emit_messages_snapshot=True)
    snaps = _snapshots(second)
    assert len(snaps) == 1
    types = [e.type for e in second]
    snap_idx = types.index(ET.MESSAGES_SNAPSHOT)
    assert types[0] == ET.RUN_STARTED
    assert all(t in (ET.RUN_STARTED, ET.STATE_SNAPSHOT) for t in types[:snap_idx])
    first_traffic = min(i for i, t in enumerate(types) if t in (ET.TEXT_MESSAGE_START, ET.STEP_STARTED, ET.STATE_DELTA))
    assert snap_idx < first_traffic

    # run 1's turn replays user -> assistant, then the tail echo of run 2's payload
    messages = snaps[0].messages
    assert [m.role for m in messages] == ["user", "assistant", "user"]
    assert messages[0].content == "hi"
    assert "step output one" in str(messages[1].content)
    assert messages[-1].content == "hi again"
    assert messages[-1].id != "m2"  # re-minted, not the client's optimistic id
    assert all(m.id for m in messages)


@pytest.mark.asyncio
async def test_agent_history_maps_user_and_assistant(tmp_path):
    from agno.agent.agent import Agent
    from agno.db.sqlite import SqliteDb
    from agno.models.base import Model
    from agno.models.response import ModelResponse

    class _StubModel(Model):
        def invoke(self, *args, **kwargs):
            return ModelResponse(role="assistant", content="stub answer")

        async def ainvoke(self, *args, **kwargs):
            return ModelResponse(role="assistant", content="stub answer")

        def invoke_stream(self, *args, **kwargs):
            yield ModelResponse(role="assistant", content="stub answer")

        async def ainvoke_stream(self, *args, **kwargs):
            yield ModelResponse(role="assistant", content="stub answer")

        def _parse_provider_response(self, response, **kwargs):
            return response

        def _parse_provider_response_delta(self, response):
            return response

    agent = Agent(name="a", model=_StubModel(id="stub"), db=SqliteDb(db_file=str(tmp_path / "a.db")))
    await _run(agent, _RunInput(run_id="r1"), emit_messages_snapshot=True)
    ri2 = _RunInput(run_id="r2", messages=[AGUIUserMessage(id="m2", content="second question")])
    second = await _run(agent, ri2, emit_messages_snapshot=True)
    snaps = _snapshots(second)
    assert len(snaps) == 1
    pairs = [(m.role, m.content) for m in snaps[0].messages]
    assert ("user", "hi") in pairs
    assert any(role == "assistant" and "stub answer" in str(content) for role, content in pairs)
    assert pairs[-1] == ("user", "second question")


# ====== the four gates, individually ======


@pytest.mark.asyncio
async def test_flag_off_emits_no_snapshot(tmp_path):
    wf = _wf(tmp_path)
    await _run(wf, _RunInput(run_id="r1"), emit_messages_snapshot=True)  # seed history
    second = await _run(wf, _RunInput(run_id="r2"))  # default off
    assert _snapshots(second) == []


@pytest.mark.asyncio
async def test_assistant_in_payload_suppresses_snapshot(tmp_path):
    # A payload carrying assistant turns proves the client already holds the
    # thread; a snapshot would drop-and-reappend its bubbles (ids never match).
    wf = _wf(tmp_path)
    await _run(wf, _RunInput(run_id="r1"), emit_messages_snapshot=True)
    ri = _RunInput(
        run_id="r2",
        messages=[
            AGUIUserMessage(id="m1", content="hi"),
            AGUIAssistantMessage(id="a1", content="prior reply"),
            AGUIUserMessage(id="m2", content="hi again"),
        ],
    )
    second = await _run(wf, ri, emit_messages_snapshot=True)
    assert _snapshots(second) == []


@pytest.mark.asyncio
async def test_resume_payload_suppresses_snapshot(tmp_path):
    # Trailing tool messages mark a resume; rehydrating mid-interaction is
    # meaningless. Only the snapshot's ABSENCE matters here -- the workflow
    # resume itself is unsupported and errors, which is out of scope.
    wf = _wf(tmp_path)
    await _run(wf, _RunInput(run_id="r1"), emit_messages_snapshot=True)
    ri = _RunInput(
        run_id="r2",
        messages=[
            AGUIUserMessage(id="m1", content="hi"),
            AGUIToolMessage(id="tm1", tool_call_id="tc1", content="tool result"),
        ],
    )
    second = await _run(wf, ri, emit_messages_snapshot=True)
    assert _snapshots(second) == []


@pytest.mark.asyncio
async def test_no_db_suppresses_snapshot(tmp_path):
    wf = _wf(tmp_path, with_db=False)
    await _run(wf, _RunInput(run_id="r1"), emit_messages_snapshot=True)
    second = await _run(wf, _RunInput(run_id="r2"), emit_messages_snapshot=True)
    assert _snapshots(second) == []


# ====== best-effort: a failing session read never breaks the run ======


@pytest.mark.asyncio
async def test_aget_session_failure_is_swallowed(tmp_path, monkeypatch):
    wf = _wf(tmp_path)
    await _run(wf, _RunInput(run_id="r1"), emit_messages_snapshot=True)

    async def boom(session_id=None, **kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(wf, "aget_session", boom)
    second = await _run(wf, _RunInput(run_id="r2"), emit_messages_snapshot=True)
    assert _snapshots(second) == []
    assert any(e.type == ET.RUN_FINISHED for e in second)
    assert not any(e.type == ET.RUN_ERROR for e in second)


# ====== full threading chain over HTTP ======


def test_http_wire_rehydrates_on_second_run(tmp_path):
    import json

    from fastapi.testclient import TestClient

    from agno.os.app import AgentOS
    from agno.os.interfaces.agui import AGUI

    wf = _wf(tmp_path)
    agent_os = AgentOS(workflows=[wf], interfaces=[AGUI(workflow=wf, emit_messages_snapshot=True)])
    client = TestClient(agent_os.get_app())

    def post(run_id, messages):
        return client.post(
            "/agui",
            json={
                "threadId": "http-t1",
                "runId": run_id,
                "state": {},
                "messages": messages,
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
        )

    first = post("r1", [{"id": "m1", "role": "user", "content": "first question"}])
    assert first.status_code == 200
    assert "MESSAGES_SNAPSHOT" not in first.text

    second = post("r2", [{"id": "m2", "role": "user", "content": "second question"}])
    payloads = [json.loads(line[5:].strip()) for line in second.text.split("\n") if line.strip().startswith("data:")]
    snaps = [p for p in payloads if p.get("type") == "MESSAGES_SNAPSHOT"]
    assert len(snaps) == 1
    messages = snaps[0]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == "first question"
    assert messages[-1]["content"] == "second question"
    assert messages[-1]["id"] != "m2"  # tail echo re-minted over the real wire too
