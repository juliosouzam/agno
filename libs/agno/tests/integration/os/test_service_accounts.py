"""Integration tests for service account (agno_pat_) authentication in AgentOS."""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from agno.agent.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.os import AgentOS
from agno.os.middleware import JWTMiddleware
from agno.os.settings import AgnoAPISettings

JWT_SECRET = "test-secret-key-for-service-account-tests"

UNIFORM_401_DETAIL = "Invalid or expired service account token"


def _make_jwt(scopes, sub="human-admin"):
    payload = {
        "sub": sub,
        "scopes": scopes,
        "exp": datetime.now(UTC) + timedelta(hours=1),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _mock_run_output():
    return type(
        "MockRunOutput",
        (),
        {"to_dict": lambda self: {"content": "ok", "run_id": "test_run_1"}},
    )()


@pytest.fixture
def sqlite_db(tmp_path):
    return SqliteDb(db_file=str(tmp_path / "service_accounts_test.db"))


@pytest.fixture
def test_agent(sqlite_db):
    agent = Agent(id="sa-test-agent", name="sa-test-agent", db=sqlite_db)
    agent.deep_copy = lambda **kwargs: agent
    return agent


@pytest.fixture
def jwt_client(test_agent, sqlite_db):
    """AgentOS with a db and JWT middleware with authorization enabled."""
    agent_os = AgentOS(agents=[test_agent], db=sqlite_db)
    app = agent_os.get_app()
    app.add_middleware(
        JWTMiddleware,
        verification_keys=[JWT_SECRET],
        algorithm="HS256",
        authorization=True,
    )
    return TestClient(app)


def _mint(client, auth_token, name="claude-code", **body_overrides):
    body = {"name": name, **body_overrides}
    return client.post(
        "/service-accounts",
        headers={"Authorization": f"Bearer {auth_token}"},
        json=body,
    )


class TestServiceAccountLifecycleWithJWT:
    def test_admin_mints_pat_and_pat_runs_agent_with_attribution(self, jwt_client, test_agent):
        admin_jwt = _make_jwt(["agent_os:admin"])
        response = _mint(jwt_client, admin_jwt)
        assert response.status_code == 201, response.text
        body = response.json()
        pat = body["token"]
        assert pat.startswith("agno_pat_")
        assert body["principal"] == "sa:claude-code"
        assert body["created_by"] == "human-admin"

        with patch.object(test_agent, "arun", new_callable=AsyncMock) as mock_arun:
            mock_arun.return_value = _mock_run_output()
            run_response = jwt_client.post(
                "/agents/sa-test-agent/runs",
                headers={"Authorization": f"Bearer {pat}"},
                data={"message": "hello", "stream": "false"},
            )
        assert run_response.status_code == 200, run_response.text
        assert mock_arun.call_args.kwargs["user_id"] == "sa:claude-code"

    def test_pat_default_scopes_allow_session_read_but_not_delete(self, jwt_client):
        admin_jwt = _make_jwt(["agent_os:admin"])
        pat = _mint(jwt_client, admin_jwt).json()["token"]

        response = jwt_client.get("/sessions", headers={"Authorization": f"Bearer {pat}"})
        assert response.status_code == 200

        response = jwt_client.delete("/sessions", headers={"Authorization": f"Bearer {pat}"})
        assert response.status_code == 403

    def test_pat_cannot_mint_pats_by_default(self, jwt_client):
        admin_jwt = _make_jwt(["agent_os:admin"])
        pat = _mint(jwt_client, admin_jwt).json()["token"]
        response = _mint(jwt_client, pat, name="sneaky")
        assert response.status_code == 403

    def test_minter_cannot_escalate_beyond_own_scopes(self, jwt_client):
        minter_jwt = _make_jwt(["service_accounts:write", "agents:run"], sub="delegated-minter")
        # Granting a scope the minter holds works
        response = _mint(jwt_client, minter_jwt, name="ci-bot", scopes=["agents:run"])
        assert response.status_code == 201

        # Granting a scope the minter does not hold is rejected
        response = _mint(jwt_client, minter_jwt, name="ci-bot-2", scopes=["teams:run"])
        assert response.status_code == 403

        # The privileged flag alone cannot escalate either
        response = _mint(
            jwt_client, minter_jwt, name="ci-bot-3", scopes=["agent_os:admin"], allow_privileged_scopes=True
        )
        assert response.status_code == 403

    def test_revoked_pat_gets_uniform_401(self, jwt_client):
        admin_jwt = _make_jwt(["agent_os:admin"])
        minted = _mint(jwt_client, admin_jwt).json()

        revoke = jwt_client.delete(
            f"/service-accounts/{minted['id']}", headers={"Authorization": f"Bearer {admin_jwt}"}
        )
        assert revoke.status_code == 204

        response = jwt_client.get("/sessions", headers={"Authorization": f"Bearer {minted['token']}"})
        assert response.status_code == 401
        assert response.json()["detail"] == UNIFORM_401_DETAIL

    def test_revoke_invalidates_cached_token_immediately(self, jwt_client):
        # Use the token first so it is cached, then revoke: the revoking worker (this
        # process, default 30s cache TTL) must reject it at once, not serve the cache.
        admin_jwt = _make_jwt(["agent_os:admin"])
        minted = _mint(jwt_client, admin_jwt).json()

        assert jwt_client.get("/sessions", headers={"Authorization": f"Bearer {minted['token']}"}).status_code == 200

        revoke = jwt_client.delete(
            f"/service-accounts/{minted['id']}", headers={"Authorization": f"Bearer {admin_jwt}"}
        )
        assert revoke.status_code == 204

        response = jwt_client.get("/sessions", headers={"Authorization": f"Bearer {minted['token']}"})
        assert response.status_code == 401
        assert response.json()["detail"] == UNIFORM_401_DETAIL

    def test_expired_pat_gets_same_uniform_401(self, jwt_client, sqlite_db):
        admin_jwt = _make_jwt(["agent_os:admin"])
        minted = _mint(jwt_client, admin_jwt).json()
        sqlite_db.update_service_account(minted["id"], expires_at=int(time.time()) - 10)

        response = jwt_client.get("/sessions", headers={"Authorization": f"Bearer {minted['token']}"})
        assert response.status_code == 401
        assert response.json()["detail"] == UNIFORM_401_DETAIL

    def test_unknown_pat_gets_same_uniform_401(self, jwt_client):
        response = jwt_client.get(
            "/sessions", headers={"Authorization": "Bearer agno_pat_doesnotexist0000000000000000"}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == UNIFORM_401_DETAIL

    def test_repeated_failed_lookups_get_throttled(self, jwt_client):
        last_status = None
        for _ in range(25):
            response = jwt_client.get(
                "/sessions", headers={"Authorization": "Bearer agno_pat_bruteforce000000000000000"}
            )
            last_status = response.status_code
        assert last_status == 429

    def test_name_reuse_after_revocation_rotates_identity(self, jwt_client):
        admin_jwt = _make_jwt(["agent_os:admin"])
        first = _mint(jwt_client, admin_jwt)
        assert first.status_code == 201

        # Duplicate active name is rejected
        duplicate = _mint(jwt_client, admin_jwt)
        assert duplicate.status_code == 409

        # After revocation the name can be reused (rotation)
        revoke = jwt_client.delete(
            f"/service-accounts/{first.json()['id']}", headers={"Authorization": f"Bearer {admin_jwt}"}
        )
        assert revoke.status_code == 204
        rotated = _mint(jwt_client, admin_jwt)
        assert rotated.status_code == 201
        assert rotated.json()["principal"] == first.json()["principal"]

    def test_list_never_exposes_hashes_or_tokens(self, jwt_client):
        admin_jwt = _make_jwt(["agent_os:admin"])
        pat = _mint(jwt_client, admin_jwt).json()["token"]

        response = jwt_client.get("/service-accounts", headers={"Authorization": f"Bearer {admin_jwt}"})
        assert response.status_code == 200
        assert pat not in response.text
        entry = response.json()["data"][0]
        assert "token" not in entry
        assert "token_hash" not in entry
        assert entry["token_prefix"] == pat[:16]

    def test_jwt_without_scope_cannot_manage_service_accounts(self, jwt_client):
        plain_jwt = _make_jwt(["agents:run"], sub="regular-user")
        assert _mint(jwt_client, plain_jwt).status_code == 403
        response = jwt_client.get("/service-accounts", headers={"Authorization": f"Bearer {plain_jwt}"})
        assert response.status_code == 403


class TestInternalTokenRegression:
    """The internal scheduler token must behave identically after the RBAC refactor."""

    @pytest.fixture
    def internal_client(self, test_agent, sqlite_db):
        agent_os = AgentOS(agents=[test_agent], db=sqlite_db, internal_service_token="internal-test-token")
        app = agent_os.get_app()
        app.add_middleware(
            JWTMiddleware,
            verification_keys=[JWT_SECRET],
            algorithm="HS256",
            authorization=True,
        )
        return TestClient(app)

    def test_internal_token_allowed_for_granted_scopes(self, internal_client):
        response = internal_client.get("/agents", headers={"Authorization": "Bearer internal-test-token"})
        assert response.status_code == 200

    def test_internal_token_denied_outside_granted_scopes(self, internal_client):
        response = internal_client.get("/sessions", headers={"Authorization": "Bearer internal-test-token"})
        assert response.status_code == 403


class TestServiceAccountsInSecurityKeyMode:
    """PATs work without JWT middleware, and their scopes are still enforced."""

    @pytest.fixture
    def security_key_client(self, test_agent, sqlite_db):
        agent_os = AgentOS(
            agents=[test_agent],
            db=sqlite_db,
            settings=AgnoAPISettings(os_security_key="root-security-key"),
        )
        return TestClient(agent_os.get_app())

    def test_security_key_mints_and_pat_runs_with_attribution(self, security_key_client, test_agent):
        response = _mint(security_key_client, "root-security-key")
        assert response.status_code == 201, response.text
        pat = response.json()["token"]

        with patch.object(test_agent, "arun", new_callable=AsyncMock) as mock_arun:
            mock_arun.return_value = _mock_run_output()
            run_response = security_key_client.post(
                "/agents/sa-test-agent/runs",
                headers={"Authorization": f"Bearer {pat}"},
                data={"message": "hello", "stream": "false"},
            )
        assert run_response.status_code == 200, run_response.text
        assert mock_arun.call_args.kwargs["user_id"] == "sa:claude-code"

    def test_pat_scopes_are_enforced_without_jwt_middleware(self, security_key_client):
        pat = _mint(security_key_client, "root-security-key").json()["token"]

        # Default scopes include sessions:read
        response = security_key_client.get("/sessions", headers={"Authorization": f"Bearer {pat}"})
        assert response.status_code == 200

        # But a run-and-read PAT is not a master key: it cannot mint more PATs
        response = _mint(security_key_client, pat, name="sneaky")
        assert response.status_code == 403

        # And it cannot delete sessions
        response = security_key_client.delete("/sessions", headers={"Authorization": f"Bearer {pat}"})
        assert response.status_code == 403

    def test_revoked_pat_rejected_in_security_key_mode(self, security_key_client):
        minted = _mint(security_key_client, "root-security-key").json()
        revoke = security_key_client.delete(
            f"/service-accounts/{minted['id']}",
            headers={"Authorization": "Bearer root-security-key"},
        )
        assert revoke.status_code == 204

        response = security_key_client.get("/sessions", headers={"Authorization": f"Bearer {minted['token']}"})
        assert response.status_code == 401
        assert response.json()["detail"] == UNIFORM_401_DETAIL


class TestServiceAccountsOnOpenInstance:
    """On an open dev instance PATs still verify and provide attribution."""

    @pytest.fixture
    def open_client(self, test_agent, sqlite_db):
        agent_os = AgentOS(agents=[test_agent], db=sqlite_db)
        return TestClient(agent_os.get_app())

    def test_pat_authenticates_and_attributes_on_open_instance(self, open_client, test_agent):
        pat = _mint(open_client, "anything-goes").json()["token"]

        with patch.object(test_agent, "arun", new_callable=AsyncMock) as mock_arun:
            mock_arun.return_value = _mock_run_output()
            run_response = open_client.post(
                "/agents/sa-test-agent/runs",
                headers={"Authorization": f"Bearer {pat}"},
                data={"message": "hello", "stream": "false"},
            )
        assert run_response.status_code == 200
        assert mock_arun.call_args.kwargs["user_id"] == "sa:claude-code"

    def test_invalid_pat_rejected_even_on_open_instance(self, open_client):
        response = open_client.get("/sessions", headers={"Authorization": "Bearer agno_pat_invalid000000000000000000"})
        assert response.status_code == 401
