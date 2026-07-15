import json
from os import getenv
from pathlib import Path
from typing import Any, List, Optional

from agno.tools import Toolkit
from agno.utils.log import log_debug

try:
    from telebot import TeleBot
    from telebot.apihelper import ApiTelegramException
    from telebot.types import ReactionTypeEmoji
except ImportError as e:
    raise ImportError("`pyTelegramBotAPI` not installed. Please install using `pip install 'agno[telegram]'`") from e


class TelegramTools(Toolkit):
    """Toolkit for sending messages and media via the Telegram Bot API.

    Args:
        chat_id: Default chat ID. Falls back to TELEGRAM_CHAT_ID env var.
        token: Bot token. Falls back to TELEGRAM_TOKEN env var.
        output_directory: Directory for saving downloaded files. Only used when save_downloads=True.
        save_downloads: Save downloaded files to disk instead of returning base64.
        enable_send_message: Enable send_message tool. Defaults to True.
        enable_send_photo: Enable send_photo tool. Defaults to False.
        enable_send_document: Enable send_document tool. Defaults to False.
        enable_send_video: Enable send_video tool. Defaults to False.
        enable_send_audio: Enable send_audio tool. Defaults to False.
        enable_send_animation: Enable send_animation tool. Defaults to False.
        enable_send_sticker: Enable send_sticker tool. Defaults to False.
        enable_edit_message: Enable edit_message tool. Defaults to False.
        enable_delete_message: Enable delete_message tool. Defaults to False.
        enable_react_with_emoji: Enable react_with_emoji tool. Defaults to False.
        enable_pin_message: Enable pin_message tool. Defaults to False.
        enable_get_chat: Enable get_chat tool. Defaults to False.
        enable_get_file: Enable get_file tool. Defaults to False.
        all: Enable all tools. Overrides individual flags when True.
    """

    def __init__(
        self,
        chat_id: Optional[str] = None,
        token: Optional[str] = None,
        output_directory: Optional[str] = None,
        save_downloads: bool = False,
        enable_send_message: bool = True,
        enable_send_photo: bool = False,
        enable_send_document: bool = False,
        enable_send_video: bool = False,
        enable_send_audio: bool = False,
        enable_send_animation: bool = False,
        enable_send_sticker: bool = False,
        enable_edit_message: bool = False,
        enable_delete_message: bool = False,
        enable_react_with_emoji: bool = False,
        enable_pin_message: bool = False,
        enable_get_chat: bool = False,
        enable_get_file: bool = False,
        all: bool = False,
        **kwargs: Any,
    ):
        self.token = token or getenv("TELEGRAM_TOKEN")
        if not self.token:
            raise ValueError("TELEGRAM_TOKEN not set. Please set the TELEGRAM_TOKEN environment variable.")

        self.chat_id = chat_id or getenv("TELEGRAM_CHAT_ID")
        self.bot = TeleBot(self.token)

        # output_directory implies save_downloads=True
        self.save_downloads = save_downloads or (output_directory is not None)
        if self.save_downloads:
            self.output_directory: Optional[Path] = (
                Path(output_directory).resolve() if output_directory else Path.cwd().resolve()
            )
            self.output_directory.mkdir(parents=True, exist_ok=True)
            log_debug(f"Downloaded files will be saved to: {self.output_directory}")
        else:
            self.output_directory = None

        tools: List[Any] = []
        if enable_send_message or all:
            tools.append(self.send_message)
        if enable_send_photo or all:
            tools.append(self.send_photo)
        if enable_send_document or all:
            tools.append(self.send_document)
        if enable_send_video or all:
            tools.append(self.send_video)
        if enable_send_audio or all:
            tools.append(self.send_audio)
        if enable_send_animation or all:
            tools.append(self.send_animation)
        if enable_send_sticker or all:
            tools.append(self.send_sticker)
        if enable_edit_message or all:
            tools.append(self.edit_message)
        if enable_delete_message or all:
            tools.append(self.delete_message)
        if enable_react_with_emoji or all:
            tools.append(self.react_with_emoji)
        if enable_pin_message or all:
            tools.append(self.pin_message)
        if enable_get_chat or all:
            tools.append(self.get_chat)
        if enable_get_file or all:
            tools.append(self.get_file)

        super().__init__(name="telegram", tools=tools, **kwargs)

    @property
    def _chat_id(self) -> str:
        if not self.chat_id:
            raise ValueError(
                "chat_id is required. Set it in the constructor or set the TELEGRAM_CHAT_ID environment variable."
            )
        return self.chat_id

    def send_message(self, message: str) -> str:
        """Send a text message to the chat.

        Args:
            message: The message text to send.
        """
        log_debug(f"Sending telegram message: {message}")
        try:
            result = self.bot.send_message(self._chat_id, message)
            return json.dumps({"status": "success", "message_id": result.message_id})
        except ApiTelegramException as e:
            return json.dumps({"status": "error", "message": str(e)})

    def send_photo(self, photo: bytes, caption: Optional[str] = None) -> str:
        """Send a photo to the chat.

        Args:
            photo: The photo as bytes.
            caption: Optional caption.
        """
        try:
            result = self.bot.send_photo(self._chat_id, photo, caption=caption)
            return json.dumps({"status": "success", "message_id": result.message_id})
        except ApiTelegramException as e:
            return json.dumps({"status": "error", "message": str(e)})

    def send_document(self, document: bytes, filename: str, caption: Optional[str] = None) -> str:
        """Send a document to the chat.

        Args:
            document: The document as bytes.
            filename: The filename for the document.
            caption: Optional caption.
        """
        try:
            result = self.bot.send_document(self._chat_id, (filename, document), caption=caption)
            return json.dumps({"status": "success", "message_id": result.message_id})
        except ApiTelegramException as e:
            return json.dumps({"status": "error", "message": str(e)})

    def send_video(self, video: bytes, caption: Optional[str] = None) -> str:
        """Send a video to the chat.

        Args:
            video: The video as bytes.
            caption: Optional caption.
        """
        try:
            result = self.bot.send_video(self._chat_id, video, caption=caption)
            return json.dumps({"status": "success", "message_id": result.message_id})
        except ApiTelegramException as e:
            return json.dumps({"status": "error", "message": str(e)})

    def send_audio(self, audio: bytes, caption: Optional[str] = None, title: Optional[str] = None) -> str:
        """Send an audio file to the chat.

        Args:
            audio: The audio as bytes.
            caption: Optional caption.
            title: Optional title for the audio track.
        """
        try:
            result = self.bot.send_audio(self._chat_id, audio, caption=caption, title=title)
            return json.dumps({"status": "success", "message_id": result.message_id})
        except ApiTelegramException as e:
            return json.dumps({"status": "error", "message": str(e)})

    def send_animation(self, animation: bytes, caption: Optional[str] = None) -> str:
        """Send an animation (GIF) to the chat.

        Args:
            animation: The animation as bytes.
            caption: Optional caption.
        """
        try:
            result = self.bot.send_animation(self._chat_id, animation, caption=caption)
            return json.dumps({"status": "success", "message_id": result.message_id})
        except ApiTelegramException as e:
            return json.dumps({"status": "error", "message": str(e)})

    def send_sticker(self, sticker: bytes) -> str:
        """Send a sticker to the chat.

        Args:
            sticker: The sticker as bytes.
        """
        try:
            result = self.bot.send_sticker(self._chat_id, sticker)
            return json.dumps({"status": "success", "message_id": result.message_id})
        except ApiTelegramException as e:
            return json.dumps({"status": "error", "message": str(e)})

    def edit_message(self, text: str, message_id: int) -> str:
        """Edit a previously sent message.

        Args:
            text: The new message text.
            message_id: The ID of the message to edit.
        """
        try:
            result = self.bot.edit_message_text(text, chat_id=self._chat_id, message_id=message_id)
            # edit_message_text returns Message on success, bool on inline message
            msg_id = result.message_id if hasattr(result, "message_id") else message_id
            return json.dumps({"status": "success", "message_id": msg_id})
        except ApiTelegramException as e:
            return json.dumps({"status": "error", "message": str(e)})

    def delete_message(self, message_id: int) -> str:
        """Delete a message from the chat.

        Args:
            message_id: The ID of the message to delete.
        """
        try:
            self.bot.delete_message(self._chat_id, message_id)
            return json.dumps({"status": "success", "deleted": True})
        except ApiTelegramException as e:
            return json.dumps({"status": "error", "message": str(e)})

    def react_with_emoji(self, message_id: int, emoji: str) -> str:
        """React to a message with an emoji.

        Args:
            message_id: The ID of the message to react to.
            emoji: The emoji to react with.
        """
        try:
            self.bot.set_message_reaction(
                chat_id=self._chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)],
            )
            return json.dumps({"status": "success", "message_id": message_id, "emoji": emoji})
        except ApiTelegramException as e:
            return json.dumps({"status": "error", "message": str(e)})

    def pin_message(self, message_id: int, disable_notification: bool = False) -> str:
        """Pin a message in the chat.

        Args:
            message_id: The ID of the message to pin.
            disable_notification: If True, no notification is sent to chat members.
        """
        try:
            self.bot.pin_chat_message(self._chat_id, message_id, disable_notification=disable_notification)
            return json.dumps({"status": "success", "pinned": True})
        except ApiTelegramException as e:
            return json.dumps({"status": "error", "message": str(e)})

    def get_chat(self) -> str:
        """Get information about the current chat."""
        try:
            chat = self.bot.get_chat(self._chat_id)
            return json.dumps(chat.__dict__)
        except ApiTelegramException as e:
            return json.dumps({"error": str(e)})

    def get_file(self, file_id: str) -> str:
        """Download a file by its file_id. Returns path if save_downloads=True, else base64.

        Args:
            file_id: The file_id from a Telegram message (photo, document, etc.).
        """
        import base64

        try:
            file_info = self.bot.get_file(file_id)
            if not file_info.file_path:
                return json.dumps({"error": "File path not available"})
            file_content = self.bot.download_file(file_info.file_path)

            result = dict(file_info.__dict__)
            if self.save_downloads and self.output_directory:
                filename = Path(file_info.file_path).name
                local_path = self.output_directory / filename
                local_path.write_bytes(file_content)
                result["local_path"] = str(local_path)
            else:
                result["content_base64"] = base64.b64encode(file_content).decode("utf-8")

            return json.dumps(result)
        except ApiTelegramException as e:
            return json.dumps({"error": str(e)})
