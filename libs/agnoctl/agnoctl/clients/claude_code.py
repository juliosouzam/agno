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

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agnoctl.clients.base import (
    ClientAdapter,
    ExistingEntry,
    WriteResult,
    bearer_header,
    servers_table,
    token_from_authorization,
)
from agnoctl.errors import CLIError

SUBPROCESS_TIMEOUT = 60.0


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
        """Return the entry Claude Code would resolve: local > project > user scope."""
        user_config = self._read_json_lenient(self._user_config_path)

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

        project_config = self._read_json_lenient(self._project_config_path)
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
            return self._write_config_file(self._project_config_path, server_name, url, token, top_level=True)
        return self._write_config_file(self._user_config_path, server_name, url, token, top_level=True)

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
            raise CLIError("claude mcp add failed: " + (detail or "unknown error"))

    # -- File fallback ------------------------------------------------------------

    def _write_config_file(
        self, path: Path, server_name: str, url: str, token: Optional[str], top_level: bool
    ) -> WriteResult:
        config = self._read_json_strict(path)
        servers = config.get("mcpServers")
        if servers is None:
            servers = {}
            config["mcpServers"] = servers
        elif not isinstance(servers, dict):
            raise CLIError("Refusing to modify " + str(path) + ": 'mcpServers' is not an object.")
        entry: Dict[str, Any] = {"type": "http", "url": url}
        if token:
            entry["headers"] = {"Authorization": bearer_header(token)}
        servers[server_name] = entry
        path.write_text(json.dumps(config, indent=2) + "\n")
        if token:
            path.chmod(0o600)
        note = None
        if path == self._project_config_path and token:
            note = str(path) + " is project-scoped and often committed to version control; it now contains a token."
        return WriteResult(method="file", location=str(path), note=note)

    @staticmethod
    def _read_json_lenient(path: Path) -> Optional[Dict[str, Any]]:
        """For reads: a missing or malformed file simply means no entry found."""
        if not path.exists():
            return None
        try:
            parsed = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _read_json_strict(path: Path) -> Dict[str, Any]:
        """For writes: refuse to clobber a file we cannot parse."""
        if not path.exists():
            return {}
        try:
            parsed = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise CLIError(
                "Refusing to modify " + str(path) + ": the existing file is not valid JSON (" + str(e) + ").",
                hint="Fix or move the file, then re-run.",
            )
        if not isinstance(parsed, dict):
            raise CLIError("Refusing to modify " + str(path) + ": expected a JSON object at the top level.")
        return parsed
