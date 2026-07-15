"""Unit tests for TelegramTools class."""

import json
from unittest.mock import MagicMock, patch

import pytest


class _FakeApiTelegramException(Exception):
    def __init__(self, function_name, result, result_code):
        self.function_name = function_name
        self.result = result
        self.result_code = result_code
        super().__init__(
            f"A request to the Telegram API was unsuccessful. Error code: {result_code}. Description: {result}"
        )


@pytest.fixture(autouse=True)
def _mock_telebot():
    with (
        patch("agno.tools.telegram.TeleBot") as mock_telebot,
        patch("agno.tools.telegram.ApiTelegramException", _FakeApiTelegramException),
    ):
        mock_telebot.return_value = MagicMock()
        yield {"TeleBot": mock_telebot}


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "fake-token")
    from agno.tools.telegram import TelegramTools

    return TelegramTools(chat_id="12345", all=True)


# ============================================================================
# INITIALIZATION TESTS
# ============================================================================


def test_init_default_registers_send_message_only(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "fake-token")
    from agno.tools.telegram import TelegramTools

    tools = TelegramTools(chat_id="12345")
    assert "send_message" in tools.functions
    assert "send_photo" not in tools.functions


def test_init_token_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "env-token")
    from agno.tools.telegram import TelegramTools

    tools = TelegramTools(chat_id="12345")
    assert tools.token == "env-token"


def test_init_token_from_param(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    from agno.tools.telegram import TelegramTools

    tools = TelegramTools(chat_id="12345", token="param-token")
    assert tools.token == "param-token"


def test_init_param_token_overrides_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "env-token")
    from agno.tools.telegram import TelegramTools

    tools = TelegramTools(chat_id="12345", token="param-token")
    assert tools.token == "param-token"


def test_init_missing_token_raises(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    from agno.tools.telegram import TelegramTools

    with pytest.raises(ValueError, match="TELEGRAM_TOKEN"):
        TelegramTools(chat_id="12345")


def test_init_chat_id_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "99999")
    from agno.tools.telegram import TelegramTools

    tools = TelegramTools()
    assert tools.chat_id == "99999"


def test_init_chat_id_from_param(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "fake-token")
    from agno.tools.telegram import TelegramTools

    tools = TelegramTools(chat_id="12345")
    assert tools.chat_id == "12345"


def test_init_media_tools_disabled_by_default(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "fake-token")
    from agno.tools.telegram import TelegramTools

    tools = TelegramTools(chat_id="12345")
    for name in ("send_photo", "send_document", "send_video", "send_audio", "send_animation", "send_sticker"):
        assert name not in tools.functions


def test_init_media_tools_enabled_explicitly(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "fake-token")
    from agno.tools.telegram import TelegramTools

    tools = TelegramTools(
        chat_id="12345",
        enable_send_photo=True,
        enable_send_video=True,
        enable_send_audio=True,
        enable_send_document=True,
        enable_send_animation=True,
        enable_send_sticker=True,
    )
    for name in ("send_photo", "send_document", "send_video", "send_audio", "send_animation", "send_sticker"):
        assert name in tools.functions


def test_init_all_flag_enables_everything(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "fake-token")
    from agno.tools.telegram import TelegramTools

    tools = TelegramTools(chat_id="12345", all=True)
    expected = (
        "send_message",
        "send_photo",
        "send_document",
        "send_video",
        "send_audio",
        "send_animation",
        "send_sticker",
        "edit_message",
        "delete_message",
        "pin_message",
        "get_chat",
        "get_file",
        "set_reaction",
    )
    for name in expected:
        assert name in tools.functions


def test_init_no_tools_when_all_disabled(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "fake-token")
    from agno.tools.telegram import TelegramTools

    tools = TelegramTools(chat_id="12345", enable_send_message=False)
    assert len(tools.functions) == 0


# ============================================================================
# CHAT ID PROPERTY TESTS
# ============================================================================


def test_chat_id_property_returns_value(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "fake-token")
    from agno.tools.telegram import TelegramTools

    tools = TelegramTools(chat_id="11111")
    assert tools._chat_id == "11111"


