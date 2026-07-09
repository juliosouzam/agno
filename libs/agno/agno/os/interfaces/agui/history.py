"""Session history -> opt-in AG-UI MESSAGES_SNAPSHOT (rehydration at run start).

With AGUI(emit_messages_snapshot=True), a run whose session already holds
prior turns emits ONE MessagesSnapshotEvent right after RUN_STARTED and the
initial STATE_SNAPSHOT, before any streamed traffic -- so a stateless client
(a page reload, a user joining a thread) recovers the conversation without a
custom history endpoint.

Gates (all must hold; the flag itself is checked by the caller):
- fresh run only: a resume payload (trailing tool messages) carries its own
  context, and rehydrating mid-interaction is meaningless;
- server history non-empty;
- no assistant message in the payload: a client that sends assistant turns
  already holds the thread, and since agno's streamed wire ids are minted
  per-event (never the persisted Message ids) the client's id-merge would
  drop-and-reappend every bubble it already renders.

The just-typed user message is echoed at the snapshot tail with a re-minted
id: omitting it would delete the client's optimistic bubble, while echoing
the original id would pin it above the replayed history. v1 is text-only by
design -- no tool-call replay, no reasoning messages, no media. Best-effort:
any failure logs and returns None; rehydration never turns the run into a
RUN_ERROR.
"""

import uuid
from typing import List, Optional, Tuple, Union

from ag_ui.core import EventType, MessagesSnapshotEvent, RunAgentInput
from ag_ui.core.types import AssistantMessage, Message, UserMessage

from agno.session.workflow import WorkflowSession
from agno.utils.log import log_warning


def _history_messages(session) -> List[Message]:
    """Map a session's prior turns onto AG-UI messages (text-only)."""
    messages: List[Message] = []
    if isinstance(session, WorkflowSession):
        # Workflow history is (input, output) interactions, not Messages.
        for interaction in session.get_chat_history():
            if interaction.input is not None:
                messages.append(UserMessage(id=str(uuid.uuid4()), content=str(interaction.input)))
            if interaction.output is not None:
                messages.append(AssistantMessage(id=str(uuid.uuid4()), content=str(interaction.output)))
        return messages
    for message in session.get_chat_history():
        content = message.get_content_string()
        if not content:
            continue  # empty bubbles are noise; tool-call-only assistant entries are skipped
        if message.role == "user":
            messages.append(UserMessage(id=message.id or str(uuid.uuid4()), content=content))
        elif message.role == "assistant":
            messages.append(AssistantMessage(id=message.id or str(uuid.uuid4()), content=content))
    return messages


def _payload_echo(run_input: RunAgentInput) -> Tuple[List[Message], List[Message]]:
    """Echo the current payload around the history: system/developer verbatim at
    the head (kept locals hold their position in the client merge), user messages
    re-minted at the tail (a verbatim id would pin the just-typed message above
    the history; omission would delete the client's optimistic bubble)."""
    head: List[Message] = []
    tail: List[Message] = []
    for message in run_input.messages or []:
        role = getattr(message, "role", None)
        if role in ("system", "developer"):
            head.append(message)
        elif role == "user" and isinstance(getattr(message, "content", None), str):
            tail.append(UserMessage(id=str(uuid.uuid4()), content=message.content))
    return head, tail


async def session_history_snapshot(
    entity, run_input: RunAgentInput, tool_messages: Union[list, None]
) -> Optional[MessagesSnapshotEvent]:
    """Build the run-start rehydration snapshot, or None when any gate fails."""
    try:
        if tool_messages:
            return None
        if any(getattr(m, "role", None) == "assistant" for m in run_input.messages or []):
            return None
        if not getattr(entity, "db", None) or not hasattr(entity, "aget_session"):
            return None
        session = await entity.aget_session(session_id=run_input.thread_id)
        if session is None:
            return None
        history = _history_messages(session)
        if not history:
            return None
        head, tail = _payload_echo(run_input)
        return MessagesSnapshotEvent(type=EventType.MESSAGES_SNAPSHOT, messages=head + history + tail)
    except Exception as e:
        log_warning(f"Failed to build AG-UI messages snapshot for session {run_input.thread_id}: {e}")
        return None
