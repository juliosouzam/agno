"""
Database Context Provider (SQLite, read + write)
Exposes query_<id> and update_<id> tools via separate read/write sub-agents.
Requires: OPENAI_API_KEY
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from agno.agent import Agent
from agno.context.database import DatabaseContextProvider
from agno.models.openai import OpenAIResponses
from sqlalchemy import create_engine, text

DB_PATH = Path(tempfile.gettempdir()) / "agno_context_db_cookbook.sqlite"
if DB_PATH.exists():
    DB_PATH.unlink()

db_url = f"sqlite:///{DB_PATH}"
engine = create_engine(db_url)

with engine.begin() as conn:
    conn.execute(
        text(
            "CREATE TABLE contacts ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, "
            "email TEXT, "
            "role TEXT"
            ")"
        )
    )
    conn.execute(
        text("INSERT INTO contacts (name, email, role) VALUES (:n, :e, :r)"),
        {"n": "Ada Lovelace", "e": "ada@example.com", "r": "engineer"},
    )

db = DatabaseContextProvider(
    id="contacts",
    sql_engine=engine,
    readonly_engine=engine,
    model=OpenAIResponses(id="gpt-5.4-mini"),
)

agent = Agent(
    model=OpenAIResponses(id="gpt-5.4"),
    tools=db.get_tools(),
    instructions=db.instructions(),
    markdown=True,
)


async def _run() -> None:
    await agent.aprint_response(
        "Add a contact named 'Grace Hopper' with email "
        "'grace@example.com' and role 'admiral' to the contacts table."
    )

    print()
    await agent.aprint_response("List every contact in the contacts table with their role.")

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name, role FROM contacts ORDER BY id")
        ).fetchall()
    print(f"\n[direct SQL] contacts table rows: {rows}")
    assert any(r.name == "Grace Hopper" for r in rows), "write did not persist"


if __name__ == "__main__":
    asyncio.run(_run())
