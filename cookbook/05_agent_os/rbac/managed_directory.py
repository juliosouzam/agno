"""
Managed Users & Roles - the default (full-directory) model

(New to this? Read managed_roles.py first, then managed_users.py.)

This is the plainest, most common setup: AgentOS holds the whole directory.

The token a user carries says only WHO they are - their user-id, in the `sub`
claim. Nothing else. No scopes, no role. On every request AgentOS takes that
`sub`, looks up the user's role in the store, expands the role into permissions
("scopes"), and enforces:

    JWT { sub: "u_1001" }
      -> store.roles_of("u_1001")  = ["editor"]                 (user  -> role)
      -> role "editor" scopes      = ["agents:*:read", ...]     (role  -> scopes)
      -> ALLOWED / BLOCKED

Two things this buys you:
  - Permissions live in AgentOS, not in the token. Change someone's role, or
    edit what a role can do, and their VERY NEXT request reflects it - same
    token, no re-login, no re-minting.
  - Disable a user and they are blocked on their next request even though their
    token is still valid and unexpired.

The wiring is the one-line default: `AuthorizationConfig(role_store=..., user_store=...)`.
No `authorization_provider`, no `roles_claim` - those are the other modes:
  - roles_claim  -> the role rides the token (keep your IdP). See self-hosted docs.
  - a provider   -> a custom decision engine. See custom_authorization_provider.py.

Note the user-ids below (u_1001, u_1002) are deliberately NOT role names, so it
is obvious the token carries an identity, not a role.

Run it:
    pip install "agno[roles]"
    python managed_directory.py
(no OpenAI key needed - we only check who is allowed, not actually chat)
"""

import os
from datetime import UTC, datetime, timedelta

import jwt
from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.openai import OpenAIResponses
from agno.os import AgentOS
from agno.os.authz.role_store import ManagedRoleStore
from agno.os.authz.user_store import ManagedUserStore
from agno.os.config import AuthorizationConfig

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

JWT_SECRET = os.getenv("JWT_VERIFICATION_KEY", "your-secret-key-at-least-256-bits-long")
OS_ID = "default-directory-os"

os.makedirs("tmp", exist_ok=True)

# ONE database for everything - agent data, roles, and the user directory. Pass
# the same `db` to AgentOS and to each store; they reuse its connection.
db = SqliteDb(db_file="tmp/managed_directory.db")

# The role store: define what each role can do, in agno scope terms. A DB is
# required (roles must persist and stay consistent across replicas). No
# roles_claim here, so roles come from this store's own assignments.
roles = ManagedRoleStore(db=db)
roles.set_role_scopes("viewer", ["agents:*:read"])
roles.set_role_scopes("editor", ["agents:*:read", "agents:research-agent:run"])
roles.set_role_scopes("admin", ["agent_os:admin"])

# The user directory: who exists, and (via the role store) which role each holds.
# The id you store IS the JWT `sub` your app mints for that person.
users = ManagedUserStore(db=db)
users.upsert("u_1001", name="Ada Lovelace")
users.upsert("u_1002", name="Grace Hopper")
roles.assign("u_1001", "editor")  # u_1001 can read + run the research agent
roles.assign("u_1002", "viewer")  # u_1002 can only read

research_agent = Agent(
    id="research-agent",
    name="Research Agent",
    model=OpenAIResponses(id="gpt-5.5"),
    db=db,
)

# The default wiring: hand AgentOS the role store and the user directory. That is
# the whole authorization setup - AgentOS uses the store's provider internally
# and denies disabled users at the enforcement point.
agent_os = AgentOS(
    id=OS_ID,
    description="Default full-directory AgentOS",
    agents=[research_agent],
    authorization=True,
    authorization_config=AuthorizationConfig(
        verification_keys=[JWT_SECRET],
        algorithm="HS256",
        verify_audience=True,
        audience=OS_ID,
        role_store=roles,
        user_store=users,
    ),
)

app = agent_os.get_app()


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """
    Each caller sends a token that carries ONLY their user-id (sub). The store
    decides what they can do. The scenario shows a runtime role change and a
    disable, both taking effect on the very next request with the same token.
    """

    import logging

    from fastapi.testclient import TestClient

    logging.disable(logging.CRITICAL)  # quiet framework logs for a clean transcript
    client = TestClient(app)

    def token(sub: str) -> str:
        # A sub-only token: no scopes, no role. Just identity.
        return jwt.encode(
            {"sub": sub, "aud": OS_ID, "exp": datetime.now(UTC) + timedelta(hours=24)},
            JWT_SECRET,
            algorithm="HS256",
        )

    def auth(sub: str) -> dict:
        return {"Authorization": f"Bearer {token(sub)}"}

    def show(label: str, r, note: str = "") -> None:
        # 200 means the request got in; 401/403 means it was bounced.
        verdict = "BLOCKED" if r.status_code in (401, 403) else "ALLOWED"
        print(f"  {label:52s} -> {verdict:7s} ({r.status_code})  {note}")

    print("\n" + "=" * 84)
    print("DEFAULT FULL-DIRECTORY MODE - the token carries only a user-id (sub)")
    print("=" * 84)
    print(
        "  roles:  viewer = read | editor = read + run research-agent | admin = anything"
    )
    print("  users:  u_1001 (Ada) is an editor | u_1002 (Grace) is a viewer")
    print(
        "  every token below carries ONLY sub. the store maps sub -> role -> scopes.\n"
    )

    print("  roles defined in the store (this is what each role can do):")
    for role in sorted(roles.list_roles()):
        scopes = roles.get_role_scopes(role)
        who = [u for u in ("u_1001", "u_1002") if role in roles.roles_of(u)]
        held_by = f"   held by: {', '.join(who)}" if who else ""
        print(f"    {role:8s} -> {scopes}{held_by}")
    print()

    print("  the tokens these users carry (decode them at jwt.io):")
    for sub in ("u_1001", "u_1002"):
        raw = token(sub)
        payload = jwt.decode(raw, JWT_SECRET, algorithms=["HS256"], audience=OS_ID)
        print(f"    {sub}: {raw}")
        print(f"           payload: {payload}  <- no scopes, no role")
    print()

    show(
        "u_1002 (viewer) LOOK at the agent",
        client.get("/agents/research-agent", headers=auth("u_1002")),
        "viewers can read",
    )
    show(
        "u_1002 (viewer) RUN the agent",
        client.post(
            "/agents/research-agent/runs",
            headers=auth("u_1002"),
            data={"message": "hi"},
        ),
        "viewers can't run, so bounced",
    )
    show(
        "u_1001 (editor) RUN the agent",
        client.post(
            "/agents/research-agent/runs",
            headers=auth("u_1001"),
            data={"message": "hi"},
        ),
        "editors can run",
    )

    print("\n  >> promote u_1002 to editor while the server is running...\n")
    roles.assign("u_1002", "editor")
    show(
        "u_1002 (editor) RUN the agent",
        client.post(
            "/agents/research-agent/runs",
            headers=auth("u_1002"),
            data={"message": "hi"},
        ),
        "same u_1002, same token, now allowed",
    )

    print("\n  >> disable u_1001 (a valid, unexpired token is now useless)...\n")
    users.set_disabled("u_1001", True)
    show(
        "u_1001 (disabled) LOOK at the agent",
        client.get("/agents/research-agent", headers=auth("u_1001")),
        "blocked on the next request, valid token or not",
    )

    print("=" * 84)
    print("the point: the token says WHO you are; AgentOS looks up WHAT you can do.")
    print(
        "change a role or disable a user and the next request reflects it - same token."
    )
    print("=" * 84)
