"""OpenAI Codex adapter.

Codex reads MCP servers from ~/.codex/config.toml as [mcp_servers.<name>] tables. The
`codex mcp add` CLI cannot set static HTTP headers (only --bearer-token-env-var, which
requires the user to manage an environment variable), so this adapter edits the config
file directly, using a static http_headers table for zero-setup authentication.

The edit is a section-scoped text replacement: only lines belonging to
[mcp_servers.<name>] (and its dotted subtables) are touched, so user comments and other
servers survive. The result is validated by re-parsing before it is written.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from agnoctl.clients.base import (
    ClientAdapter,
    ExistingEntry,
    WriteResult,
    atomic_write_text,
    bearer_header,
    token_from_authorization,
)
from agnoctl.errors import CLIError

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def _toml_string(value: str) -> str:
    # TOML basic strings share JSON's escaping rules for the characters that matter here.
    return json.dumps(value)


class CodexAdapter(ClientAdapter):
    key = "codex"

    def __init__(self, home: Optional[Path] = None):
        self.home = home or Path.home()

    @property
    def config_path(self) -> Path:
        return self.home / ".codex" / "config.toml"

    def detect(self) -> bool:
        return (self.home / ".codex").is_dir()

    def read_existing(self, server_name: str) -> Optional[ExistingEntry]:
        parsed = self._parse_config()
        if parsed is None:
            return None
        entry = (parsed.get("mcp_servers") or {}).get(server_name)
        if not isinstance(entry, dict):
            return None
        url = entry.get("url")
        if not isinstance(url, str) or not url:
            return None
        headers = entry.get("http_headers")
        if not isinstance(headers, dict):
            headers = {}
        return ExistingEntry(
            url=url,
            token=token_from_authorization(headers.get("Authorization")),
            location=str(self.config_path),
        )

    def write(self, server_name: str, url: str, token: Optional[str]) -> WriteResult:
        block_lines = ["[mcp_servers." + server_name + "]", "url = " + _toml_string(url)]
        if token:
            block_lines.append(
                "http_headers = { " + _toml_string("Authorization") + " = " + _toml_string(bearer_header(token)) + " }"
            )
        block = "\n".join(block_lines) + "\n"

        existing_text = self.config_path.read_text() if self.config_path.exists() else ""
        if existing_text:
            try:
                tomllib.loads(existing_text)
            except tomllib.TOMLDecodeError as e:
                raise CLIError(
                    "Refusing to modify "
                    + str(self.config_path)
                    + ": the existing TOML does not parse ("
                    + str(e)
                    + ").",
                    hint="Fix or move the file, then re-run.",
                )
        new_text = self._replace_section(existing_text, server_name, block)

        try:
            tomllib.loads(new_text)
        except tomllib.TOMLDecodeError as e:
            raise CLIError(
                "Refusing to write " + str(self.config_path) + ": the resulting TOML would be invalid (" + str(e) + ")."
            )

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.config_path, new_text, secure=bool(token))
        return WriteResult(method="file", location=str(self.config_path))

    # -- Internals -----------------------------------------------------------------

    def _parse_config(self) -> Optional[Dict[str, Any]]:
        if not self.config_path.exists():
            return None
        try:
            return tomllib.loads(self.config_path.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            return None

    @staticmethod
    def _replace_section(text: str, server_name: str, block: str) -> str:
        """Replace (or append) the [mcp_servers.<name>] section, including dotted subtables.

        Every table header — [table] and [[array-of-tables]] alike — ends the previous
        section, so content following the managed section is never swallowed. Trailing
        comment/blank lines inside the managed section that lead into the next header
        are preserved, since they usually describe what follows.
        """
        section_prefix = "[mcp_servers." + server_name
        lines = text.splitlines()
        kept: List[str] = []
        dropped: List[str] = []
        insert_at: Optional[int] = None
        in_section = False

        def flush_trailing_comments() -> None:
            trailing: List[str] = []
            for dropped_line in reversed(dropped):
                if dropped_line.strip() == "" or dropped_line.lstrip().startswith("#"):
                    trailing.append(dropped_line)
                else:
                    break
            kept.extend(reversed(trailing))
            dropped.clear()

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("["):
                owns = stripped.startswith(section_prefix + "]") or stripped.startswith(section_prefix + ".")
                if in_section and not owns:
                    flush_trailing_comments()
                in_section = owns
                if owns:
                    if insert_at is None:
                        insert_at = len(kept)
                    continue
            if in_section:
                dropped.append(line)
            else:
                kept.append(line)
        if in_section:
            dropped.clear()

        block_lines = block.rstrip("\n").splitlines()
        if insert_at is not None:
            kept[insert_at:insert_at] = block_lines
        else:
            if kept and kept[-1].strip():
                kept.append("")
            kept.extend(block_lines)
        return "\n".join(kept).rstrip("\n") + "\n"
