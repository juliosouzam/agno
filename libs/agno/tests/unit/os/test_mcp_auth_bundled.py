"""Unit tests for the Bundled Authorization Server (Tier 1 of mcp_auth).

Phase 2 of the mcp_auth spec, exercised over the full AgentOS app on SQLite (the same
SQLAlchemy store code paths as Postgres):

  1. The full connector dance: DCR -> /authorize -> consent page (secret + CSRF) ->
     code -> PKCE token exchange -> MCP request with the identity bridged.
  2. The consent gate is real: wrong secret rejected (and throttled), CSRF mismatch
     rejected, deny redirects with access_denied, the page never renders without a
     valid pending transaction, and framing is denied.
  3. Token lifecycle: codes are single-use; refresh tokens rotate on every use (a
     replayed refresh token fails); server-decided scopes (client requests never
     expand the grant); revocation deletes refresh state.
  4. Persistence: tokens survive a "redeploy" (a second provider instance on the same
     database verifies tokens issued by the first -- the two-replica smoke), and
     nothing replayable is stored in the database (hash-at-rest).
"""

import pytest

pytest.importorskip("fastmcp")

import base64  # noqa: E402
import hashlib  # noqa: E402
import re  # noqa: E402
import secrets  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from urllib.parse import parse_qs, urlparse  # noqa: E402

import httpx  # noqa: E402
from sqlalchemy import inspect as sa_inspect  # noqa: E402
from sqlalchemy import text  # noqa: E402

from agno.agent import Agent  # noqa: E402
from agno.os import AgentOS, MCPServerConfig  # noqa: E402
from agno.os.mcp_auth_bundled import CONSENT_PATH, DEFAULT_GRANT_SCOPES, AgentOSBundledAuth  # noqa: E402

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
_REDIRECT_URI = "http://localhost:9999/callback"
_SECRET = "test-connect-secret"


def _agent() -> Agent:
    return Agent(id="demo-agent", name="Demo Agent")


async def _ok_tool(message: str) -> str:
    return message


def _db(tmp_path):
    from agno.db.sqlite import SqliteDb

    return SqliteDb(db_file=str(tmp_path / "bundled_as.db"))


def _provider(db, **kwargs) -> AgentOSBundledAuth:
    kwargs.setdefault("base_url", "http://localhost")
    kwargs.setdefault("connect_secret", _SECRET)
    return AgentOSBundledAuth(db=db, **kwargs)


def _os(provider, db=None) -> AgentOS:
    return AgentOS(
        agents=[_agent()],
        db=db,
        enable_mcp_server=True,
        mcp_auth=provider,
        mcp_config=MCPServerConfig(tools=[_ok_tool], enable_builtin_tools=False),
    )


@asynccontextmanager
async def _http_client(os: AgentOS):
    app = os.get_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            yield client


def _pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def _register(client, scope="agents:run sessions:read", auth_method="none"):
    response = await client.post(
        "/register",
        json={
            "client_name": "Test Connector",
            "redirect_uris": [_REDIRECT_URI],
            "token_endpoint_auth_method": auth_method,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": scope,
        },
    )
    return response


async def _start_authorization(client, client_id, scope="agents:run"):
    """DCR client drives /authorize and lands on the consent page. Returns (page, txn, csrf, verifier)."""
    verifier, challenge = _pkce_pair()
    authorization = await client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": _REDIRECT_URI,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "s",
            "scope": scope,
        },
        follow_redirects=False,
    )
    assert authorization.status_code in (302, 307), authorization.text
    consent_url = authorization.headers["location"]
    assert CONSENT_PATH in consent_url
    page = await client.get(consent_url)
    assert page.status_code == 200
    txn = re.search(r'name="txn" value="([^"]+)"', page.text).group(1)
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    return page, txn, csrf, verifier


async def _approve(client, txn, csrf, secret=_SECRET, action="approve"):
    return await client.post(
        CONSENT_PATH,
        data={"txn": txn, "csrf": csrf, "secret": secret, "action": action},
        follow_redirects=False,
    )


async def _exchange_code(client, client_id, code, verifier):
    return await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )


