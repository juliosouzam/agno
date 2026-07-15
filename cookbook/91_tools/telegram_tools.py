"""
Telegram Tools - Bot Communication and Messaging

This example demonstrates how to use TelegramTools for Telegram bot operations.
Shows enable_ flag patterns for selective function access.

Prerequisites:
- Create a new bot with BotFather on Telegram: https://core.telegram.org/bots/features#creating-a-new-bot
- Get the token from BotFather
- Send a message to the bot
- Get the chat_id by going to: https://api.telegram.org/bot<your-bot-token>/getUpdates
- Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID environment variables
"""

import os

from agno.agent import Agent
from agno.models.google import Gemini
from agno.tools.telegram import TelegramTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

# Use environment variables for credentials
telegram_token = os.getenv("TELEGRAM_TOKEN", "<enter-your-bot-token>")
chat_id = os.getenv("TELEGRAM_CHAT_ID", "<enter-your-chat-id>")

# Example 1: All functions enabled
agent = Agent(
    name="telegram-full",
    model=Gemini(id="gemini-2.5-flash"),
    tools=[
        TelegramTools(
            token=telegram_token,
            chat_id=chat_id,
            all=True,  # Enable all tools including pin_message, get_chat, get_file
        )
    ],
    description="You are a comprehensive Telegram bot assistant with all messaging capabilities.",
    instructions=[
        "Help users with all Telegram bot operations",
        "Send messages, handle media, and manage bot interactions",
        "Provide clear feedback on bot operations",
    ],
    markdown=True,
)

# Example 2: Agent that reacts to messages with emoji
reaction_agent = Agent(
    name="telegram-reactor",
    model=Gemini(id="gemini-2.5-flash"),
    tools=[
        TelegramTools(
            token=telegram_token,
            chat_id=chat_id,
            enable_react_with_emoji=True,
        )
    ],
    description="You are a Telegram assistant that acknowledges messages with emoji reactions.",
    instructions=[
        "When processing a user request, react to their message with an appropriate emoji",
        "Use thumbs up for success, eyes for thinking, warning for errors",
    ],
    markdown=True,
)

# Example 3: Agent with file download capabilities
download_agent = Agent(
    name="telegram-downloader",
    model=Gemini(id="gemini-2.5-flash"),
    tools=[
        TelegramTools(
            token=telegram_token,
            chat_id=chat_id,
            enable_get_file=True,
            save_downloads=True,
            output_directory="/tmp/telegram_downloads",
        )
    ],
    description="You are a Telegram assistant that downloads files from chat.",
    instructions=[
        "Download files when given a file_id",
        "Report the local path where files are saved",
    ],
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Test 1: Send a message
    print("Test 1: Send message")
    agent.print_response("Send a message saying 'Hello from cookbook test'")

    # Test 2: Get chat info (new tool)
    print("\nTest 2: Get chat info")
    agent.print_response("Get information about this chat")

    # Test 3: Send and pin a message (new tool)
    print("\nTest 3: Pin message")
    agent.print_response("Send 'Cookbook test pinned' and pin it")