def test_chat_id_property_raises_when_missing(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "fake-token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    from agno.tools.telegram import TelegramTools

    tools = TelegramTools()
    with pytest.raises(ValueError, match="chat_id is required"):
        _ = tools._chat_id


# ============================================================================
# SEND MESSAGE TESTS
# ============================================================================


def test_send_message_success(tools):
    mock_result = MagicMock()
    mock_result.message_id = 101
    tools.bot.send_message = MagicMock(return_value=mock_result)

    result = tools.send_message("Hello")
    tools.bot.send_message.assert_called_once_with("12345", "Hello")
    parsed = json.loads(result)
    assert parsed["status"] == "success"
    assert parsed["message_id"] == 101


def test_send_message_api_error(tools):
    tools.bot.send_message = MagicMock(side_effect=_FakeApiTelegramException("sendMessage", "Bad Request", 400))

    result = tools.send_message("Hello")
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "Bad Request" in parsed["message"]


# ============================================================================
# SEND PHOTO TESTS
# ============================================================================


def test_send_photo_success(tools):
    mock_result = MagicMock()
    mock_result.message_id = 103
    tools.bot.send_photo = MagicMock(return_value=mock_result)

    result = tools.send_photo(b"image-bytes", caption="A photo")
    tools.bot.send_photo.assert_called_once_with("12345", b"image-bytes", caption="A photo")
    parsed = json.loads(result)
    assert parsed["status"] == "success"
    assert parsed["message_id"] == 103


# ============================================================================
# SEND DOCUMENT TESTS
# ============================================================================


def test_send_document_success(tools):
    mock_result = MagicMock()
    mock_result.message_id = 105
    tools.bot.send_document = MagicMock(return_value=mock_result)

    result = tools.send_document(b"doc-bytes", "report.pdf")
    tools.bot.send_document.assert_called_once_with("12345", ("report.pdf", b"doc-bytes"), caption=None)
    parsed = json.loads(result)
    assert parsed["status"] == "success"


# ============================================================================
# SEND VIDEO TESTS
# ============================================================================


def test_send_video_success(tools):
    mock_result = MagicMock()
    mock_result.message_id = 107
    tools.bot.send_video = MagicMock(return_value=mock_result)

    result = tools.send_video(b"video-bytes", caption="A video")
    tools.bot.send_video.assert_called_once_with("12345", b"video-bytes", caption="A video")
    parsed = json.loads(result)
    assert parsed["status"] == "success"


# ============================================================================
# SEND AUDIO TESTS
# ============================================================================


def test_send_audio_success(tools):
    mock_result = MagicMock()
    mock_result.message_id = 109
    tools.bot.send_audio = MagicMock(return_value=mock_result)

    result = tools.send_audio(b"audio-bytes", caption="A song", title="Song Title")
    tools.bot.send_audio.assert_called_once_with("12345", b"audio-bytes", caption="A song", title="Song Title")
    parsed = json.loads(result)
    assert parsed["status"] == "success"


# ============================================================================
# SEND ANIMATION TESTS
# ============================================================================


def test_send_animation_success(tools):
    mock_result = MagicMock()
    mock_result.message_id = 111
    tools.bot.send_animation = MagicMock(return_value=mock_result)

    result = tools.send_animation(b"gif-bytes", caption="A GIF")
    tools.bot.send_animation.assert_called_once_with("12345", b"gif-bytes", caption="A GIF")
    parsed = json.loads(result)
    assert parsed["status"] == "success"


# ============================================================================
# SEND STICKER TESTS
# ============================================================================


def test_send_sticker_success(tools):
    mock_result = MagicMock()
    mock_result.message_id = 113
    tools.bot.send_sticker = MagicMock(return_value=mock_result)

    result = tools.send_sticker(b"sticker-bytes")
    tools.bot.send_sticker.assert_called_once_with("12345", b"sticker-bytes")
    parsed = json.loads(result)
    assert parsed["status"] == "success"


# ============================================================================
# EDIT MESSAGE TESTS
# ============================================================================


def test_edit_message_success(tools):
    mock_result = MagicMock()
    mock_result.message_id = 42
    tools.bot.edit_message_text = MagicMock(return_value=mock_result)

    result = tools.edit_message("Updated text", message_id=42)
    tools.bot.edit_message_text.assert_called_once_with("Updated text", chat_id="12345", message_id=42)
    parsed = json.loads(result)
    assert parsed["status"] == "success"
    assert parsed["message_id"] == 42


def test_edit_message_api_error(tools):
    tools.bot.edit_message_text = MagicMock(
        side_effect=_FakeApiTelegramException("editMessageText", "Bad Request", 400)
    )

    result = tools.edit_message("Updated text", message_id=42)
    parsed = json.loads(result)
    assert parsed["status"] == "error"


# ============================================================================
# DELETE MESSAGE TESTS
# ============================================================================


def test_delete_message_success(tools):
    tools.bot.delete_message = MagicMock(return_value=True)

    result = tools.delete_message(message_id=42)
    tools.bot.delete_message.assert_called_once_with("12345", 42)
    parsed = json.loads(result)
    assert parsed["status"] == "success"
    assert parsed["deleted"] is True


def test_delete_message_api_error(tools):
    tools.bot.delete_message = MagicMock(side_effect=_FakeApiTelegramException("deleteMessage", "Bad Request", 400))

    result = tools.delete_message(message_id=42)
    parsed = json.loads(result)
    assert parsed["status"] == "error"


# ============================================================================
# PIN MESSAGE TESTS
# ============================================================================


def test_pin_message_success(tools):
    tools.bot.pin_chat_message = MagicMock(return_value=True)

    result = tools.pin_message(message_id=42)
    tools.bot.pin_chat_message.assert_called_once_with("12345", 42, disable_notification=False)
    parsed = json.loads(result)
    assert parsed["status"] == "success"
    assert parsed["pinned"] is True


def test_pin_message_silent(tools):
    tools.bot.pin_chat_message = MagicMock(return_value=True)

    result = tools.pin_message(message_id=42, disable_notification=True)
    tools.bot.pin_chat_message.assert_called_once_with("12345", 42, disable_notification=True)
    parsed = json.loads(result)
    assert parsed["status"] == "success"


def test_pin_message_api_error(tools):
    tools.bot.pin_chat_message = MagicMock(
        side_effect=_FakeApiTelegramException("pinChatMessage", "Bad Request", 400)
    )

    result = tools.pin_message(message_id=42)
    parsed = json.loads(result)
    assert parsed["status"] == "error"


# ============================================================================
# GET CHAT TESTS
# ============================================================================


def test_get_chat_success(tools):
    mock_chat = MagicMock()
    mock_chat.id = 12345
    mock_chat.type = "group"
    mock_chat.title = "Test Group"
    mock_chat.username = None
    mock_chat.first_name = None
    mock_chat.last_name = None
    mock_chat.description = "A test group"
    tools.bot.get_chat = MagicMock(return_value=mock_chat)

    result = tools.get_chat()
    tools.bot.get_chat.assert_called_once_with("12345")
    parsed = json.loads(result)
    assert parsed["status"] == "success"
    assert parsed["chat"]["id"] == 12345
    assert parsed["chat"]["type"] == "group"
    assert parsed["chat"]["title"] == "Test Group"


def test_get_chat_api_error(tools):
    tools.bot.get_chat = MagicMock(side_effect=_FakeApiTelegramException("getChat", "Chat not found", 400))

    result = tools.get_chat()
    parsed = json.loads(result)
    assert parsed["status"] == "error"


# ============================================================================
# GET FILE TESTS
# ============================================================================


def test_get_file_success(tools):
    mock_file = MagicMock()
    mock_file.file_id = "ABC123"
    mock_file.file_path = "photos/file_0.jpg"
    mock_file.file_size = 12345
    tools.bot.get_file = MagicMock(return_value=mock_file)
    tools.bot.download_file = MagicMock(return_value=b"fake-image-bytes")

    result = tools.get_file(file_id="ABC123")
    tools.bot.get_file.assert_called_once_with("ABC123")
    tools.bot.download_file.assert_called_once_with("photos/file_0.jpg")
    parsed = json.loads(result)
    assert parsed["status"] == "success"
    assert parsed["file_id"] == "ABC123"
    assert parsed["file_path"] == "photos/file_0.jpg"
    assert "content_base64" in parsed
    assert "local_path" not in parsed


def test_get_file_save_to_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_TOKEN", "fake-token")
    from agno.tools.telegram import TelegramTools

    tools = TelegramTools(chat_id="12345", enable_get_file=True, save_downloads=True, output_directory=str(tmp_path))

    mock_file = MagicMock()
    mock_file.file_id = "ABC123"
    mock_file.file_path = "photos/file_0.jpg"
    mock_file.file_size = 12345
    tools.bot.get_file = MagicMock(return_value=mock_file)
    tools.bot.download_file = MagicMock(return_value=b"fake-image-bytes")

    result = tools.get_file(file_id="ABC123")
    parsed = json.loads(result)
    assert parsed["status"] == "success"
    assert "local_path" in parsed
    assert "content_base64" not in parsed
    assert (tmp_path / "file_0.jpg").exists()
    assert (tmp_path / "file_0.jpg").read_bytes() == b"fake-image-bytes"


def test_get_file_api_error(tools):
    tools.bot.get_file = MagicMock(side_effect=_FakeApiTelegramException("getFile", "File not found", 400))

    result = tools.get_file(file_id="invalid")
    parsed = json.loads(result)
    assert parsed["status"] == "error"


# ============================================================================
# SET REACTION TESTS
# ============================================================================


def test_set_reaction_add(tools):
    tools.bot.set_message_reaction = MagicMock(return_value=True)

    with patch("telebot.types.ReactionTypeEmoji") as mock_reaction_type:
        mock_reaction_type.return_value = "mocked_reaction"
        result = tools.set_reaction(message_id=42, emoji="thumbs_up")

    tools.bot.set_message_reaction.assert_called_once()
    parsed = json.loads(result)
    assert parsed["status"] == "success"
    assert parsed["emoji"] == "thumbs_up"


def test_set_reaction_remove(tools):
    tools.bot.set_message_reaction = MagicMock(return_value=True)

    result = tools.set_reaction(message_id=42, emoji=None)
    tools.bot.set_message_reaction.assert_called_once_with("12345", 42, reaction=None)
    parsed = json.loads(result)
    assert parsed["status"] == "success"
    assert parsed["emoji"] is None


def test_set_reaction_api_error(tools):
    tools.bot.set_message_reaction = MagicMock(
        side_effect=_FakeApiTelegramException("setMessageReaction", "Bad Request", 400)
    )

    with patch("telebot.types.ReactionTypeEmoji"):
        result = tools.set_reaction(message_id=42, emoji="thumbs_up")

    parsed = json.loads(result)
    assert parsed["status"] == "error"