async def _full_flow(client, scope="agents:run"):
    """DCR -> authorize -> consent approve -> token. Returns the token payload + client_id."""
    registration = await _register(client)
    assert registration.status_code == 201, registration.text
    client_id = registration.json()["client_id"]
    _, txn, csrf, verifier = await _start_authorization(client, client_id, scope=scope)
    approved = await _approve(client, txn, csrf)
    assert approved.status_code == 302, approved.text
    query = parse_qs(urlparse(approved.headers["location"]).query)
    assert "code" in query, approved.headers["location"]
    token_response = await _exchange_code(client, client_id, query["code"][0], verifier)
    assert token_response.status_code == 200, token_response.text
    return token_response.json(), client_id


# ==================== The full connector dance ====================


async def test_full_flow_connects_and_runs_mcp(tmp_path):
    db = _db(tmp_path)
    async with _http_client(_os(_provider(db), db=db)) as client:
        tokens, client_id = await _full_flow(client)
        response = await client.post(
            "/mcp", json=_MCP_INIT_BODY, headers={**_MCP_HEADERS, "Authorization": f"Bearer {tokens['access_token']}"}
        )

    assert response.status_code == 200
    assert tokens["token_type"].lower() == "bearer"
    assert tokens["refresh_token"]


async def test_discovery_and_challenge(tmp_path):
    db = _db(tmp_path)
    async with _http_client(_os(_provider(db), db=db)) as client:
        metadata = await client.get("/.well-known/oauth-authorization-server")
        challenge = await client.post("/mcp", json=_MCP_INIT_BODY, headers=_MCP_HEADERS)

    assert metadata.status_code == 200
    assert metadata.json()["issuer"].rstrip("/") == "http://localhost"
    assert challenge.status_code == 401
    assert "resource_metadata=" in challenge.headers.get("www-authenticate", "")


# ==================== The consent gate ====================


async def test_wrong_secret_rejected_then_throttled(tmp_path):
    db = _db(tmp_path)
    provider = _provider(db, max_login_failures_per_ip=3)
    async with _http_client(_os(provider, db=db)) as client:
        registration = await _register(client)
        client_id = registration.json()["client_id"]
        _, txn, csrf, _ = await _start_authorization(client, client_id)

        first = await _approve(client, txn, csrf, secret="wrong")
        assert first.status_code == 200
        assert "Wrong connect secret" in first.text

        # Keep sending wrong secrets (fresh CSRF from each re-render) until throttled.
        status = 200
        for _ in range(5):
            match = re.search(r'name="csrf" value="([^"]+)"', first.text)
            csrf = match.group(1) if match else csrf
            first = await _approve(client, txn, csrf, secret="wrong")
            status = first.status_code
            if status == 429:
                break
        assert status == 429


async def test_correct_secret_is_never_throttled(tmp_path):
    """The limiter shapes only the FAILURE response: a flood of wrong-secret attempts
    (the DCR/authorize endpoints are unauthenticated) must not lock out a correct login."""
    db = _db(tmp_path)
    # A global limiter of 1 is saturated by a single wrong attempt from any source.
    provider = _provider(db, max_login_failures_global=1)
    async with _http_client(_os(provider, db=db)) as client:
        reg_a = await _register(client)
        _, txn_a, csrf_a, _ = await _start_authorization(client, reg_a.json()["client_id"])
        saturate = await _approve(client, txn_a, csrf_a, secret="wrong")
        assert saturate.status_code in (200, 429)

        # A fresh, legitimate connection with the correct secret still succeeds.
        reg_b = await _register(client)
        _, txn_b, csrf_b, _ = await _start_authorization(client, reg_b.json()["client_id"])
        approved = await _approve(client, txn_b, csrf_b, secret=_SECRET)
    assert approved.status_code == 302
    assert "code=" in approved.headers["location"]


async def test_csrf_mismatch_rejected(tmp_path):
    db = _db(tmp_path)
    async with _http_client(_os(_provider(db), db=db)) as client:
        registration = await _register(client)
        client_id = registration.json()["client_id"]
        _, txn, _, _ = await _start_authorization(client, client_id)
        response = await _approve(client, txn, csrf="forged-token")
    assert response.status_code == 400


