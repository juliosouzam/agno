"""`agno connect` end-to-end flows against the fake AgentOS and tmp client configs."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import agno_cli.commands.connect as connect_module
from agno_cli.clients.claude_code import ClaudeCodeAdapter
from agno_cli.clients.codex import CodexAdapter
from agno_cli.clients.cursor import CursorAdapter
from agno_cli.main import app
from tests.conftest import FakeAgentOS, install_fake

runner = CliRunner()

URL_ARGS = ["--url", "http://localhost:7777"]
MCP_URL = "http://localhost:7777/mcp"


@pytest.fixture
def fake_clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """All three clients 'installed' under a tmp home, wired into the connect command."""
    (tmp_path / ".claude.json").write_text("{}")
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".cursor").mkdir()

    def build(home=None, cwd=None, project=False):
        return {
            "claude-code": ClaudeCodeAdapter(home=tmp_path, cwd=tmp_path, which=lambda name: None),
            "codex": CodexAdapter(home=tmp_path),
            "cursor": CursorAdapter(home=tmp_path, cwd=tmp_path, project=project),
        }

    monkeypatch.setattr(connect_module, "build_adapters", build)
    return tmp_path


def _connect(args=(), **kwargs):
    return runner.invoke(app, ["connect", "--json"] + URL_ARGS + list(args), **kwargs)


def test_connect_happy_path(monkeypatch, fake_os, fake_clients):
    monkeypatch.setenv("AGNO_ADMIN_TOKEN", fake_os.security_key)
    result = _connect()
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    assert {r["client"] for r in payload["results"]} == {"claude-code", "codex", "cursor"}
    assert all(r["status"] == "connected" for r in payload["results"])
    assert all(r["verify"]["ok"] for r in payload["results"])
    assert sorted(fake_os.accounts.keys()) == ["claude-code", "codex", "cursor"]

    # No plaintext token anywhere in the report.
    for account in fake_os.accounts.values():
        assert account["token"] not in result.output

    # Tokens landed in the client configs.
    claude_config = json.loads((fake_clients / ".mcp.json").read_text())
    assert claude_config["mcpServers"]["agno"]["url"] == MCP_URL
    cursor_config = json.loads((fake_clients / ".cursor" / "mcp.json").read_text())
    token = cursor_config["mcpServers"]["agno"]["headers"]["Authorization"]
    assert token == "Bearer " + fake_os.accounts["cursor"]["token"]


def test_connect_rerun_is_idempotent(monkeypatch, fake_os, fake_clients):
    monkeypatch.setenv("AGNO_ADMIN_TOKEN", fake_os.security_key)
    first = _connect()
    assert first.exit_code == 0, first.output
    creates_after_first = fake_os.create_calls

    second = _connect()
    assert second.exit_code == 0, second.output
    payload = json.loads(second.output)
    assert all(r["status"] == "already-connected" for r in payload["results"])
    assert fake_os.create_calls == creates_after_first


def test_connect_conflict_without_rotate_fails_noninteractive(monkeypatch, fake_os, fake_clients):
    monkeypatch.setenv("AGNO_ADMIN_TOKEN", fake_os.security_key)
    assert _connect().exit_code == 0

    # Wipe client configs but keep server-side accounts: mint now conflicts.
    (fake_clients / ".mcp.json").unlink()
    (fake_clients / ".codex" / "config.toml").unlink()
    (fake_clients / ".cursor" / "mcp.json").unlink()

    result = _connect()
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert all("already exists" in (r["error"] or "") for r in payload["results"])


def test_connect_rotate_replaces_accounts(monkeypatch, fake_os, fake_clients):
    monkeypatch.setenv("AGNO_ADMIN_TOKEN", fake_os.security_key)
    assert _connect().exit_code == 0
    old_token = fake_os.accounts["cursor"]["token"]

    result = _connect(["--rotate"])
    assert result.exit_code == 0, result.output
    new_token = fake_os.accounts["cursor"]["token"]
    assert new_token != old_token

    cursor_config = json.loads((fake_clients / ".cursor" / "mcp.json").read_text())
    assert cursor_config["mcpServers"]["agno"]["headers"]["Authorization"] == "Bearer " + new_token


def test_connect_rotates_stale_entry(monkeypatch, fake_os, fake_clients):
    """A config entry whose token was revoked server-side gets rotated on re-run."""
    monkeypatch.setenv("AGNO_ADMIN_TOKEN", fake_os.security_key)
    assert _connect().exit_code == 0
    for account in list(fake_os.accounts.values()):
        account["revoked_at"] = 1780000001

    result = _connect(["--rotate"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert all(r["status"] == "connected" for r in payload["results"])


def test_connect_no_auth_mode(monkeypatch, fake_clients):
    fake = FakeAgentOS(auth_mode="none")
    install_fake(monkeypatch, fake)
    result = _connect()
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert all(r["status"] == "connected" for r in payload["results"])
    assert fake.create_calls == 0
    cursor_config = json.loads((fake_clients / ".cursor" / "mcp.json").read_text())
    assert "headers" not in cursor_config["mcpServers"]["agno"]


def test_connect_mcp_disabled(monkeypatch, fake_clients):
    fake = FakeAgentOS(mcp_enabled=False)
    install_fake(monkeypatch, fake)
    result = _connect()
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "enable_mcp_server=True" in payload["error"]


def test_connect_warns_when_mcp_unauthenticated(monkeypatch, fake_clients):
    fake = FakeAgentOS(mcp_requires_token=False)
    install_fake(monkeypatch, fake)
    monkeypatch.setenv("AGNO_ADMIN_TOKEN", fake.security_key)
    result = _connect()
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["warning"] is not None
    assert "unauthenticated" in payload["warning"]


def test_connect_shared_account_with_name(monkeypatch, fake_os, fake_clients):
    monkeypatch.setenv("AGNO_ADMIN_TOKEN", fake_os.security_key)
    result = _connect(["--name", "my-machine"])
    assert result.exit_code == 0, result.output
    assert list(fake_os.accounts.keys()) == ["my-machine"]
    assert fake_os.create_calls == 1


def test_connect_explicit_client_selection(monkeypatch, fake_os, fake_clients):
    monkeypatch.setenv("AGNO_ADMIN_TOKEN", fake_os.security_key)
    result = _connect(["--clients", "cursor"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [r["client"] for r in payload["results"]] == ["cursor"]
    assert list(fake_os.accounts.keys()) == ["cursor"]


def test_connect_unknown_client(monkeypatch, fake_os, fake_clients):
    result = _connect(["--clients", "emacs"])
    assert result.exit_code == 1
    assert "Unknown client" in json.loads(result.output)["error"]


def test_connect_missing_admin_credential(monkeypatch, fake_os, fake_clients):
    result = _connect()
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "AGNO_ADMIN_TOKEN" in payload["hint"]


def test_connect_skip_existing_leaves_broken_entry(monkeypatch, fake_os, fake_clients):
    monkeypatch.setenv("AGNO_ADMIN_TOKEN", fake_os.security_key)
    assert _connect().exit_code == 0
    for account in list(fake_os.accounts.values()):
        account["revoked_at"] = 1780000001

    result = _connect(["--skip-existing"])
    payload = json.loads(result.output)
    assert all(r["status"] == "skipped" for r in payload["results"])
    assert result.exit_code == 1
