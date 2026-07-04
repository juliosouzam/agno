"""`agno connect`: wire a running AgentOS into coding agents as an MCP server.

Flow: discover the AgentOS -> resolve admin credential -> mint one service account per
client -> write each client's MCP config -> verify each connection with a real MCP
tools/list call -> report. Re-runs are safe: entries that already verify are skipped,
broken or stale ones are rotated (revoke + re-mint + rewrite).
"""

import sys
from typing import Any, Dict, List, Optional

import typer

from agno_cli.clients import CLIENT_ALIASES, build_adapters
from agno_cli.clients.base import ClientAdapter
from agno_cli.commands._common import handle_cli_error, parse_expires, resolve_admin_token
from agno_cli.console import emit_json, print_error, print_info, print_success, print_warning
from agno_cli.discovery import MCP_ENABLE_INSTRUCTIONS, OSInfo, discover
from agno_cli.errors import CLIError, ConflictError
from agno_cli.http import AgentOSAPI, ServiceAccount
from agno_cli.mcp_client import verify_mcp


def _resolve_clients(clients: Optional[str], adapters: Dict[str, ClientAdapter]) -> List[ClientAdapter]:
    if clients:
        selected: List[ClientAdapter] = []
        for raw in clients.split(","):
            key = CLIENT_ALIASES.get(raw.strip().lower())
            if key is None:
                supported = ", ".join(sorted(set(CLIENT_ALIASES.keys())))
                raise CLIError("Unknown client: " + raw.strip(), hint="Supported clients: " + supported)
            adapter = adapters[key]
            if adapter not in selected:
                selected.append(adapter)
        return selected
    detected = [adapter for adapter in adapters.values() if adapter.detect()]
    if not detected:
        raise CLIError(
            "No supported coding agents detected on this machine.",
            hint="Install Claude Code, Codex, or Cursor, or pass --clients explicitly.",
        )
    return detected


def _mint(
    api: AgentOSAPI,
    account_name: str,
    scopes: Optional[List[str]],
    expires_in_days: Optional[int],
    never_expires: bool,
    rotate: bool,
    skip_existing: bool,
    json_mode: bool,
) -> Optional[ServiceAccount]:
    """Mint a service account, resolving name conflicts per the idempotency policy.

    Returns None when the caller should skip this client (existing account kept).
    """
    try:
        return api.create_service_account(
            name=account_name,
            scopes=scopes,
            expires_in_days=expires_in_days,
            never_expires=never_expires,
        )
    except ConflictError:
        if skip_existing:
            return None
        if not rotate:
            interactive = not json_mode and sys.stdin.isatty()
            if not interactive:
                raise
            if not typer.confirm(
                "Service account '" + account_name + "' already exists. Revoke and re-mint it?", default=False
            ):
                return None
        existing = api.find_service_account(account_name)
        if existing is not None:
            api.revoke_service_account(existing.id)
        return api.create_service_account(
            name=account_name,
            scopes=scopes,
            expires_in_days=expires_in_days,
            never_expires=never_expires,
        )


