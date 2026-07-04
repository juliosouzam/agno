"""Discovery: structured /info fields, probe fallbacks, and failure modes."""

import httpx
import pytest

from agno_cli.discovery import discover
from agno_cli.errors import CLIError
from tests.conftest import FakeAgentOS, install_fake


def test_discover_via_info_fields(fake_os):
    info = discover("http://localhost:7777")
    assert info.discovered_via == "info"
    assert info.mcp_enabled is True
    assert info.mcp_url == "http://localhost:7777/mcp"
    assert info.auth_mode == "security_key"
    assert info.version == "2.7.0"


def test_discover_probe_fallback_security_key(monkeypatch):
    fake = FakeAgentOS(info_discovery=False)
    install_fake(monkeypatch, fake)
    info = discover("http://localhost:7777")
    assert info.discovered_via == "probe"
    assert info.mcp_enabled is True
    assert info.auth_mode == "security_key"


def test_discover_probe_fallback_jwt(monkeypatch):
    fake = FakeAgentOS(info_discovery=False, auth_mode="jwt")
    install_fake(monkeypatch, fake)
    info = discover("http://localhost:7777")
    assert info.auth_mode == "jwt"


def test_discover_probe_fallback_none_auth(monkeypatch):
    fake = FakeAgentOS(info_discovery=False, auth_mode="none")
    install_fake(monkeypatch, fake)
    info = discover("http://localhost:7777")
    assert info.auth_mode == "none"


def test_discover_probe_detects_mcp_disabled(monkeypatch):
    fake = FakeAgentOS(info_discovery=False, mcp_enabled=False)
    install_fake(monkeypatch, fake)
    info = discover("http://localhost:7777")
    assert info.mcp_enabled is False
    assert info.mcp_path is None


def test_discover_unreachable_raises(monkeypatch):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    import agno_cli.http as http_module

    monkeypatch.setattr(http_module, "_transport_override", httpx.MockTransport(refuse))
    monkeypatch.delenv("AGNO_OS_URL", raising=False)
    with pytest.raises(CLIError) as exc_info:
        discover(None)
    assert "No running AgentOS" in exc_info.value.message


def test_discover_env_var_url(monkeypatch, fake_os):
    monkeypatch.setenv("AGNO_OS_URL", "http://envhost:9000")
    info = discover(None)
    assert info.base_url == "http://envhost:9000"
