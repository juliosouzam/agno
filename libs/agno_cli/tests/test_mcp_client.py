"""MCP verification client: JSON and SSE payloads, auth failures."""

from agno_cli.mcp_client import verify_mcp
from tests.conftest import FakeAgentOS, install_fake

MCP_URL = "http://localhost:7777/mcp"


def test_verify_ok_with_valid_token(monkeypatch, fake_os):
    result = verify_mcp(MCP_URL, token=fake_os.security_key)
    assert result.ok is True
    assert "run_agent" in result.tools


def test_verify_rejected_without_token(monkeypatch, fake_os):
    result = verify_mcp(MCP_URL, token=None)
    assert result.ok is False
    assert result.status_code == 401


def test_verify_rejected_with_bad_token(monkeypatch, fake_os):
    result = verify_mcp(MCP_URL, token="agno_pat_wrong")
    assert result.ok is False
    assert result.status_code == 401


def test_verify_parses_sse_responses(monkeypatch):
    fake = FakeAgentOS(sse_responses=True)
    install_fake(monkeypatch, fake)
    result = verify_mcp(MCP_URL, token=fake.security_key)
    assert result.ok is True
    assert result.tools == fake.mcp_tools


def test_verify_open_server_no_token(monkeypatch):
    fake = FakeAgentOS(auth_mode="none")
    install_fake(monkeypatch, fake)
    result = verify_mcp(MCP_URL, token=None)
    assert result.ok is True


def test_verify_404_when_mcp_disabled(monkeypatch):
    fake = FakeAgentOS(mcp_enabled=False)
    install_fake(monkeypatch, fake)
    result = verify_mcp(MCP_URL, token=fake.security_key)
    assert result.ok is False
    assert result.status_code == 404
