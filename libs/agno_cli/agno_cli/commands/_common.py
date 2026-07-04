"""Helpers shared by CLI commands."""

import os
import sys
from typing import Optional, Tuple

import typer

from agno_cli.errors import CLIError

ADMIN_TOKEN_ENV = "AGNO_ADMIN_TOKEN"
SECURITY_KEY_ENV = "OS_SECURITY_KEY"

_SERVER_NAME_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def validate_server_name(server_name: str) -> None:
    """MCP server entry names must stay flat: a dotted name would create nested TOML
    tables in Codex's config that later reads could never find."""
    if not server_name or not set(server_name) <= _SERVER_NAME_ALLOWED:
        raise CLIError(
            "Invalid --server-name: " + server_name,
            hint="Use letters, digits, '-' and '_' only.",
        )


def resolve_admin_token(auth_mode: str, json_mode: bool) -> Optional[str]:
    """Resolve the admin credential used to call the service-accounts API.

    Order: AGNO_ADMIN_TOKEN env, OS_SECURITY_KEY env, interactive prompt. Prompting is
    disabled in --json mode and when stdin is not a TTY, so agent-driven runs fail with
    a clear instruction instead of hanging.
    """
    if auth_mode == "none":
        return None
    token = os.environ.get(ADMIN_TOKEN_ENV) or os.environ.get(SECURITY_KEY_ENV)
    if token:
        return token
    if json_mode or not sys.stdin.isatty():
        raise CLIError(
            "This AgentOS requires an admin credential to mint tokens (auth mode: " + auth_mode + ").",
            hint="Set " + ADMIN_TOKEN_ENV + " (or " + SECURITY_KEY_ENV + ") and re-run.",
        )
    return typer.prompt("Admin credential for this AgentOS", hide_input=True)


def parse_expires(value: str) -> Tuple[Optional[int], bool]:
    """Parse an --expires value into (expires_in_days, never_expires).

    Accepts a bare number of days ("90"), a d-suffixed form ("90d"), or "never".
    """
    cleaned = value.strip().lower()
    if cleaned == "never":
        return None, True
    if cleaned.endswith("d"):
        cleaned = cleaned[:-1]
    if not cleaned.isdigit() or int(cleaned) < 1:
        raise CLIError(
            "Invalid --expires value: " + value,
            hint="Use a number of days like '90' or '90d', or 'never'.",
        )
    return int(cleaned), False


def handle_cli_error(error: CLIError, json_mode: bool) -> "typer.Exit":
    """Print a CLIError appropriately for the output mode and return the Exit to raise."""
    from agno_cli.console import emit_json, print_error, print_warning

    if json_mode:
        payload = {"error": error.message}
        if error.hint:
            payload["hint"] = error.hint
        emit_json(payload)
    else:
        print_error(error.message)
        if error.hint:
            print_warning(error.hint)
    return typer.Exit(error.exit_code)
