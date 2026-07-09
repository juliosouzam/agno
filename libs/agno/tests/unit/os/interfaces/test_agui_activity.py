"""Opt-in AG-UI ACTIVITY channel for workflow progress.

The workflow_progress dict already carried by STATE (STATE_SNAPSHOT/STATE_DELTA)
is dual-emitted as ACTIVITY_SNAPSHOT/ACTIVITY_DELTA when AGUI(emit_activity=True):
snapshot-first (clients silently drop deltas for unknown activity ids), RFC 6902
deltas rooted at the activity content (not at run_state), a stable per-run message
id, and an authoritative full snapshot at every terminal (completed AND error)
before RUN_FINISHED / RUN_ERROR. The flag defaults off, keeping the existing wire
byte-identical -- asserted first.
"""

import json
from unittest.mock import ANY  # noqa: F401  (parity with sibling test modules)

import pytest

pytest.importorskip("ag_ui", reason="ag_ui not installed")

from ag_ui.core import EventType

from agno.os.interfaces.agui.stream import async_stream_agno_response_as_agui_events
from agno.run.agent import RunCompletedEvent, RunContentEvent
from agno.run.workflow import (
    StepCompletedEvent,
    StepStartedEvent,
    WorkflowCompletedEvent,
    WorkflowErrorEvent,
    WorkflowStartedEvent,
)

ET = EventType

ACTIVITY_TYPES = {ET.ACTIVITY_SNAPSHOT, ET.ACTIVITY_DELTA}
STATE_TYPES = {ET.STATE_SNAPSHOT, ET.STATE_DELTA}


def _stream(*chunks):
    async def gen():
        for chunk in chunks:
            yield chunk

    return gen()


async def _collect(stream, emit_activity=False):
    return [
        event
        async for event in async_stream_agno_response_as_agui_events(stream, "t1", "r1", emit_activity=emit_activity)
    ]


def _workflow_sequence():
    return (
        WorkflowStartedEvent(workflow_name="wf"),
        StepStartedEvent(step_name="s1", step_id="id_1"),
        StepCompletedEvent(step_name="s1", step_id="id_1", content="one"),
        StepStartedEvent(step_name="s2", step_id="id_2"),
        StepCompletedEvent(step_name="s2", step_id="id_2", content="two"),
        WorkflowCompletedEvent(content=None, workflow_name="wf"),
    )


# ====== flag off (the default): wire byte-identical, zero ACTIVITY events ======


@pytest.mark.asyncio
async def test_flag_off_emits_no_activity_events():
    events = await _collect(_stream(*_workflow_sequence()))
    assert not any(e.type in ACTIVITY_TYPES for e in events)


@pytest.mark.asyncio
async def test_agent_stream_with_flag_on_emits_no_activity():
    # Agents/teams never populate workflow_progress -> inert even with the flag on.
    events = await _collect(_stream(RunContentEvent(content="hi"), RunCompletedEvent(content="hi")), emit_activity=True)
    assert not any(e.type in ACTIVITY_TYPES for e in events)


# ====== snapshot-first protocol, stable identity ======


@pytest.mark.asyncio
async def test_snapshot_first_then_deltas_with_stable_id_and_type():
    events = await _collect(_stream(*_workflow_sequence()), emit_activity=True)
    activity = [e for e in events if e.type in ACTIVITY_TYPES]
    assert activity, "flag on must emit activity events"
    assert activity[0].type == ET.ACTIVITY_SNAPSHOT  # clients drop deltas for unknown ids
    assert any(e.type == ET.ACTIVITY_DELTA for e in activity)
    assert {e.message_id for e in activity} == {"agno-workflow-progress-r1"}
    assert {e.activity_type for e in activity} == {"agno-workflow-progress"}


@pytest.mark.asyncio
async def test_delta_replay_matches_terminal_snapshot_steps():
    # Applying the emitted patches onto the prior content must reproduce the steps
    # the terminal snapshot carries. Status legitimately differs: mark_completed
    # flips RUNNING->COMPLETED outside progress_handler, which is exactly why the
    # terminal snapshot is an authoritative resync.
    import jsonpatch

    events = await _collect(_stream(*_workflow_sequence()), emit_activity=True)
    reconstructed = None
    for e in events:
        if e.type == ET.ACTIVITY_SNAPSHOT:
            if reconstructed is None:
                reconstructed = e.content
        elif e.type == ET.ACTIVITY_DELTA:
            reconstructed = jsonpatch.apply_patch(reconstructed, e.patch)
    terminal = [e for e in events if e.type == ET.ACTIVITY_SNAPSHOT][-1].content
    assert terminal["status"] == "COMPLETED"
    assert reconstructed is not None and reconstructed["steps"] == terminal["steps"]
    assert [s["name"] for s in terminal["steps"]] == ["s1", "s2"]
    assert all(s["status"] == "completed" for s in terminal["steps"])


