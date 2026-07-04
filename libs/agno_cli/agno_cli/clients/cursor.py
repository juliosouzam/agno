"""Cursor adapter.

Cursor has no CLI for adding MCP servers; the supported programmatic path is editing
mcp.json directly: ~/.cursor/mcp.json (global, all projects) or <project>/.cursor/mcp.json
(project-scoped, wins on name collisions). Remote servers are configured with a url and
optional headers; transport is inferred from the presence of url.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from agno_cli.clients.base import ClientAdapter, ExistingEntry, WriteResult, bearer_header, token_from_authorization


class CursorAdapter(ClientAdapter):
    key = "cursor"
    display_name = "Cursor"

    def __init__(self, home: Optional[Path] = None, cwd: Optional[Path] = None, project: bool = False):
        self.home = home or Path.home()
        self.cwd = cwd or Path.cwd()
        self.project = project

    @property
    def config_path(self) -> Path:
        if self.project:
            return self.cwd / ".cursor" / "mcp.json"
        return self.home / ".cursor" / "mcp.json"

    def detect(self) -> bool:
        return (self.home / ".cursor").is_dir()

    def read_existing(self, server_name: str) -> Optional[ExistingEntry]:
        # Project config wins on collisions, so check it first even in global mode.
        for path in (self.cwd / ".cursor" / "mcp.json", self.home / ".cursor" / "mcp.json"):
            config = self._read_json(path)
            if not config:
                continue
            entry = (config.get("mcpServers") or {}).get(server_name)
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            if not isinstance(url, str) or not url:
                continue
            headers = entry.get("headers")
            if not isinstance(headers, dict):
                headers = {}
            return ExistingEntry(
                url=url,
                token=token_from_authorization(headers.get("Authorization")),
                location=str(path),
            )
        return None

    def write(self, server_name: str, url: str, token: Optional[str]) -> WriteResult:
        path = self.config_path
        config = self._read_json(path) or {}
        servers = config.setdefault("mcpServers", {})
        entry: Dict[str, Any] = {"url": url}
        if token:
            entry["headers"] = {"Authorization": bearer_header(token)}
        servers[server_name] = entry

        path.parent.mkdir(parents=True, exist_ok=True)
        created = not path.exists()
        path.write_text(json.dumps(config, indent=2) + "\n")
        if created:
            path.chmod(0o600)
        return WriteResult(method="file", location=str(path))

    @staticmethod
    def _read_json(path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        try:
            parsed = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None
