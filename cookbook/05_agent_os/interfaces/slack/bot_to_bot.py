"""
Bot-to-Bot Communication
========================

Demonstrates how one Slack bot can receive and respond to messages from
another bot using the `respond_to_bot_messages` flag.

Use case: Bot A (orchestrator) can delegate tasks to Bot B (specialist).

Setup:
  1. Two Slack apps installed to the same workspace:
     - Receiver Bot: receives messages from other bots
     - Sender Bot: sends messages (can be any bot in the workspace)
  2. Event Subscription URL for Receiver:
       https://<tunnel>/slack/events
  3. Environment variables:
       SLACK_TOKEN (Receiver bot token)
       SLACK_SIGNING_SECRET (Receiver signing secret)
       SLACK_SENDER_BOT_TOKEN (Sender bot token, for testing)
  4. ngrok: ngrok http 7777

Key flag: `respond_to_bot_messages=True`
  - Default (False): All bot messages are dropped (standard behavior)
  - True: Only the bot's OWN messages are dropped (echo guard),
          messages from OTHER bots are processed normally

Loop safety:
  - The echo guard prevents infinite loops from own messages
  - Replies don't @mention the sender
  - With reply_to_mentions_only=True, only mentions/DMs are processed
  - WARNING: Two bots with reply_to_mentions_only=False +
    respond_to_bot_messages=True in a shared channel WILL loop
"""

from os import getenv

from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.os.app import AgentOS
from agno.os.interfaces.slack import Slack

receiver_agent = Agent(
    name="Bot Receiver",
    model=OpenAIResponses(id="gpt-4.1-mini"),
    instructions=[
        "You are a specialist bot that receives tasks from other bots.",
        "When another bot sends you a message, acknowledge it and respond helpfully.",
        "Always mention that you received a message from a peer bot.",
    ],
    markdown=True,
)

agent_os = AgentOS(
    agents=[receiver_agent],
    interfaces=[
        Slack(
            agent=receiver_agent,
            token=getenv("SLACK_TOKEN"),
            signing_secret=getenv("SLACK_SIGNING_SECRET"),
            streaming=False,
            reply_to_mentions_only=True,
            respond_to_bot_messages=True,
        ),
    ],
)
app = agent_os.get_app()


def test_bot_to_bot():
    """
    Test sending a message from the sender bot to trigger the receiver.
    Run the server first, then call this function.
    """
    from slack_sdk import WebClient

    sender_token = getenv("SLACK_SENDER_BOT_TOKEN")
    receiver_token = getenv("SLACK_TOKEN")

    if not sender_token or not receiver_token:
        print("Set SLACK_SENDER_BOT_TOKEN and SLACK_TOKEN")
        return

    sender = WebClient(token=sender_token)
    receiver = WebClient(token=receiver_token)

    sender_auth = sender.auth_test()
    receiver_auth = receiver.auth_test()

    print(f"Sender: {sender_auth['user']} (bot_id: {sender_auth.get('bot_id')})")
    print(f"Receiver: {receiver_auth['user']} (bot_id: {receiver_auth.get('bot_id')})")

    receiver_user_id = receiver_auth["user_id"]
    channel = getenv("SLACK_TEST_CHANNEL", "C0AHK2V7P4P")

    print(f"\nSending @mention from sender to receiver in channel {channel}...")
    resp = sender.chat_postMessage(
        channel=channel,
        text=f"<@{receiver_user_id}> Hello from peer bot! Please process this task.",
    )
    print(f"Message sent: ts={resp['ts']}")
    print("Check the channel for the receiver's response.")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_bot_to_bot()
    else:
        agent_os.serve(app="bot_to_bot:app", reload=True)
