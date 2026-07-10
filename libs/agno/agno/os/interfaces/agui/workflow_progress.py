import copy
from typing import Any, Dict, List, Optional

from ag_ui.core import (
    BaseEvent,
    EventType,
    StateDeltaEvent,
    StateSnapshotEvent,
    StepFinishedEvent,
    StepStartedEvent,
)

from agno.os.interfaces.agui import activity
from agno.os.interfaces.agui.state import StreamState
from agno.os.interfaces.agui.workflow_handlers import _event_value, _render_content
from agno.run.base import BaseRunOutputEvent, RunStatus
from agno.run.workflow import WorkflowRunEvent

_E = WorkflowRunEvent
_MAX_OUTPUT = 500  # cap step output stored in STATE so deltas stay small

_PAUSE_STEP = frozenset({_E.step_paused.value, _E.step_executor_paused.value, _E.step_output_review.value})
_PAUSE_WORKFLOW = frozenset({_E.workflow_paused.value, _E.router_paused.value})
_CONTINUE = frozenset({_E.step_continued.value, _E.step_executor_continued.value})
# condition_paused ("ConditionPaused") is intentionally absent: it is a vestigial enum
# value in agno core with no event class, never emitted -- so it needs no handling here.


def _progress(state: StreamState) -> Dict[str, Any]:
    # Reset progress when run_id changes to avoid cross-turn leakage from echoed state
    if state.run_state is None:
        state.run_state = {}
    existing = state.run_state.get("workflow_progress")
    if existing is None or existing.get("run_id") != state.run_id:
        state.run_state["workflow_progress"] = {
            "run_id": state.run_id,
            "status": RunStatus.running.value,
            "steps": [],
        }
    return state.run_state["workflow_progress"]


def _sync_steps_to_root(state: StreamState) -> None:
    # Dojo agentic_generative_ui expects state.steps[{description, status}]
    if state.run_state is None or "workflow_progress" not in state.run_state:
        return
    wp_steps = state.run_state["workflow_progress"].get("steps", [])
    # Map to Dojo format: description (from name), status (running -> pending for Dojo)
    state.run_state["steps"] = [
        {
            "description": s.get("name", ""),
            "status": "pending" if s.get("status") == "running" else s.get("status", "pending"),
        }
        for s in wp_steps
    ]


def _baseline(state: StreamState) -> List[BaseEvent]:
    # Establish delta baseline if no caller-supplied state
    if state.run_state is not None:
        return []
    state.run_state = {}
    state.set_state_snapshot(state.run_state)
    return [StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=copy.deepcopy(state.run_state))]


def _emit_delta(state: StreamState) -> List[BaseEvent]:
    if state.run_state is None:
        return []
    ops = state.compute_state_delta(state.run_state)
    if not ops:
        return []
    state.set_state_snapshot(state.run_state)
    return [StateDeltaEvent(type=EventType.STATE_DELTA, delta=ops)]


def _short(content: Any) -> Optional[str]:
    if content is None:
        return None
    text = _render_content(content)
    return text[:_MAX_OUTPUT] if text else None


def _open_step(steps: List[Dict[str, Any]], step_id: Optional[str]) -> Optional[Dict[str, Any]]:
    # Find by step_id (not step_index) to handle concurrent Parallel siblings
    for step in reversed(steps):
        if step["status"] in ("running", "paused") and step["id"] == step_id:
            return step
    return None


def mark_completed(state: StreamState) -> None:
    # Promote workflow to completed; mark any leftover running steps as skipped
    progress = state.workflow_progress
    if progress is None:
        return
    if progress.get("status") == RunStatus.running.value:
        progress["status"] = RunStatus.completed.value
    if progress.get("status") == RunStatus.completed.value:
        for step in progress["steps"]:
            if step["status"] == "running":
                step["status"] = "skipped"
    _sync_steps_to_root(state)


def error_snapshot(state: StreamState) -> List[BaseEvent]:
    # Terminalize progress to ERROR and emit final snapshot
    progress = state.workflow_progress
    if progress is None or state.run_state is None:
        return []
    progress["status"] = RunStatus.error.value
    for step in progress["steps"]:
        if step["status"] in ("running", "paused"):
            step["status"] = "error"
    _sync_steps_to_root(state)
    state.set_state_snapshot(state.run_state)
    events: List[BaseEvent] = [
        StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=copy.deepcopy(state.run_state))
    ]
    return events + activity.terminal_snapshot(state)


def progress_handler(chunk: BaseRunOutputEvent, state: StreamState) -> List[BaseEvent]:
    events = _baseline(state)
    value = _event_value(chunk)
    progress = _progress(state)
    state.workflow_progress = progress
    steps = progress["steps"]
    name = getattr(chunk, "step_name", None)
    step_id = getattr(chunk, "step_id", None)

    if value == _E.step_started.value:
        steps.append({"id": step_id, "name": name, "status": "running", "output": None})
        if name:
            events.append(StepStartedEvent(type=EventType.STEP_STARTED, step_name=name))
    elif value == _E.step_completed.value:
        step = _open_step(steps, step_id)
        if step is not None:
            step.update(status="completed", output=_short(getattr(chunk, "content", None)))
        if name:
            events.append(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=name))
    elif value == _E.step_error.value:
        step = _open_step(steps, step_id)
        if step is not None:
            step.update(status="error", output=_short(getattr(chunk, "error", None)))
        if name:
            events.append(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=name))
    elif value in _PAUSE_STEP:
        step = _open_step(steps, step_id)
        if step is not None:
            step["status"] = "paused"
    elif value in _PAUSE_WORKFLOW:
        progress["status"] = RunStatus.paused.value
    elif value in _CONTINUE:
        step = _open_step(steps, step_id)
        if step is not None:
            step["status"] = "running"
    elif value == _E.workflow_cancelled.value:
        progress["status"] = RunStatus.cancelled.value
        state.cancelled = True
    # workflow_started initialises progress above; container/agent events are no-ops (their inner
    # step_started/completed populate the flat list). step_output carries no step_id and only restates
    # content the immediately-following step_completed sets authoritatively, so it needs no branch here.
    _sync_steps_to_root(state)
    return events + _emit_delta(state) + activity.on_progress(state)
