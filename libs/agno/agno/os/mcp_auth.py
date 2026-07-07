"""OAuth on the AgentOS MCP endpoint -- the ``mcp_auth`` seam.

``AgentOS(mcp_auth=<fastmcp AuthProvider>)`` hands authentication for the mounted MCP
app to the provider. fastmcp serves the provider's routes (RFC 9728 discovery and, for
an authorization-server provider, ``/authorize``, ``/token``, ``/register``, ``/revoke``)
inside the MCP sub-app -- which AgentOS mounts at root, so they resolve at the public
URLs -- and wraps the MCP path in the SDK's ``RequireAuthMiddleware``, which emits the
``401`` + ``WWW-Authenticate: Bearer resource_metadata="..."`` challenge OAuth clients
(claude.ai, ChatGPT) use for discovery.

agno adds two things on top of the provider:

- **Bearer coexistence**: the provider is composed via fastmcp's ``MultiAuth`` with the
  service-account verifier and, when the deployment has a JWT config, a JWT verifier --
  so existing ``agno_pat_`` bearers (Claude Code, Cursor, the ``agno connect``
  claude-desktop bridge) and agno-JWT bearers keep working on an OAuth-enabled ``/mcp``.
- **The identity bridge**: fastmcp attaches the verified token to the ASGI scope
  (``scope["user"]``), while the MCP tools read ``request.state``. The bridge
  middleware maps one onto the other with the full contract the tool gates need
  (``user_id``, ``scopes``, ``authorization_enabled``, ``admin_scope``). It must run
  INSIDE fastmcp's authentication middleware, so it is passed via
  ``mcp.http_app(middleware=[...])`` -- never ``add_middleware``, which prepends
  outside authentication where no verified token exists yet.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agno.os.middleware.jwt import is_reserved_principal as _is_reserved_principal
from agno.utils.log import log_warning

try:
    from fastmcp.server.auth import AccessToken, AuthProvider, MultiAuth, TokenVerifier
except ImportError as e:  # pragma: no cover - exercised only without the extra installed
    raise ImportError(
        "`fastmcp>=3.4.3` is required for `AgentOS(mcp_auth=...)`. "
        "Please install it using `pip install 'fastmcp>=3.4.3'`."
    ) from e

if TYPE_CHECKING:
    from agno.os.app import AgentOS

# Claim stamped on PAT-verified tokens so the identity bridge can restore the
# service-account fields (request.state.service_account_name) the tool gates read.
SERVICE_ACCOUNT_CLAIM = "agno_service_account"

# Claim stamped by the agno-JWT verifier so the bridge mirrors the parent middleware's
# behavior: JWT scopes are enforced only when RBAC is on (state.authorization_enabled =
# os.authorization), while PAT and OAuth-provider scopes are always enforced.
AUTHORIZATION_ENABLED_CLAIM = "agno_authorization_enabled"

# Marks a token minted by a trusted first-party agno source (the bundled AS) so the
# identity bridge permits it to carry a server-assigned reserved principal (``oauth:``).
# An external Tier-2 provider's token never carries it, so a reserved ``sub`` from such a
# token is rejected rather than honored (impersonation guard).
INTERNAL_ISSUER_CLAIM = "agno_mcp_internal_issuer"


class ServiceAccountTokenVerifier(TokenVerifier):
    """Verifies ``agno_pat_`` bearers against the AgentOS service-account store.

    The ``MultiAuth`` verifier that keeps PAT clients working when an OAuth provider
    owns the MCP endpoint. Reuses :class:`~agno.os.service_accounts.ServiceAccountVerifier`
    (hashed lookup, cache, failed-lookup throttle, expiry/revocation) and surfaces the
    account as a fastmcp ``AccessToken`` whose claims carry the ``sa:<name>`` principal
    for the identity bridge. Throttled/unavailable lookups verify as ``None`` (a 401):
    ``MultiAuth.verify_token`` has no channel for 429/503.
    """

    def __init__(self, verifier: Any):
        super().__init__()
        self._verifier = verifier

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        from agno.os.service_accounts import TOKEN_PREFIX

        if not token.startswith(TOKEN_PREFIX):
            return None
        result = await self._verifier.verify(token)
        account = getattr(result, "account", None)
        if not result.ok or account is None:
            return None
        return AccessToken(
            token=token,
            client_id=account.principal,
            scopes=list(account.scopes or []),
            # Expiry and revocation are enforced by the verifier on every lookup.
            expires_at=None,
            claims={"sub": account.principal, SERVICE_ACCOUNT_CLAIM: account.name},
        )


class JWTBearerTokenVerifier(TokenVerifier):
    """Verifies agno JWT bearers (the deployment's existing PEM / local-JWKS config).

    The second ``MultiAuth`` verifier: keeps existing agno-JWT clients working when an
    OAuth provider owns the MCP endpoint. Mirrors the parent ``AuthMiddleware``'s JWT
    handling: the same validator and audience constraints, the reserved-principal
    rejection, and the authorization flag (JWT scopes are enforced only when RBAC is
    on, unlike PAT scopes which are first-party ACL data).
    """

    def __init__(
        self,
        validator: Any,
        verify_audience: bool = False,
        audience: Any = None,
        authorization: bool = False,
    ) -> None:
        super().__init__()
        self._validator = validator
        self._verify_audience = verify_audience
        self._audience = audience
        self._authorization = authorization

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        from agno.os.middleware.jwt import is_reserved_principal

        try:
            expected_audience = (self._audience or None) if self._verify_audience else None
            payload = self._validator.validate_token(token, expected_audience)
        except Exception:
            return None
        claims = self._validator.extract_claims(payload)
        user_id = claims.get("user_id")
        if is_reserved_principal(user_id):
            log_warning(f"Rejected JWT claiming a reserved principal on the MCP endpoint: {user_id!r}")
            return None
        return AccessToken(
            token=token,
            client_id=str(user_id) if user_id is not None else "agno-jwt",
            scopes=list(claims.get("scopes") or []),
            expires_at=payload.get("exp"),
            claims={**payload, "sub": user_id, AUTHORIZATION_ENABLED_CLAIM: self._authorization},
        )


class MCPIdentityBridgeMiddleware:
    """Copies the fastmcp-verified identity onto ``request.state`` for the MCP tools.

    The tool gates are fail-open on a missing flag: ``_require_tool_scopes`` and the
    run-continuation gate skip enforcement unless ``request.state.authorization_enabled``
    (or a service-account identity) is present -- so the bridge sets the full contract,
    not just ``user_id``. Unauthenticated requests (the OAuth flow endpoints, the 401
    challenge on the MCP path) pass through untouched.
    """

    def __init__(self, app: Any, admin_scope: Optional[str] = None, user_isolation: bool = False) -> None:
        self.app = app
        self.admin_scope = admin_scope
        self.user_isolation = user_isolation

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            access_token = getattr(scope.get("user"), "access_token", None)
            if access_token is not None:
                claims = getattr(access_token, "claims", None) or {}
                user_id = claims.get("sub") or getattr(access_token, "client_id", None)
                service_account_name = claims.get(SERVICE_ACCOUNT_CLAIM)
                # A token must not claim a server-reserved principal (sa:/oauth:/scheduler)
                # unless it comes from a trusted first-party source that owns that
                # namespace: the PAT verifier (sa:) or the bundled AS (oauth:). An
                # external Tier-2 provider's token carrying such a sub is an impersonation
                # attempt -- leave the identity unset so the fail-closed tool gates deny.
                trusted_internal = service_account_name is not None or bool(claims.get(INTERNAL_ISSUER_CLAIM))
                if not trusted_internal and _is_reserved_principal(user_id):
                    log_warning(f"MCP token claims a reserved principal {user_id!r}; refusing to bridge its identity")
                    await self.app(scope, receive, send)
                    return
                # request.state is backed by scope["state"]; the mounted sub-app and the
                # parent share it, so the tools read these exactly as they do under the
                # parent AuthMiddleware.
                state = scope.setdefault("state", {})
                state["authenticated"] = True
                state["user_id"] = user_id
                state["session_id"] = claims.get("session_id")
                state["scopes"] = list(getattr(access_token, "scopes", None) or [])
                state["authorization_enabled"] = bool(claims.get(AUTHORIZATION_ENABLED_CLAIM, True))
                if self.admin_scope:
                    state["admin_scope"] = self.admin_scope
                state["user_isolation_enabled"] = self.user_isolation
                if service_account_name is not None:
                    state["service_account_name"] = service_account_name
                else:
                    # Parity with the parent JWT path, which exposes the full decoded
                    # claims for factory ctx.trusted.claims (the PAT path does not).
                    state["claims"] = claims
        await self.app(scope, receive, send)


def _build_jwt_token_verifier(os: "AgentOS") -> Optional[JWTBearerTokenVerifier]:
    """A verifier for the deployment's existing JWT config, or None when none is configured."""
    from os import getenv

    from agno.os.middleware.jwt import JWTValidator, build_jwt_middleware_kwargs

    kwargs = build_jwt_middleware_kwargs(
        getattr(os, "authorization_config", None),
        authorization=bool(getattr(os, "authorization", False)),
    )
    jwt_configured = bool(
        kwargs["verification_keys"] or kwargs["jwks_file"] or getenv("JWT_VERIFICATION_KEY") or getenv("JWT_JWKS_FILE")
    )
    if not jwt_configured:
        return None
    validator = JWTValidator(
        verification_keys=kwargs["verification_keys"],
        jwks_file=kwargs["jwks_file"],
        algorithm=kwargs["algorithm"],
    )
    return JWTBearerTokenVerifier(
        validator,
        verify_audience=bool(kwargs.get("verify_audience")),
        audience=kwargs.get("audience"),
        authorization=bool(getattr(os, "authorization", False)),
    )


def _resolve_bundled(os: "AgentOS") -> AuthProvider:
    """Resolve ``mcp_auth="bundled"``: the built-in AS on the deployment's Postgres db.

    Configuration comes from the environment (``AGENTOS_PUBLIC_URL``,
    ``MCP_CONNECT_SECRET``, optional ``AGENTOS_MCP_SIGNING_KEY``) so a template deploy
    enables it with env vars alone.
    """
    from agno.os.mcp_auth_bundled import AgentOSBundledAuth

    db = _first_postgres_db(os)
    if db is None:
        raise ValueError(
            'mcp_auth="bundled" requires a Postgres database on the AgentOS (the bundled authorization '
            "server stores clients, codes, and refresh-token state there). Pass db=PostgresDb(...), or "
            "construct AgentOSBundledAuth(db=...) explicitly -- it accepts SqliteDb for development."
        )
    return AgentOSBundledAuth.from_env(db=db, server_name=getattr(os, "name", None))


def _first_postgres_db(os: "AgentOS") -> Optional[Any]:
    try:
        from agno.db.postgres import PostgresDb
    except ImportError:
        return None
    candidates: List[Any] = []
    # os.db (the AgentOS-level db) is set at construction, before database
    # auto-discovery populates os.dbs -- so it is the reliable source at
    # mcp_auth resolution time. os.dbs is a fallback for agent-attached dbs and
    # maps id -> list[db], so its values are flattened.
    if getattr(os, "db", None) is not None:
        candidates.append(os.db)
    for value in (getattr(os, "dbs", None) or {}).values():
        if isinstance(value, (list, tuple, set)):
            candidates.extend(value)
        else:
            candidates.append(value)
    for db in candidates:
        if isinstance(db, PostgresDb):
            return db
    return None


def resolve_mcp_auth(os: "AgentOS") -> Optional[AuthProvider]:
    """Resolve ``AgentOS.mcp_auth`` into the provider handed to ``FastMCP(auth=...)``.

    Composes the provider (``MultiAuth``) with the service-account verifier whenever
    the OS has a db, and with a JWT verifier whenever the deployment has a JWT config,
    so enabling OAuth never breaks the deployment's existing PAT or JWT clients.
    Returns None when ``mcp_auth`` is unset.
    """
    raw = getattr(os, "mcp_auth", None)
    if raw is None:
        return None
    if isinstance(raw, str):
        if raw != "bundled":
            raise ValueError(
                f'Unknown mcp_auth value {raw!r}: use "bundled" for the built-in authorization server, '
                "or pass a fastmcp AuthProvider instance (e.g. AuthKitProvider)."
            )
        raw = _resolve_bundled(os)
    if not isinstance(raw, AuthProvider):
        raise TypeError(
            f"mcp_auth must be a fastmcp AuthProvider, got {type(raw).__name__!r}. "
            "See fastmcp.server.auth for the available providers."
        )
    verifiers: List[TokenVerifier] = []
    service_account_verifier = os._get_service_account_verifier()
    if service_account_verifier is not None:
        verifiers.append(ServiceAccountTokenVerifier(service_account_verifier))
    jwt_verifier = _build_jwt_token_verifier(os)
    if jwt_verifier is not None:
        verifiers.append(jwt_verifier)
    if not verifiers:
        return raw
    return MultiAuth(server=raw, verifiers=verifiers)


def mcp_auth_route_paths(provider: AuthProvider, mcp_path: str = "/mcp") -> List[str]:
    """The public paths the provider serves inside the MCP sub-app, plus the MCP path.

    Used to exempt exactly these paths from the parent ``AuthMiddleware``: browsers and
    connector backends hit them with no agno bearer, and the provider guards them itself
    (PKCE and client auth on the OAuth endpoints, the 401 challenge on the MCP path).
    Exact paths only -- a wildcard here would silently un-authenticate unrelated routes.
    """
    paths = [mcp_path]
    try:
        for route in provider.get_routes(mcp_path=mcp_path):
            path = getattr(route, "path", None)
            if isinstance(path, str) and path not in paths:
                paths.append(path)
    except Exception as e:
        log_warning(f"Could not enumerate mcp_auth provider routes for auth exemptions: {e}")
    return paths


def describe_mcp_auth(provider: AuthProvider, mcp_path: str = "/mcp") -> Dict[str, Any]:
    """Discovery details for ``/info``: the authorization server(s) and the resource URL.

    Best-effort convenience for clients like ``agno connect``; the authoritative
    discovery surface is the provider's own ``/.well-known`` routes.
    """
    server = getattr(provider, "server", None) or provider  # unwrap MultiAuth
    authorization_servers: Optional[List[str]] = None
    raw_servers = getattr(server, "authorization_servers", None)  # RemoteAuthProvider
    if raw_servers:
        authorization_servers = [str(s) for s in raw_servers]
    elif getattr(server, "base_url", None) is not None:  # OAuthProvider is its own AS
        authorization_servers = [str(server.base_url)]
    resource: Optional[str] = None
    try:
        resource_url = server._get_resource_url(mcp_path)
        resource = str(resource_url) if resource_url is not None else None
    except Exception as e:
        log_warning(f"Could not derive the MCP resource URL from the mcp_auth provider: {e}")
    return {"authorization_servers": authorization_servers, "resource": resource}
