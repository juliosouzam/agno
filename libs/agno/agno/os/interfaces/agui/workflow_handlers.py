import json
import uuid
from typing import Any, List, Optional

from ag_ui.core import (
    BaseEvent,
    EventType,
    RunErrorEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)

from agno.os.interfaces.agui.state import StreamState
from agno.run.base import BaseRunOutputEvent
from agno.run.workflow import WorkflowRunEvent
from agno.workflow.types import StepType

# Terminal workflow events that the stream gate routes to process_completion.
# workflow_cancelled is deliberately excluded — it surfaces as a STATE status
# ("cancelled") via progress_handler and the run finalizes cleanly.
_WORKFLOW_TERMINAL_VALUES = frozenset(
    {
        WorkflowRunEvent.workflow_completed.value,
        WorkflowRunEvent.workflow_error.value,
    }
)

# Every other WorkflowRunEvent value — routed to the native STATE workflow_progress
# handler (progress_handler in handlers.py), which projects the workflow's structure
# (steps, routers, loops, ...) into shared STATE. custom_event is re-excluded there so
# the author's own event keeps its CustomEvent passthrough.
STRUCTURAL_EVENT_VALUES = frozenset(e.value for e in WorkflowRunEvent) - _WORKFLOW_TERMINAL_VALUES


def _event_value(chunk: BaseRunOutputEvent) -> str:
    event = getattr(chunk, "event", None)
    if event is None:
        return ""
    return event.value if hasattr(event, "value") else str(event)


def is_workflow_terminal(chunk: BaseRunOutputEvent) -> bool:
    return _event_value(chunk) in _WORKFLOW_TERMINAL_VALUES


def is_workflow_completed(chunk: BaseRunOutputEvent) -> bool:
    return _event_value(chunk) == WorkflowRunEvent.workflow_completed.value


def _new_text_message(text: str) -> List[BaseEvent]:
    message_id = str(uuid.uuid4())
    return [
        TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=message_id, role="assistant"),
        TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id=message_id, delta=text),
        TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=message_id),
    ]


def _leaf_streamed(node: Any) -> Optional[bool]:
    # Descend to final leaf and check if it streamed; None = uncertain (drop-safe: emit)
    if getattr(node, "step_type", None) in (StepType.PARALLEL, StepType.LOOP):
        return None
    sub = getattr(node, "steps", None)
    if sub:  # Router/Steps/Condition container -> descend the spine to its final leaf
        return _leaf_streamed(sub[-1])
    executor = getattr(node, "executor_type", None)
    if executor in ("agent", "team"):
        return True
    if executor == "function":
        return False
    return None


def _final_leaf_streamed(chunk: BaseRunOutputEvent) -> Optional[bool]:
    results = getattr(chunk, "step_results", None)
    if not results:
        return None
    return _leaf_streamed(results[-1])


def _render_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, (list, dict)):
        return json.dumps(content, default=str)
    return str(content)


def workflow_completion_events(chunk: BaseRunOutputEvent, state: StreamState) -> List[BaseEvent]:
    if _event_value(chunk) == WorkflowRunEvent.workflow_error.value:
        error = getattr(chunk, "error", None) or "Workflow error occurred"
        return [RunErrorEvent(type=EventType.RUN_ERROR, message=str(error))]

    # Cancellation: WorkflowCancelledEvent set state.cancelled and surfaced via
    # workflow_progress STATUS; the trailing WorkflowCompletedEvent.content is the
    # cancel REASON, not an answer. Never render it.
    if state.cancelled:
        return []

    content = getattr(chunk, "content", None)
    if content is None:
        return []
    rendered = _render_content(content)
    if not rendered.strip():
        return []

    # Provenance (descend-to-leaf): suppress the completion recap ONLY when the
    # final answer is an agent/team leaf AND content actually reached the wire.
    # With stream_executor_events=False the agent's answer lands in .content with
    # an agent leaf but never streams (streamed_any_text stays False) -> emit, so
    # it is never silently dropped. A function leaf, or any uncertain shape
    # (Parallel/Loop fan-out, missing/nested provenance), also emits -- which may
    # rarely DUPLICATE a streamed answer. Deliberate drop-safe bias: a rare
    # duplicate beats a dropped answer.
    if _final_leaf_streamed(chunk) and state.streamed_any_text:
        return []
    return _new_text_message(rendered)
