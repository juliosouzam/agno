"""Claude Code adapter.

Preferred write path: the `claude mcp add` CLI (the sanctioned interface; it owns config
placement). Fallback when the binary is missing: edit the config file for the requested
scope directly — ~/.claude.json (user scope) or <cwd>/.mcp.json (project scope).

Reads follow Claude Code's same-name resolution precedence: local scope
(~/.claude.json projects.<cwd>.mcpServers) > project (.mcp.json) > user
(~/.claude.json mcpServers). Getting this order right matters: the entry this
adapter reports is the one Claude Code will actually use, which is how connect
detects stale shadowing entries after a write.

Note: the CLI write path passes the Authorization header via argv, which is transiently
visible in the local process list. The file fallback avoids this; both paths end with a
read-back so a write that did not take effect is reported, never assumed.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agnoctl.clients.base import (
    ClientAdapter,
    ExistingEntry,
    WriteResult,
    bearer_header,
    read_json_lenient,
    servers_table,
    token_from_authorization,
    write_servers_entry,
)
from agnoctl.errors import CLIError

SUBPROCESS_TIMEOUT = 60.0


def _redact_token(text: str, token: Optional[str]) -> str:
    """Strip the bearer token (raw and as an Authorization value) out of a string before
    it reaches a user-facing error or JSON output."""
    if not token:
        return text
    return text.replace(bearer_header(token), "Bearer ***").replace(token, "***")


def _entry_from_servers(servers: Dict[str, Any], server_name: str, location: str) -> Optional[ExistingEntry]:
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
        """Return the entry Claude Code would resolve: local > project > user scope."""
        user_config = read_json_lenient(self._user_config_path)

        if user_config:
            projects = user_config.get("projects")
            if isinstance(projects, dict):
                project = projects.get(str(self.cwd))
                if isinstance(project, dict):
                    entry = _entry_from_servers(
                        servers_table(project), server_name, str(self._user_config_path) + " (local scope)"
                    )
                    if entry:
                        return entry

        project_config = read_json_lenient(self._project_config_path)
        if project_config:
            entry = _entry_from_servers(servers_table(project_config), server_name, str(self._project_config_path))
            if entry:
                return entry

        if user_config:
            entry = _entry_from_servers(
                servers_table(user_config), server_name, str(self._user_config_path) + " (user scope)"
            )
            if entry:
                return entry
        return None

    def write(self, server_name: str, url: str, token: Optional[str]) -> WriteResult:
        if self._which("claude") is not None:
            self._write_via_cli(server_name, url, token)
            return WriteResult(method="cli", location="claude mcp add (scope: " + self.scope + ")")
        if self.scope == "project":
            return self._write_config_file(self._project_config_path, server_name, url, token)
        return self._write_config_file(self._user_config_path, server_name, url, token)

    # -- CLI path ---------------------------------------------------------------

    def _run_claude(self, args: List[str]) -> "subprocess.CompletedProcess[str]":
        try:
            return self._runner(
                args,
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise CLIError("The claude CLI did not respond within " + str(int(SUBPROCESS_TIMEOUT)) + "s: " + args[2])

    def _write_via_cli(self, server_name: str, url: str, token: Optional[str]) -> None:
        # Variadic flags (--header) must come after the name and URL, or Claude Code's
        # parser consumes the positional arguments.
        add_args: List[str] = ["claude", "mcp", "add", "--transport", "http", "--scope", self.scope, server_name, url]
        if token:
            add_args += ["--header", "Authorization: " + bearer_header(token)]

        result = self._run_claude(add_args)
        if result.returncode != 0 and "already exists" in (result.stderr or "").lower():
            remove = self._run_claude(["claude", "mcp", "remove", "--scope", self.scope, server_name])
            if remove.returncode == 0:
                result = self._run_claude(add_args)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            # The token rode in via argv; if the CLI echoed the command back in its error,
            # keep the secret out of the message we print / return as JSON.
            raise CLIError("claude mcp add failed: " + (_redact_token(detail, token) or "unknown error"))

    # -- File fallback ------------------------------------------------------------

    def _write_config_file(self, path: Path, server_name: str, url: str, token: Optional[str]) -> WriteResult:
        entry: Dict[str, Any] = {"type": "http", "url": url}
        if token:
            entry["headers"] = {"Authorization": bearer_header(token)}
        write_servers_entry(path, server_name, entry, secure=bool(token))
        note = None
        if path == self._project_config_path and token:
            note = str(path) + " is project-scoped and often committed to version control; it now contains a token."
        return WriteResult(method="file", location=str(path), note=note)
