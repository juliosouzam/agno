"""Unit tests for Tier 2 of mcp_auth: bring-your-own authorization server.

Phase 3 of the mcp_auth spec. A ``RemoteAuthProvider`` (the shape of WorkOS
``AuthKitProvider``) with a local token verifier stands in for the external AS, so the
provider-agnostic seam is proven offline:

  1. The exemption surface is minimal -- the MCP path plus the protected-resource
     metadata; the AS endpoints live on the external domain and nothing else on the
     parent app is un-authenticated.
  2. Discovery advertises the external authorization server; ``/mcp`` challenges.
  3. Externally-issued tokens verify through the provider and bridge onto
     request.state; PATs keep working via MultiAuth.
  4. ``AuthKitProvider`` itself constructs against this seam (no network at build
     time) and serves the same minimal surface.

The live proof against a real WorkOS AuthKit tenant (DCR + JWKS over the network) is a
deployment test, not a unit test -- plan-v0.md Phase 3 tracks it.
"""

import pytest

pytest.importorskip("fastmcp")

import time  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from uuid import uuid4  # noqa: E402

import httpx  # noqa: E402
from fastmcp.server.auth import RemoteAuthProvider  # noqa: E402
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier  # noqa: E402
from pydantic import AnyHttpUrl  # noqa: E402
from starlette.middleware import Middleware  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402

from agno.agent import Agent  # noqa: E402
from agno.db.schemas.service_accounts import ServiceAccount  # noqa: E402
from agno.os import AgentOS, MCPServerConfig  # noqa: E402
from agno.os.mcp_auth import mcp_auth_route_paths  # noqa: E402
from agno.os.service_accounts import generate_token  # noqa: E402

_MCP_INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
_MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
_EXTERNAL_AS = "https://auth.example.com/"
_EXTERNAL_TOKEN = "external-as-issued-token"


def _agent() -> Agent:
    return Agent(id="demo-agent", name="Demo Agent")


async def _ok_tool(message: str) -> str:
    return message


def _tier2_provider() -> RemoteAuthProvider:
    """An external-AS resource server: local verification, remote authorization."""
    verifier = StaticTokenVerifier(
        tokens={_EXTERNAL_TOKEN: {"client_id": "external-user@example.com", "scopes": ["agents:run"]}}
    )
    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[AnyHttpUrl(_EXTERNAL_AS)],
        base_url="http://localhost",
    )


def _os(provider, db=None, **config_kwargs) -> AgentOS:
    return AgentOS(
        agents=[_agent()],
        db=db,
        enable_mcp_server=True,
        mcp_auth=provider,
        mcp_config=MCPServerConfig(tools=[_ok_tool], enable_builtin_tools=False, **config_kwargs),
    )


@asynccontextmanager
async def _http_client(os: AgentOS):
    app = os.get_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            yield client


def test_tier2_exemption_surface_is_minimal():
    """A RemoteAuthProvider serves no AS endpoints locally: only the MCP path and the
    protected-resource metadata are exempted from the parent auth layer."""
    os = _os(_tier2_provider())
    paths = mcp_auth_route_paths(os._get_mcp_auth_provider())
    assert "/mcp" in paths
    assert any(p.startswith("/.well-known/oauth-protected-resource") for p in paths)
    for as_path in ("/authorize", "/token", "/register", "/revoke"):
        assert as_path not in paths


async def test_tier2_discovery_points_at_external_as():
    async with _http_client(_os(_tier2_provider())) as client:
        metadata = await client.get("/.well-known/oauth-protected-resource/mcp")
        challenge = await client.post("/mcp", json=_MCP_INIT_BODY, headers=_MCP_HEADERS)

    assert metadata.status_code == 200
    payload = metadata.json()
    assert payload["resource"].rstrip("/") == "http://localhost/mcp"
    assert [s.rstrip("/") for s in payload["authorization_servers"]] == [_EXTERNAL_AS.rstrip("/")]
    assert challenge.status_code == 401
    assert "resource_metadata=" in challenge.headers.get("www-authenticate", "")


async def test_tier2_external_token_bridges_identity():
    captured: dict = {}

    class _CaptureState(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
            response = await call_next(request)
            if request.url.path == "/mcp":
                captured["user_id"] = getattr(request.state, "user_id", None)
                captured["scopes"] = getattr(request.state, "scopes", None)
            return response

    os = _os(_tier2_provider(), middleware=[Middleware(_CaptureState)])
    async with _http_client(os) as client:
        response = await client.post(
            "/mcp", json=_MCP_INIT_BODY, headers={**_MCP_HEADERS, "Authorization": f"Bearer {_EXTERNAL_TOKEN}"}
        )

    assert response.status_code == 200
    assert captured["user_id"] == "external-user@example.com"
    assert captured["scopes"] == ["agents:run"]


async def test_tier2_invalid_token_rejected():
    async with _http_client(_os(_tier2_provider())) as client:
        response = await client.post(
            "/mcp", json=_MCP_INIT_BODY, headers={**_MCP_HEADERS, "Authorization": "Bearer not-issued-by-the-as"}
        )
    assert response.status_code == 401


async def test_tier2_pat_coexistence(tmp_path):
    from agno.db.sqlite import SqliteDb

    db = SqliteDb(db_file=str(tmp_path / "tier2.db"))
    plaintext, token_hash, token_prefix = generate_token()
    db.create_service_account(
        ServiceAccount(
            id=str(uuid4()),
            name="tier2-bot",
            token_hash=token_hash,
            token_prefix=token_prefix,
            scopes=["agents:run"],
            created_at=int(time.time()),
        ).to_dict()
    )
    async with _http_client(_os(_tier2_provider(), db=db)) as client:
        response = await client.post(
            "/mcp", json=_MCP_INIT_BODY, headers={**_MCP_HEADERS, "Authorization": f"Bearer {plaintext}"}
        )
    assert response.status_code == 200


async def test_tier2_info_reports_external_as():
    async with _http_client(_os(_tier2_provider())) as client:
        response = await client.get("/info")
    payload = response.json()
    assert payload["auth_mode"] == "oauth"
    assert [s.rstrip("/") for s in payload["mcp"]["oauth"]["authorization_servers"]] == [_EXTERNAL_AS.rstrip("/")]


def test_authkit_provider_fits_the_seam():
    """The documented Tier-2 default constructs offline and serves the same minimal
    surface (its AS endpoints live on the AuthKit domain)."""
    try:
        from fastmcp.server.auth.providers.workos import AuthKitProvider
    except ImportError:  # pragma: no cover
        pytest.skip("workos provider not available in this fastmcp build")

    provider = AuthKitProvider(authkit_domain="https://example-tenant.authkit.app", base_url="http://localhost")
    paths = [getattr(r, "path", "") for r in provider.get_routes(mcp_path="/mcp")]
    assert any(p.startswith("/.well-known/oauth-protected-resource") for p in paths)
    for as_path in ("/authorize", "/token", "/register"):
        assert as_path not in paths