async def test_deny_redirects_with_access_denied(tmp_path):
    db = _db(tmp_path)
    async with _http_client(_os(_provider(db), db=db)) as client:
        registration = await _register(client)
        client_id = registration.json()["client_id"]
        _, txn, csrf, _ = await _start_authorization(client, client_id)
        response = await _approve(client, txn, csrf, action="deny")
    assert response.status_code == 302
    assert "error=access_denied" in response.headers["location"]


async def test_consent_page_requires_valid_transaction(tmp_path):
    db = _db(tmp_path)
    async with _http_client(_os(_provider(db), db=db)) as client:
        response = await client.get(f"{CONSENT_PATH}?txn=not-a-real-transaction")
    assert response.status_code == 404


async def test_consent_page_denies_framing(tmp_path):
    db = _db(tmp_path)
    async with _http_client(_os(_provider(db), db=db)) as client:
        registration = await _register(client)
        client_id = registration.json()["client_id"]
        page, _, _, _ = await _start_authorization(client, client_id)
    assert page.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]


async def test_confidential_client_registration_rejected(tmp_path):
    db = _db(tmp_path)
    async with _http_client(_os(_provider(db), db=db)) as client:
        response = await _register(client, auth_method="client_secret_post")
    assert response.status_code == 400
    assert "public clients" in response.text


# ==================== Token lifecycle ====================


async def test_authorization_code_is_single_use(tmp_path):
    db = _db(tmp_path)
    async with _http_client(_os(_provider(db), db=db)) as client:
        registration = await _register(client)
        client_id = registration.json()["client_id"]
        _, txn, csrf, verifier = await _start_authorization(client, client_id)
        approved = await _approve(client, txn, csrf)
        code = parse_qs(urlparse(approved.headers["location"]).query)["code"][0]

        first = await _exchange_code(client, client_id, code, verifier)
        replay = await _exchange_code(client, client_id, code, verifier)

    assert first.status_code == 200
    assert replay.status_code in (400, 401)


async def test_refresh_rotates_and_old_token_dies(tmp_path):
    db = _db(tmp_path)
    async with _http_client(_os(_provider(db), db=db)) as client:
        tokens, client_id = await _full_flow(client)

        refreshed = await client.post(
            "/token",
            data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id},
        )
        assert refreshed.status_code == 200, refreshed.text
        new_tokens = refreshed.json()
        assert new_tokens["refresh_token"] != tokens["refresh_token"]

        replay = await client.post(
            "/token",
            data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id},
        )
        assert replay.status_code in (400, 401)

        # The rotated pair works: the new access token runs MCP requests.
        response = await client.post(
            "/mcp",
            json=_MCP_INIT_BODY,
            headers={**_MCP_HEADERS, "Authorization": f"Bearer {new_tokens['access_token']}"},
        )
        assert response.status_code == 200


async def test_scopes_are_server_decided(tmp_path):
    """A client requesting broader scopes (admin) gets exactly the configured grant."""
    db = _db(tmp_path)
    async with _http_client(_os(_provider(db), db=db)) as client:
        tokens, _ = await _full_flow(client, scope="agents:run sessions:read")
    assert set(tokens["scope"].split()) == set(DEFAULT_GRANT_SCOPES)


async def test_revocation_kills_refresh(tmp_path):
    db = _db(tmp_path)
    async with _http_client(_os(_provider(db), db=db)) as client:
        tokens, client_id = await _full_flow(client)
        # The SDK's RevocationRequest requires the client_secret field to be present
        # (str | None with no default); a public client sends it empty.
        revocation = await client.post(
            "/revoke",
            data={"token": tokens["refresh_token"], "client_id": client_id, "client_secret": ""},
        )
        assert revocation.status_code == 200, revocation.text
        replay = await client.post(
            "/token",
            data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id},
        )
    assert replay.status_code in (400, 401)


# ==================== Persistence ====================


async def test_tokens_survive_redeploy_and_verify_on_second_replica(tmp_path):
    """A second provider instance on the same database (a redeploy, or another replica)
    verifies tokens issued by the first: the signing key is persisted and shared, and
    access-token verification is stateless."""
    db = _db(tmp_path)
    async with _http_client(_os(_provider(db), db=db)) as client:
        tokens, client_id = await _full_flow(client)

    replica_os = _os(_provider(db), db=db)
    async with _http_client(replica_os) as client:
        access_ok = await client.post(
            "/mcp", json=_MCP_INIT_BODY, headers={**_MCP_HEADERS, "Authorization": f"Bearer {tokens['access_token']}"}
        )
        refreshed = await client.post(
            "/token",
            data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id},
        )

    assert access_ok.status_code == 200
    assert refreshed.status_code == 200, refreshed.text


