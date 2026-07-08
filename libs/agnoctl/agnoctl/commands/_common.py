"""Helpers shared by CLI commands."""

import os
import sys
from typing import Optional, Tuple
from urllib.parse import urlsplit

import typer

from agnoctl.discovery import _is_loopback_host
from agnoctl.errors import CLIError

ADMIN_TOKEN_ENV = "AGNO_ADMIN_TOKEN"
SECURITY_KEY_ENV = "OS_SECURITY_KEY"

_SERVER_NAME_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")

# MCP entry name when the AgentOS serves no usable name (servers predating the /info
# name field, or a name that slugs to nothing). agnoctl 0.1.x named every entry
# LEGACY_SERVER_NAME; connect recognizes those entries and renames them in place.
DEFAULT_SERVER_NAME = "agentos"
LEGACY_SERVER_NAME = "agno"


def derive_server_name(os_name: Optional[str]) -> str:
    """The MCP entry name derived from an AgentOS instance name.

    Client configs should read "customer-support" (the OS's identity), not a framework
    constant. Slugified to the validate_server_name charset: lowercased, spaces and dots
    become "-", anything else outside the charset is dropped, runs collapse.
    """
    if not os_name:
        return DEFAULT_SERVER_NAME
    chars = []
    for ch in os_name.lower():
        if ch in " .":
            chars.append("-")
        elif ch in _SERVER_NAME_ALLOWED:
            chars.append(ch)
    slug = "".join(chars)
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-")
    return slug or DEFAULT_SERVER_NAME


def validate_server_name(server_name: str) -> None:
    """MCP server entry names must stay flat: a dotted name would create nested TOML
    tables in Codex's config that later reads could never find."""
    if not server_name or not set(server_name) <= _SERVER_NAME_ALLOWED:
        raise CLIError(
            "Invalid --server-name: " + server_name,
            hint="Use letters, digits, '-' and '_' only.",
        )


def validate_project_name(name: str) -> None:
    """A project name becomes a directory under the CWD, so it must be a single flat path
    segment. Reject absolute paths, path separators, and '..' (which would let a scaffold
    escape the CWD or clobber an arbitrary directory) by holding it to the same
    conservative character set as MCP server names."""
    if not name or not set(name) <= _SERVER_NAME_ALLOWED:
        raise CLIError(
            "Invalid project name: " + name,
            hint="Use a single directory name of letters, digits, '-' and '_' only (no '/', '..' or absolute paths).",
        )


def require_secure_url(url: str, *, allow_http: bool, what: str = "a credential") -> None:
    """Refuse to attach a bearer credential over plaintext HTTP to a non-loopback host.

    The admin token and freshly minted PATs are sent as ``Authorization: Bearer`` headers;
    over http:// to a remote host that hands the secret to anyone on the path. TLS is
    required unless the target is loopback (local dev) or the operator has explicitly
    opted in with ``--allow-http`` for a trusted private network.
    """
    if allow_http:
        return
    parts = urlsplit(url)
    if parts.scheme.lower() == "https":
        return
    if _is_loopback_host(parts.hostname):
        return
    raise CLIError(
        "Refusing to send " + what + " to " + url + " over plaintext HTTP.",
        hint="Use an https:// URL, or pass --allow-http to override on a trusted private network.",
    )


def ensure_env_file_url_trusted(
    base_url: str,
    url_source: str,
    url_source_file: Optional[str],
    *,
    assume_yes: bool,
    json_mode: bool,
) -> None:
    """Guard the ambient-env-file redirect before a credential or config write.

    An AGENTOS_URL read from a .env / .env.production in the current directory is trusted
    automatically only when it points at this machine (localhost / 127.x / ::1). A URL that
    points at a remote host could have been planted by an untrusted directory to redirect the
    admin credential or rewrite MCP client configs, so require an explicit go-ahead: prompt
    interactively (default yes -- the operator eyeballs the URL, and it is almost always the
    one their own deploy just wrote), and refuse outright in automation (--json or no TTY)
    unless --yes was passed, since a headless run has no one looking at the URL. An explicit
    --url or an exported AGENTOS_URL env var is not an ambient file and is trusted as before.
    """
    if url_source != "env-file":
        return
    if _is_loopback_host(urlsplit(base_url).hostname):
        return
    if assume_yes:
        return
    source = url_source_file or "an env file"
    if json_mode or not stdin_is_interactive():
        raise CLIError(
            "AGENTOS_URL in " + source + " points to a remote host (" + base_url + ").",
            hint="Pass --url to target it explicitly, or --yes to trust the env file.",
        )
    if not typer.confirm("Trust AGENTOS_URL=" + base_url + " (from " + source + ")?", default=True):
        raise CLIError("Aborted: did not trust the env-file URL " + base_url + ".")


# Where a human gets an admin credential when none is in the environment. The UI path
# ships with the AgentOS control plane; on a JWT-mode OS it is the only sanctioned source.
ADMIN_TOKEN_SOURCES = (
    "Generate a short-lived admin token in the AgentOS UI (Settings -> OS & Security), or set "
    + ADMIN_TOKEN_ENV
    + " (or "
    + SECURITY_KEY_ENV
    + ")."
)


def require_mint_capable(base_url: str, auth_mode: str, or_else: Optional[str] = None) -> None:
    """Fail fast when this AgentOS cannot mint tokens for anyone.

    auth_mode "oauth" covers two very different deployments: OAuth composed with a REST
    credential (OS_SECURITY_KEY / JWT -- minting works), and OAuth as the only auth,
    leaving the REST plane open. The open one refuses every mint while accepting any
    credential on reads, so resolving a credential would prompt for -- and appear to
    accept -- a value the mint then rejects. Detect the open plane up front and name
    the real problem: the server, not the credential.
    """
    if auth_mode != "oauth":
        return
    from agnoctl.http import service_accounts_open

    if not service_accounts_open(base_url):
        return
    hint = "Set " + SECURITY_KEY_ENV + " (or configure JWT auth) on the server to enable minting."
    if or_else:
        hint += " " + or_else
    raise CLIError(
        "This AgentOS cannot mint tokens: OAuth protects its MCP endpoint, but its REST API has "
        "no authentication configured, and the server refuses to mint without an authenticated caller.",
        hint=hint,
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
            hint=ADMIN_TOKEN_SOURCES,
        )
    from agnoctl.console import print_info

    print_info(ADMIN_TOKEN_SOURCES)
    return typer.prompt("Admin credential for this AgentOS", hide_input=True)


def stdin_is_interactive() -> bool:
    """Whether we can prompt the user. False in automation contexts (non-TTY); commands
    also treat --json as non-interactive. Wrapped so it can be stubbed in tests, where the
    runner swaps sys.stdin out from under a direct isatty patch."""
    return sys.stdin.isatty()


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
    from agnoctl.console import emit_json, print_error, print_warning

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
