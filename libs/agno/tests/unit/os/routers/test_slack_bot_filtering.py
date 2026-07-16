from unittest.mock import AsyncMock, patch

import pytest

from agno.os.interfaces.slack.helpers import extract_event_context, resolve_slack_bot

from .conftest import (
    build_app,
    make_agent_mock,
    make_async_client_mock,
    make_signed_request,
    make_slack_mock,
    wait_for_call,
)

# -- extract_event_context sender identity --


def test_extract_event_context_both_user_and_bot_id_preserved():
    ctx = extract_event_context({"text": "hi", "channel": "C1", "user": "U123", "bot_id": "B456", "ts": "111"})
    assert ctx["user"] == "U123"
    assert ctx["bot_id"] == "B456"


def test_extract_event_context_bot_only_message():
    ctx = extract_event_context({"text": "hi", "channel": "C1", "bot_id": "B456", "ts": "111"})
    assert ctx["user"] == ""
    assert ctx["bot_id"] == "B456"


def test_extract_event_context_human_message():
    ctx = extract_event_context({"text": "hi", "channel": "C1", "user": "U123", "ts": "111"})
    assert ctx["user"] == "U123"
    assert ctx["bot_id"] == ""


def test_extract_event_context_empty_when_neither():
    ctx = extract_event_context({"text": "hi", "channel": "C1", "ts": "111"})
    assert ctx["user"] == ""
    assert ctx["bot_id"] == ""


# -- resolve_slack_bot --


@pytest.mark.asyncio
async def test_resolve_slack_bot_resolves_name():
    mock_client = AsyncMock()
    mock_client.bots_info = AsyncMock(return_value={"bot": {"name": "My Bot"}})

    resolved_id, display_name = await resolve_slack_bot(mock_client, "B123456")

    mock_client.bots_info.assert_awaited_once_with(bot="B123456")
    assert resolved_id == "B123456"
    assert display_name == "My Bot"


@pytest.mark.asyncio
async def test_resolve_slack_bot_fallback_on_error():
    mock_client = AsyncMock()
    mock_client.bots_info = AsyncMock(side_effect=Exception("API error"))

    resolved_id, display_name = await resolve_slack_bot(mock_client, "B123456")

    assert resolved_id == "B123456"
    assert display_name is None


# -- bot message filtering integration tests --


@pytest.mark.asyncio
async def test_default_drops_all_bot_events():
    agent_mock = make_agent_mock()
    mock_slack = make_slack_mock(token="xoxb-test")

    with (
        patch("agno.os.interfaces.slack.router.verify_slack_signature", return_value=True),
        patch("agno.os.interfaces.slack.router.SlackTools", return_value=mock_slack),
        patch("agno.os.interfaces.slack.event_handler.AsyncWebClient", return_value=make_async_client_mock()),
    ):
        app = build_app(agent_mock, reply_to_mentions_only=False)
        from fastapi.testclient import TestClient

        client = TestClient(app)
        body = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel_type": "channel",
                "text": "hello from a bot",
                "bot_id": "B_OTHER_BOT",
                "channel": "C123",
                "ts": "1708123456.000100",
            },
        }
        resp = make_signed_request(client, body)

    assert resp.status_code == 200
    agent_mock.arun.assert_not_called()


@pytest.mark.asyncio
async def test_opt_in_allows_peer_bot_messages():
    agent_mock = make_agent_mock()
    mock_slack = make_slack_mock(token="xoxb-test")
    mock_slack.client.auth_test.return_value = {"bot_id": "B_SELF", "user_id": "U_SELF_BOT"}

    with (
        patch("agno.os.interfaces.slack.router.verify_slack_signature", return_value=True),
        patch("agno.os.interfaces.slack.router.SlackTools", return_value=mock_slack),
        patch("agno.os.interfaces.slack.event_handler.AsyncWebClient", return_value=make_async_client_mock()),
    ):
        app = build_app(agent_mock, reply_to_mentions_only=False, respond_to_bot_messages=True)
        from fastapi.testclient import TestClient

        client = TestClient(app)
        body = {
            "type": "event_callback",
            "authorizations": [{"user_id": "U_SELF_BOT"}],
            "event": {
                "type": "message",
                "subtype": "bot_message",
                "channel_type": "channel",
                "text": "hello from peer bot",
                "bot_id": "B_OTHER_BOT",
                "channel": "C123",
                "ts": "1708123456.000100",
            },
        }
        resp = make_signed_request(client, body)

    assert resp.status_code == 200
    await wait_for_call(agent_mock.arun)
    agent_mock.arun.assert_called_once()


@pytest.mark.asyncio
async def test_opt_in_drops_own_messages_by_bot_id():
    agent_mock = make_agent_mock()
    mock_slack = make_slack_mock(token="xoxb-test")
    mock_slack.client.auth_test.return_value = {"bot_id": "B_SELF"}

    with (
        patch("agno.os.interfaces.slack.router.verify_slack_signature", return_value=True),
        patch("agno.os.interfaces.slack.router.SlackTools", return_value=mock_slack),
        patch("agno.os.interfaces.slack.event_handler.AsyncWebClient", return_value=make_async_client_mock()),
    ):
        app = build_app(agent_mock, reply_to_mentions_only=False, respond_to_bot_messages=True)
        from fastapi.testclient import TestClient

        client = TestClient(app)
        body = {
            "type": "event_callback",
            "api_app_id": "A_SELF",
            "authorizations": [{"user_id": "U_SELF_BOT"}],
            "event": {
                "type": "message",
                "subtype": "bot_message",
                "channel_type": "channel",
                "text": "my own message",
                "bot_id": "B_SELF",
                "channel": "C123",
                "ts": "1708123456.000100",
            },
        }
        resp = make_signed_request(client, body)

    assert resp.status_code == 200
    agent_mock.arun.assert_not_called()