# ====== ordering: STATE precedes ACTIVITY, terminals resolve before the run closes ======


@pytest.mark.asyncio
async def test_every_activity_event_immediately_follows_a_state_event():
    events = await _collect(_stream(*_workflow_sequence()), emit_activity=True)
    types = [e.type for e in events]
    for i, t in enumerate(types):
        if t in ACTIVITY_TYPES:
            assert types[i - 1] in STATE_TYPES, f"activity at {i} not preceded by a STATE event"


@pytest.mark.asyncio
async def test_completed_terminal_ordering():
    events = await _collect(_stream(*_workflow_sequence()), emit_activity=True)
    types = [e.type for e in events]
    assert types[-3:] == [ET.STATE_SNAPSHOT, ET.ACTIVITY_SNAPSHOT, ET.RUN_FINISHED]
    assert events[-2].content["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_yielded_error_terminal_ordering():
    events = await _collect(
        _stream(
            WorkflowStartedEvent(workflow_name="wf"),
            StepStartedEvent(step_name="boom", step_id="id_b"),
            WorkflowErrorEvent(error="boom failed", workflow_name="wf"),
        ),
        emit_activity=True,
    )
    types = [e.type for e in events]
    assert types[-3:] == [ET.STATE_SNAPSHOT, ET.ACTIVITY_SNAPSHOT, ET.RUN_ERROR]
    terminal = events[-2].content
    assert terminal["status"] == "ERROR"
    assert terminal["steps"][0]["status"] == "error"


@pytest.mark.asyncio
async def test_raised_error_terminalizes_activity_before_reraise():
    # The on_error="fail" shape: the engine raises mid-stream, preempting the
    # deferred completion. The stream driver must still land the activity terminal
    # (after the STATE error snapshot) before re-raising; RUN_ERROR itself is
    # emitted by run_entity's except, above this layer.
    async def raising():
        yield WorkflowStartedEvent(workflow_name="wf")
        yield StepStartedEvent(step_name="boom", step_id="id_b")
        raise RuntimeError("boom failed")

    events = []
    with pytest.raises(RuntimeError):
        async for e in async_stream_agno_response_as_agui_events(raising(), "t1", "r1", emit_activity=True):
            events.append(e)
    types = [e.type for e in events]
    assert types[-2:] == [ET.STATE_SNAPSHOT, ET.ACTIVITY_SNAPSHOT]
    assert events[-1].content["status"] == "ERROR"
    assert events[-1].content["steps"][0]["status"] == "error"


# ====== full threading chain + camelCase wire keys over HTTP ======


def test_http_wire_uses_camelcase_activity_keys():
    from fastapi.testclient import TestClient

    from agno.os.app import AgentOS
    from agno.os.interfaces.agui import AGUI
    from agno.workflow.step import Step
    from agno.workflow.types import StepInput, StepOutput
    from agno.workflow.workflow import Workflow

    def noop(step_input: StepInput) -> StepOutput:
        return StepOutput(content="ok")

    wf = Workflow(name="w", steps=[Step(name="s", executor=noop)])
    agent_os = AgentOS(workflows=[wf], interfaces=[AGUI(workflow=wf, emit_activity=True)])
    client = TestClient(agent_os.get_app())
    body = {
        "threadId": "t1",
        "runId": "r1",
        "state": {},
        "messages": [{"id": "m1", "role": "user", "content": "go"}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    response = client.post("/agui", json=body)
    assert response.status_code == 200
    payloads = [json.loads(line[5:].strip()) for line in response.text.split("\n") if line.strip().startswith("data:")]
    snaps = [p for p in payloads if p.get("type") == "ACTIVITY_SNAPSHOT"]
    assert snaps, "activity snapshot must reach the HTTP wire"
    assert snaps[0]["messageId"] == "agno-workflow-progress-r1"
    assert snaps[0]["activityType"] == "agno-workflow-progress"
    assert isinstance(snaps[0]["content"], dict)
