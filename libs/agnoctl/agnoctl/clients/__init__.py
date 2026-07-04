"""Client adapters: how each coding agent stores MCP server configuration."""

from pathlib import Path
from typing import Dict, Optional

from agnoctl.clients.base import ClientAdapter, ExistingEntry, WriteResult
from agnoctl.clients.claude_code import ClaudeCodeAdapter
from agnoctl.clients.codex import CodexAdapter
from agnoctl.clients.cursor import CursorAdapter

# Accepted spellings for --clients, mapped to canonical adapter keys.
CLIENT_ALIASES = {
    "claude": "claude-code",
    "claude-code": "claude-code",
    "codex": "codex",
    "cursor": "cursor",
}


def build_adapters(
    home: Optional[Path] = None,
    cwd: Optional[Path] = None,
    project: bool = False,
) -> Dict[str, ClientAdapter]:
    """All known adapters keyed by canonical client key."""
    claude_scope = "project" if project else "user"
    return {
        "claude-code": ClaudeCodeAdapter(home=home, cwd=cwd, scope=claude_scope),
        "codex": CodexAdapter(home=home),
        "cursor": CursorAdapter(home=home, cwd=cwd, project=project),
    }