@pytest.mark.asyncio
async def test_opt_in_drops_own_messages_by_bot_user_id():
    agent_mock = make_agent_mock()
    mock_slack = make_slack_mock(token="xoxb-test")
    mock_slack.client.auth_test.return_value = {"user_id": "U_SELF_BOT"}

    with (
        patch("agno.os.interfaces.slack.router.verify_slack_signature", return_value=True),
        patch("agno.os.interfaces.slack.router.SlackTools", return_value=mock_slack),
        patch("agno.os.interfaces.slack.event_handler.AsyncWebClient", return_value=make_async_client_mock()),
    ):
        app = build_app(agent_mock, reply_to_mentions_only=False, respond_to_bot_messages=True)
        from fastapi.testclient import TestClient

        client = TestClient(app)
        body = {
            "type": "event_callback",
            "api_app_id": "A_SELF",
            "event": {
                "type": "message",
                "channel_type": "channel",
                "text": "my own message",
                "user": "U_SELF_BOT",
                "channel": "C123",
                "ts": "1708123456.000100",
            },
        }
        resp = make_signed_request(client, body)

    assert resp.status_code == 200
    agent_mock.arun.assert_not_called()


@pytest.mark.asyncio
async def test_opt_in_allows_peer_webhook_bot_with_only_bot_id():
    agent_mock = make_agent_mock()
    mock_slack = make_slack_mock(token="xoxb-test")
    mock_slack.client.auth_test.return_value = {"bot_id": "B_SELF"}

    with (
        patch("agno.os.interfaces.slack.router.verify_slack_signature", return_value=True),
        patch("agno.os.interfaces.slack.router.SlackTools", return_value=mock_slack),
        patch("agno.os.interfaces.slack.event_handler.AsyncWebClient", return_value=make_async_client_mock()),
    ):
        app = build_app(agent_mock, reply_to_mentions_only=False, respond_to_bot_messages=True)
        from fastapi.testclient import TestClient

        client = TestClient(app)
        body = {
            "type": "event_callback",
            "api_app_id": "A_SELF",
            "authorizations": [{"user_id": "U_SELF_BOT"}],
            "event": {
                "type": "message",
                "subtype": "bot_message",
                "channel_type": "channel",
                "text": "webhook message",
                "bot_id": "B_WEBHOOK",
                "channel": "C123",
                "ts": "1708123456.000100",
            },
        }
        resp = make_signed_request(client, body)

    assert resp.status_code == 200
    await wait_for_call(agent_mock.arun)
    agent_mock.arun.assert_called_once()


@pytest.mark.asyncio
async def test_opt_in_allows_peer_by_user_id_mismatch():
    agent_mock = make_agent_mock()
    mock_slack = make_slack_mock(token="xoxb-test")
    mock_slack.client.auth_test.return_value = {"bot_id": "B_SELF", "user_id": "U_SELF_BOT"}

    with (
        patch("agno.os.interfaces.slack.router.verify_slack_signature", return_value=True),
        patch("agno.os.interfaces.slack.router.SlackTools", return_value=mock_slack),
        patch("agno.os.interfaces.slack.event_handler.AsyncWebClient", return_value=make_async_client_mock()),
    ):
        app = build_app(agent_mock, reply_to_mentions_only=False, respond_to_bot_messages=True)
        from fastapi.testclient import TestClient

        client = TestClient(app)
        body = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel_type": "channel",
                "text": "message from other user",
                "user": "U_OTHER",
                "channel": "C123",
                "ts": "1708123456.000100",
            },
        }
        resp = make_signed_request(client, body)

    assert resp.status_code == 200
    await wait_for_call(agent_mock.arun)
    agent_mock.arun.assert_called_once()


@pytest.mark.asyncio
async def test_lifecycle_subtypes_still_dropped_with_opt_in():
    agent_mock = make_agent_mock()
    mock_slack = make_slack_mock(token="xoxb-test")

    with (
        patch("agno.os.interfaces.slack.router.verify_slack_signature", return_value=True),
        patch("agno.os.interfaces.slack.router.SlackTools", return_value=mock_slack),
        patch("agno.os.interfaces.slack.event_handler.AsyncWebClient", return_value=make_async_client_mock()),
    ):
        app = build_app(agent_mock, reply_to_mentions_only=False, respond_to_bot_messages=True)
        from fastapi.testclient import TestClient

        client = TestClient(app)
        body = {
            "type": "event_callback",
            "api_app_id": "A_SELF",
            "authorizations": [{"user_id": "U_SELF_BOT"}],
            "event": {
                "type": "message",
                "subtype": "message_changed",
                "channel_type": "channel",
                "channel": "C123",
                "ts": "1708123456.000100",
                "message": {"text": "edited", "user": "U_OTHER"},
            },
        }
        resp = make_signed_request(client, body)

    assert resp.status_code == 200
    agent_mock.arun.assert_not_called()
