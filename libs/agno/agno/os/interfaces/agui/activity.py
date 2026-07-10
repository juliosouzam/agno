import copy
from typing import Any, Dict, List

from ag_ui.core import ActivityDeltaEvent, ActivitySnapshotEvent, BaseEvent, EventType

from agno.os.interfaces.agui.state import StreamState
from agno.utils.log import log_warning

ACTIVITY_TYPE = "agno-workflow-progress"


def _message_id(state: StreamState) -> str:
    return f"{ACTIVITY_TYPE}-{state.run_id}"


def _snapshot(state: StreamState, progress: Dict[str, Any]) -> List[BaseEvent]:
    state.activity_baseline = copy.deepcopy(progress)
    return [
        ActivitySnapshotEvent(
            type=EventType.ACTIVITY_SNAPSHOT,
            message_id=_message_id(state),
            activity_type=ACTIVITY_TYPE,
            content=copy.deepcopy(progress),
        )
    ]


def on_progress(state: StreamState) -> List[BaseEvent]:
    # Snapshot first, then RFC 6902 deltas; resync on patch failure
    if not state.emit_activity or state.workflow_progress is None:
        return []
    progress = state.workflow_progress
    if state.activity_baseline is None:
        return _snapshot(state, progress)
    try:
        import jsonpatch

        ops = jsonpatch.make_patch(state.activity_baseline, progress).patch
    except Exception as e:
        log_warning(f"Failed to compute activity delta, resynchronizing with a snapshot: {e}")
        return _snapshot(state, progress)
    if not ops:
        return []
    state.activity_baseline = copy.deepcopy(progress)
    return [
        ActivityDeltaEvent(
            type=EventType.ACTIVITY_DELTA,
            message_id=_message_id(state),
            activity_type=ACTIVITY_TYPE,
            patch=ops,
        )
    ]


def terminal_snapshot(state: StreamState) -> List[BaseEvent]:
    if not state.emit_activity or state.workflow_progress is None:
        return []
    return _snapshot(state, state.workflow_progress)