async def test_nothing_replayable_stored_in_db(tmp_path):
    """Hash-at-rest: neither the issued tokens nor the authorization code appear in the
    database; only their SHA-256 hashes do."""
    db = _db(tmp_path)
    provider = _provider(db)
    async with _http_client(_os(provider, db=db)) as client:
        registration = await _register(client)
        client_id = registration.json()["client_id"]
        _, txn, csrf, verifier = await _start_authorization(client, client_id)
        approved = await _approve(client, txn, csrf)
        code = parse_qs(urlparse(approved.headers["location"]).query)["code"][0]
        token_response = await _exchange_code(client, client_id, code, verifier)
        tokens = token_response.json()

    engine = db.db_engine
    tables = [t for t in sa_inspect(engine).get_table_names() if t.startswith("agno_mcp_oauth")]
    assert tables
    with engine.connect() as conn:
        for table in tables:
            for row in conn.execute(text(f"SELECT * FROM {table}")):  # noqa: S608 - test-only introspection
                row_text = " ".join(str(v) for v in row)
                assert code not in row_text
                assert tokens["refresh_token"] not in row_text
                assert tokens["access_token"] not in row_text


async def test_env_signing_key_is_primary(tmp_path, monkeypatch):
    """With AGENTOS_MCP_SIGNING_KEY set, two providers on different databases issue
    mutually verifiable tokens (same derived key + issuer) -- the env-primary path."""
    provider_a = _provider(_db(tmp_path), signing_key_material="a-high-entropy-material")
    db_b = _db(tmp_path / "b")
    (tmp_path / "b").mkdir(exist_ok=True)
    provider_b = _provider(db_b, signing_key_material="a-high-entropy-material")

    db_a = _db(tmp_path)
    async with _http_client(_os(provider_a, db=db_a)) as client:
        tokens, _ = await _full_flow(client)

    async with _http_client(_os(provider_b, db=db_b)) as client:
        response = await client.post(
            "/mcp", json=_MCP_INIT_BODY, headers={**_MCP_HEADERS, "Authorization": f"Bearer {tokens['access_token']}"}
        )
    assert response.status_code == 200


# ==================== Construction ====================


def test_requires_connect_secret(tmp_path):
    with pytest.raises(ValueError, match="connect secret"):
        AgentOSBundledAuth(base_url="http://localhost", db=_db(tmp_path), connect_secret="")


