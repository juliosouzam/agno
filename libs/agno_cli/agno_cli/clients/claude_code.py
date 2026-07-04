"""Claude Code adapter.

Preferred path: the `claude mcp add` CLI (it owns its config files and handles scope
placement). Fallback when the binary is missing: write the project-scoped .mcp.json,
which Claude Code reads from the project root and which is safe for us to edit.

Config locations read for idempotency checks:
- ~/.claude.json: top-level "mcpServers" (user scope) and projects.<cwd>.mcpServers (local scope)
- <cwd>/.mcp.json: "mcpServers" (project scope)
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agno_cli.clients.base import ClientAdapter, ExistingEntry, WriteResult, bearer_header, token_from_authorization
from agno_cli.errors import CLIError


def _entry_from_config(servers: Dict[str, Any], server_name: str, location: str) -> Optional[ExistingEntry]:
    entry = servers.get(server_name)
    if not isinstance(entry, dict):
        return None
    url = entry.get("url")
    if not isinstance(url, str) or not url:
        return None
    headers = entry.get("headers")
    if not isinstance(headers, dict):
        headers = {}
    return ExistingEntry(url=url, token=token_from_authorization(headers.get("Authorization")), location=location)


class ClaudeCodeAdapter(ClientAdapter):
    key = "claude-code"
    display_name = "Claude Code"

    def __init__(
        self,
        home: Optional[Path] = None,
        cwd: Optional[Path] = None,
        scope: str = "user",
        which: Callable[[str], Optional[str]] = shutil.which,
        runner: Callable[..., "subprocess.CompletedProcess[str]"] = subprocess.run,
    ):
        self.home = home or Path.home()
        self.cwd = cwd or Path.cwd()
        self.scope = scope
        self._which = which
        self._runner = runner

    @property
    def _user_config_path(self) -> Path:
        return self.home / ".claude.json"

    @property
    def _project_config_path(self) -> Path:
        return self.cwd / ".mcp.json"

    def detect(self) -> bool:
        return self._which("claude") is not None or self._user_config_path.exists()

    def read_existing(self, server_name: str) -> Optional[ExistingEntry]:
        project_config = self._read_json(self._project_config_path)
        if project_config:
            entry = _entry_from_config(
                project_config.get("mcpServers") or {}, server_name, str(self._project_config_path)
            )
            if entry:
                return entry

        user_config = self._read_json(self._user_config_path)
        if user_config:
            entry = _entry_from_config(
                user_config.get("mcpServers") or {}, server_name, str(self._user_config_path) + " (user scope)"
            )
            if entry:
                return entry
            projects = user_config.get("projects")
            if isinstance(projects, dict):
                project = projects.get(str(self.cwd))
                if isinstance(project, dict):
                    entry = _entry_from_config(
                        project.get("mcpServers") or {}, server_name, str(self._user_config_path) + " (local scope)"
                    )
                    if entry:
                        return entry
        return None

    def write(self, server_name: str, url: str, token: Optional[str]) -> WriteResult:
        if self._which("claude") is not None:
            self._write_via_cli(server_name, url, token)
            return WriteResult(method="cli", location="claude mcp add (scope: " + self.scope + ")")
        return self._write_project_file(server_name, url, token)

    # -- CLI path ---------------------------------------------------------------

    def _write_via_cli(self, server_name: str, url: str, token: Optional[str]) -> None:
        # Variadic flags (--header) must come after the name and URL, or Claude Code's
        # parser consumes the positional arguments.
        add_args: List[str] = ["claude", "mcp", "add", "--transport", "http", "--scope", self.scope, server_name, url]
        if token:
            add_args += ["--header", "Authorization: " + bearer_header(token)]

        result = self._runner(add_args, capture_output=True, text=True)
        if result.returncode != 0 and "already exists" in (result.stderr or "").lower():
            remove = self._runner(
                ["claude", "mcp", "remove", "--scope", self.scope, server_name], capture_output=True, text=True
            )
            if remove.returncode == 0:
                result = self._runner(add_args, capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise CLIError("claude mcp add failed: " + (detail or "unknown error"))

    # -- File fallback ------------------------------------------------------------

    def _write_project_file(self, server_name: str, url: str, token: Optional[str]) -> WriteResult:
        path = self._project_config_path
        config = self._read_json(path) or {}
        servers = config.setdefault("mcpServers", {})
        entry: Dict[str, Any] = {"type": "http", "url": url}
        if token:
            entry["headers"] = {"Authorization": bearer_header(token)}
        servers[server_name] = entry
        path.write_text(json.dumps(config, indent=2) + "\n")
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
