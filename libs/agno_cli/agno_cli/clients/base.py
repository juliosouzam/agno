"""Shared shapes for coding-agent client adapters.

An adapter knows how one coding agent (Claude Code, Codex, Cursor) stores MCP server
configuration: how to detect the client on this machine, read an existing entry back
(for idempotent re-runs), and write an entry pointing at an AgentOS MCP endpoint.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExistingEntry:
    """An MCP server entry found in a client's configuration."""

    url: str
    token: Optional[str]
    location: str  # human-readable: file path or config scope it was found in


@dataclass
class WriteResult:
    """How and where a config write landed."""

    method: str  # "cli" (client's own CLI did the write) | "file" (we edited the config file)
    location: str


def bearer_header(token: str) -> str:
    return "Bearer " + token


def token_from_authorization(value: Optional[str]) -> Optional[str]:
    """Extract the raw token from an Authorization header value."""
    if not value:
        return None
    if value.lower().startswith("bearer "):
        return value[len("bearer ") :].strip() or None
    return value.strip() or None


class ClientAdapter(ABC):
    key: str
    display_name: str

    @abstractmethod
    def detect(self) -> bool:
        """Whether this client appears to be installed or configured on this machine."""

    @abstractmethod
    def read_existing(self, server_name: str) -> Optional[ExistingEntry]:
        """Return the existing MCP entry for server_name, if any."""

    @abstractmethod
    def write(self, server_name: str, url: str, token: Optional[str]) -> WriteResult:
        """Create or replace the MCP entry for server_name. Must be idempotent."""