def test_from_env_requires_public_url(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTOS_PUBLIC_URL", raising=False)
    monkeypatch.delenv("MCP_CONNECT_SECRET", raising=False)
    with pytest.raises(ValueError, match="AGENTOS_PUBLIC_URL"):
        AgentOSBundledAuth.from_env(db=_db(tmp_path))


def test_from_env_requires_connect_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOS_PUBLIC_URL", "https://my-os.example.com")
    monkeypatch.delenv("MCP_CONNECT_SECRET", raising=False)
    with pytest.raises(ValueError, match="MCP_CONNECT_SECRET"):
        AgentOSBundledAuth.from_env(db=_db(tmp_path))


def test_async_db_rejected_at_construction(tmp_path):
    """An async db must fail clearly at construction, not with an opaque 500 on the first
    request (the sync store paths cannot drive an AsyncEngine)."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from agno.db.sqlite.async_sqlite import AsyncSqliteDb

    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "async.db"))
    db = AsyncSqliteDb(db_engine=engine)
    with pytest.raises(ValueError, match="async databases"):
        _provider(db)


# ==================== RFC 8252 loopback redirect (Claude Code / mcp-remote) ====================


async def test_loopback_redirect_port_may_vary(tmp_path):
    """A CLI client registers a loopback callback, then authorizes from a different
    ephemeral port on a later run -- the second port must be accepted (RFC 8252)."""
    db = _db(tmp_path)
    async with _http_client(_os(_provider(db), db=db)) as client:
        registration = await client.post(
            "/register",
            json={
                "client_name": "Claude Code",
                "redirect_uris": ["http://127.0.0.1:51111/callback"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
        )
        client_id = registration.json()["client_id"]

        _, challenge = _pkce_pair()
        # Same client_id, a DIFFERENT loopback port.
        authorization = await client.get(
            "/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": "http://127.0.0.1:60222/callback",
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "s",
            },
            follow_redirects=False,
        )
    assert authorization.status_code in (302, 307), authorization.text
    assert CONSENT_PATH in authorization.headers["location"]


# ==================== DCR table cap (anti-flood, not a lifetime limit) ====================


async def test_client_cap_bounds_pending_not_lifetime(tmp_path):
    """The cap bounds PENDING (unconsummated) registrations only, so a run of legitimate
    consumed connections never wedges /register for new clients."""
    db = _db(tmp_path)
    async with _http_client(_os(_provider(db, max_clients=3), db=db)) as client:
        # Four full, consented connections -- each consumes its client row.
        for _ in range(4):
            tokens, _ = await _full_flow(client)
            assert tokens["access_token"]
        # A fresh registration still succeeds despite max_clients=3, because consumed
        # rows do not count against the pending cap.
        registration = await _register(client)
    assert registration.status_code == 201


# ==================== Refresh across access-token expiry (forced short TTL) ====================


async def test_refresh_survives_access_token_expiry_across_replicas(tmp_path):
    """plan Phase 2 proof: with a forced-short access TTL, a connector keeps working
    across expiry via refresh -- observed on a SECOND provider instance (shared DB +
    stateless verify), with the old refresh token rejected there."""
    import asyncio

    db = _db(tmp_path)
    async with _http_client(_os(_provider(db, access_token_ttl=1), db=db)) as client:
        tokens, client_id = await _full_flow(client)

    await asyncio.sleep(1.2)  # let the access token expire

    replica = _os(_provider(db, access_token_ttl=1), db=db)
    async with _http_client(replica) as client:
        expired = await client.post(
            "/mcp", json=_MCP_INIT_BODY, headers={**_MCP_HEADERS, "Authorization": f"Bearer {tokens['access_token']}"}
        )
        assert expired.status_code == 401  # stateless expiry check on the second replica

        refreshed = await client.post(
            "/token",
            data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id},
        )
        assert refreshed.status_code == 200, refreshed.text
        new_access = refreshed.json()["access_token"]

        # The old refresh token is rejected on the second replica (rotation is shared).
        replay = await client.post(
            "/token",
            data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id},
        )
        assert replay.status_code in (400, 401)

        # The freshly refreshed access token runs MCP requests.
        ok = await client.post(
            "/mcp", json=_MCP_INIT_BODY, headers={**_MCP_HEADERS, "Authorization": f"Bearer {new_access}"}
        )
        assert ok.status_code == 200


# ==================== Signing-key rotation overlap ====================


async def test_signing_key_rotation_overlap(tmp_path):
    """Adding a new signing key (env-primary) keeps tokens signed by the persisted key
    verifiable during the overlap, so rotation is graceful rather than a hard cut."""
    db = _db(tmp_path)
    # First instance: no env key, so it generates+persists a db key and signs with it.
    async with _http_client(_os(_provider(db), db=db)) as client:
        tokens, _ = await _full_flow(client)

    # Rotation: a new env-primary key is added. The persisted key is still active, so the
    # old token keeps verifying (overlap); new tokens are signed by the env key.
    rotated = _os(_provider(db, signing_key_material="freshly-rotated-in-key-material"), db=db)
    async with _http_client(rotated) as client:
        old_still_valid = await client.post(
            "/mcp", json=_MCP_INIT_BODY, headers={**_MCP_HEADERS, "Authorization": f"Bearer {tokens['access_token']}"}
        )
    assert old_still_valid.status_code == 200


# ==================== Reserved principal ====================


def test_oauth_principal_is_reserved():
    """A deployment JWT must not be able to claim an oauth: principal the bundled AS
    assigns to its connected clients."""
    from agno.os.middleware.jwt import is_reserved_principal

    assert is_reserved_principal("oauth:claude-ai") is True
    assert is_reserved_principal("sa:bot") is True
    assert is_reserved_principal("regular-user") is False
