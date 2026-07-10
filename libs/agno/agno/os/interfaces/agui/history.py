import uuid
from typing import List, Optional, Tuple, Union

from ag_ui.core import EventType, MessagesSnapshotEvent, RunAgentInput
from ag_ui.core.types import AssistantMessage, Message, UserMessage

from agno.session.workflow import WorkflowSession
from agno.utils.log import log_warning


def _history_messages(session) -> List[Message]:
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
    # System/developer at head, user messages re-minted at tail
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
    # Returns None if any gate fails: resume, assistant in payload, no history
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