def connect(
    url: Optional[str] = typer.Option(None, "--url", help="AgentOS base URL (default: autodiscover on localhost)."),
    clients: Optional[str] = typer.Option(
        None, "--clients", help="Comma-separated clients to configure (claude-code,codex,cursor). Default: detected."
    ),
    name: Optional[str] = typer.Option(
        None, "--name", help="Use one shared service account with this name instead of one per client."
    ),
    scopes: List[str] = typer.Option(
        [], "--scopes", "-s", help="Scope to grant (repeatable). Default: the server's run + read scopes."
    ),
    expires: str = typer.Option("90d", "--expires", help="Token lifetime in days ('90d', '30') or 'never'."),
    server_name: str = typer.Option("agno", "--server-name", help="MCP server entry name written to client configs."),
    project: bool = typer.Option(
        False, "--project", help="Write project-scoped configs (.mcp.json / .cursor/mcp.json) instead of user-level."
    ),
    rotate: bool = typer.Option(False, "--rotate", help="Revoke and re-mint existing accounts without asking."),
    skip_existing: bool = typer.Option(
        False, "--skip-existing", help="Never touch existing accounts or config entries."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit a single JSON document for machine consumption."),
) -> None:
    """Connect this machine's coding agents to a running AgentOS over MCP."""
    try:
        _connect(
            url=url,
            clients=clients,
            name=name,
            scopes=list(scopes) or None,
            expires=expires,
            server_name=server_name,
            project=project,
            rotate=rotate,
            skip_existing=skip_existing,
            json_mode=json_output,
        )
    except CLIError as e:
        raise handle_cli_error(e, json_output)


def _connect(
    url: Optional[str],
    clients: Optional[str],
    name: Optional[str],
    scopes: Optional[List[str]],
    expires: str,
    server_name: str,
    project: bool,
    rotate: bool,
    skip_existing: bool,
    json_mode: bool,
) -> None:
    expires_in_days, never_expires = parse_expires(expires)

    os_info = discover(url)
    if not os_info.mcp_enabled:
        raise CLIError(
            "Found an AgentOS at "
            + os_info.base_url
            + ", but its MCP server is not enabled.\n\n"
            + MCP_ENABLE_INSTRUCTIONS
        )
    if not json_mode:
        version = " (agno " + os_info.version + ")" if os_info.version else ""
        print_info("AgentOS at " + os_info.base_url + version + ", MCP at " + os_info.mcp_url)

    adapters = build_adapters(project=project)
    selected = _resolve_clients(clients, adapters)

    minting = os_info.auth_mode not in ("none",)
    admin_token = resolve_admin_token(os_info.auth_mode, json_mode) if minting else None
    if not minting and not json_mode:
        print_info("Authorization is disabled on this AgentOS; connecting without credentials.")

    # Truthful-outcome check: if the OS enforces auth but /mcp answers without a token,
    # the server predates MCP token enforcement and minted headers are decorative.
    open_mcp_warning: Optional[str] = None
    if minting:
        open_probe = verify_mcp(os_info.mcp_url, token=None)
        if open_probe.ok:
            open_mcp_warning = (
                "This AgentOS accepts unauthenticated MCP requests: its agno version predates "
                "token enforcement on /mcp. Tokens were still minted and configured; upgrade "
                "agno to make them meaningful."
            )

    api = AgentOSAPI(os_info.base_url, admin_token=admin_token) if minting else None

    shared_account: Optional[ServiceAccount] = None
    results: List[Dict[str, Any]] = []
    revoke_hints: List[str] = []

    try:
        for adapter in selected:
            result: Dict[str, Any] = {"client": adapter.key, "status": "failed", "error": None}
            try:
                account_name = name or adapter.key
                existing = adapter.read_existing(server_name)

                # Idempotency: an entry already pointing at this OS that still verifies is left alone.
                if existing is not None and existing.url == os_info.mcp_url and not rotate:
                    check = verify_mcp(os_info.mcp_url, token=existing.token)
                    if check.ok and (existing.token is not None or not minting):
                        result.update(
                            status="already-connected",
                            location=existing.location,
                            verify=check.public_dict(),
                        )
                        results.append(result)
                        continue
                    if skip_existing:
                        result.update(
                            status="skipped",
                            error="Existing entry no longer verifies; re-run without --skip-existing to rotate.",
                        )
                        results.append(result)
                        continue

                token: Optional[str] = None
                account: Optional[ServiceAccount] = None
                if minting and api is not None:
                    if name is not None and shared_account is not None:
                        account = shared_account
                    else:
                        account = _mint(
                            api,
                            account_name,
                            scopes,
                            expires_in_days,
                            never_expires,
                            rotate=rotate,
                            skip_existing=skip_existing,
                            json_mode=json_mode,
                        )
                        if account is None:
                            result.update(status="skipped", error="Service account exists; kept untouched.")
                            results.append(result)
                            continue
                        if name is not None:
                            shared_account = account
                    token = account.token

                write_result = adapter.write(server_name, os_info.mcp_url, token)
                verify_result = verify_mcp(os_info.mcp_url, token=token)

                result.update(location=write_result.location, verify=verify_result.public_dict())
                if account is not None:
                    result["account"] = account.public_dict()
                    revoke_hints.append(account.name)
                if verify_result.ok:
                    result["status"] = "connected"
                else:
                    result["error"] = verify_result.error
            except CLIError as e:
                result["error"] = e.message
            results.append(result)
    finally:
        if api is not None:
            api.close()

    _report(os_info, results, server_name, open_mcp_warning, json_mode)


def _report(
    os_info: OSInfo,
    results: List[Dict[str, Any]],
    server_name: str,
    open_mcp_warning: Optional[str],
    json_mode: bool,
) -> None:
    ok_statuses = ("connected", "already-connected")
    ok_count = sum(1 for r in results if r["status"] in ok_statuses)
    if ok_count == len(results):
        exit_code = 0
    elif ok_count == 0:
        exit_code = 1
    else:
        exit_code = 2

    if json_mode:
        emit_json(
            {
                "os": os_info.public_dict(),
                "server_name": server_name,
                "results": results,
                "warning": open_mcp_warning,
                "exit_code": exit_code,
            }
        )
        raise typer.Exit(exit_code)

    print_info("")
    for r in results:
        label = r["client"]
        if r["status"] == "connected":
            tools = (r.get("verify") or {}).get("tools")
            suffix = " (" + str(tools) + " tools)" if tools else ""
            print_success("  connected      " + label + suffix + "  ->  " + str(r.get("location", "")))
        elif r["status"] == "already-connected":
            print_success("  already ok     " + label + "  ->  " + str(r.get("location", "")))
        elif r["status"] == "skipped":
            print_warning("  skipped        " + label + "  (" + str(r.get("error") or "") + ")")
        else:
            print_error("  failed         " + label + "  (" + str(r.get("error") or "unknown error") + ")")

    accounts = sorted({r["account"]["name"] for r in results if r.get("account")})
    if accounts:
        print_info("")
        print_info("Revoke any time with: agno tokens revoke <name>  (accounts: " + ", ".join(accounts) + ")")
    if open_mcp_warning:
        print_info("")
        print_warning(open_mcp_warning)

    raise typer.Exit(exit_code)
