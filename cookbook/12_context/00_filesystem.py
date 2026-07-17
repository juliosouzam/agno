"""
Filesystem Context Provider
Wraps a local directory with a query_<id> tool routed through a FileTools sub-agent.
Requires: OPENAI_API_KEY
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agno.agent import Agent
from agno.context.fs import FilesystemContextProvider
from agno.models.openai import OpenAIResponses

fs = FilesystemContextProvider(
    id="cookbooks",
    root=Path(__file__).resolve().parent,
    model=OpenAIResponses(id="gpt-5.4-mini"),
)

agent = Agent(
    model=OpenAIResponses(id="gpt-5.4"),
    tools=fs.get_tools(),
    instructions=fs.instructions(),
    markdown=True,
)

if __name__ == "__main__":
    asyncio.run(
        agent.aprint_response(
            "Walk me through setting up an agno context provider. Read "
            "the README and a simple example in this directory, then "
            "lay out the minimal steps with a short code snippet. Cite "
            "the files you pulled from."
        )
    )
