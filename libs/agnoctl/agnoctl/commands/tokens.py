"""`agno tokens`: thin wrappers over the AgentOS service-accounts API."""

from datetime import datetime, timezone
from typing import List, Optional

import typer

from agnoctl.commands._common import handle_cli_error, parse_expires, resolve_admin_token
from agnoctl.console import console, emit_json, print_info, print_success, print_warning
from agnoctl.discovery import discover
from agnoctl.errors import CLIError, ConflictError
from agnoctl.http import AgentOSAPI

tokens_app = typer.Typer(name="tokens", help="Mint, list, and revoke AgentOS service-account tokens.")

UrlOption = typer.Option(None, "--url", help="AgentOS base URL (default: autodiscover on localhost).")
JsonOption = typer.Option(False, "--json", help="Emit a single JSON document for machine consumption.")


def _timestamp(value: Optional[int]) -> str:
    if not value:
        return "-"
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _api_for(url: Optional[str], json_mode: bool) -> AgentOSAPI:
    os_info = discover(url)
    admin_token = resolve_admin_token(os_info.auth_mode, json_mode)
    return AgentOSAPI(os_info.base_url, admin_token=admin_token)


@tokens_app.command("create")
def create(
    name: str = typer.Argument(..., help="Service account name (lowercase slug, e.g. 'ci-runner')."),
    scopes: List[str] = typer.Option([], "--scopes", "-s", help="Scope to grant (repeatable). Default: run + read."),
    expires: str = typer.Option("90d", "--expires", help="Days until expiry ('90d', '30') or 'never'."),
    privileged: bool = typer.Option(
        False, "--privileged", help="Required to grant write/delete/admin or service_accounts scopes."
    ),
    url: Optional[str] = UrlOption,
    json_output: bool = JsonOption,
) -> None:
    """Mint a service-account token. The plaintext is shown exactly once."""
    try:
        expires_in_days, never_expires = parse_expires(expires)
        with _api_for(url, json_output) as api:
            try:
                account = api.create_service_account(
                    name=name,
                    scopes=list(scopes) or None,
                    expires_in_days=expires_in_days,
                    never_expires=never_expires,
                    allow_privileged_scopes=privileged,
                )
            except ConflictError as e:
                e.hint = "Revoke it first (agno tokens revoke " + name + ") or pick a different name."
                raise
    except CLIError as e:
        raise handle_cli_error(e, json_output)

    if json_output:
        payload = account.public_dict()
        payload["token"] = account.token
        emit_json(payload)
        return

    print_success("Service account '" + account.name + "' created (principal: " + account.principal + ").")
    print_info("Scopes: " + ", ".join(account.scopes))
    print_info("Expires: " + _timestamp(account.expires_at))
    print_warning("This token is shown once and cannot be retrieved again. Save it now:")
    console.print()
    console.print("    " + (account.token or ""), style="bold", highlight=False)
    console.print()


@tokens_app.command("list")
def list_(
    url: Optional[str] = UrlOption,
    json_output: bool = JsonOption,
) -> None:
    """List service accounts (metadata and display prefixes only, never tokens)."""
    try:
        with _api_for(url, json_output) as api:
            accounts = api.list_service_accounts()
    except CLIError as e:
        raise handle_cli_error(e, json_output)

    if json_output:
        emit_json({"service_accounts": [a.public_dict() for a in accounts]})
        return

    if not accounts:
        print_info("No service accounts found.")
        return

    from rich.table import Table

    table = Table(box=None, header_style="bold")
    for column in ("Name", "Token", "Scopes", "Expires", "Last used", "Status"):
        table.add_column(column)
    for a in accounts:
        status = "revoked" if a.revoked_at else "active"
        table.add_row(
            a.name,
            a.token_prefix + "…",
            ", ".join(a.scopes),
            _timestamp(a.expires_at),
            _timestamp(a.last_used_at),
            status,
        )
    console.print(table)


@tokens_app.command("revoke")
def revoke(
    name: str = typer.Argument(..., help="Name of the service account to revoke."),
    url: Optional[str] = UrlOption,
    json_output: bool = JsonOption,
) -> None:
    """Revoke a service account. Takes effect on the account's next request."""
    try:
        with _api_for(url, json_output) as api:
            account = api.find_service_account(name)
            if account is None:
                raise CLIError("No service account named '" + name + "' found.")
            api.revoke_service_account(account.id)
    except CLIError as e:
        raise handle_cli_error(e, json_output)

    if json_output:
        emit_json({"revoked": account.public_dict()})
        return
    print_success("Service account '" + name + "' revoked. Existing tokens stop working on their next request.")
