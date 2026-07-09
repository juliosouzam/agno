"""
Session Rehydration (opt-in AG-UI MESSAGES_SNAPSHOT)
====================================================

An agent served with ``AGUI(..., emit_messages_snapshot=True)`` and a database
rehydrates history-less clients: when a client reattaches to an existing
session (page reload, joining a thread) and sends no assistant turns of its
own, the run opens with ONE MESSAGES_SNAPSHOT replaying the session's prior
turns, then streams normally. Four ANDed gates keep it safe for clients that
already hold the thread (their id-based merge would drop-and-reappend every
rendered bubble): the flag, fresh-run-only, non-empty server history, and no
assistant message in the payload. The just-typed user message is echoed at
the snapshot tail with a re-minted id. The flag defaults off.

Try it (same threadId both times):
1. POST a first user message -- fresh session, no snapshot.
2. POST a second user message (only that message in ``messages``) -- the
   stream opens with MESSAGES_SNAPSHOT replaying turn 1, then answers.

Run:
    .venvs/demo/bin/python cookbook/05_agent_os/interfaces/agui/session_rehydration.py
"""

from agno.agent.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.openai import OpenAIResponses
from agno.os import AgentOS
from agno.os.interfaces.agui import AGUI

MODEL_ID = "gpt-5.5"
PORT = 9001

chat_agent = Agent(
    name="Rehydration Chat",
    model=OpenAIResponses(id=MODEL_ID),
    db=SqliteDb(db_file="tmp/agui_session_rehydration.db"),
    add_history_to_context=True,
    instructions="Answer briefly and remember the running conversation.",
    markdown=True,
)

agent_os = AgentOS(
    agents=[chat_agent],
    interfaces=[AGUI(agent=chat_agent, emit_messages_snapshot=True)],
)
app = agent_os.get_app()

if __name__ == "__main__":
    agent_os.serve(app="session_rehydration:app", reload=False, port=PORT)
