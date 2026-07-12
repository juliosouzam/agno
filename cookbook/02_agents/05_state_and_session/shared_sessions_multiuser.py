"""
Shared Sessions: Multiple Users in One Session
===============================================

This example demonstrates the `shared_sessions` feature that allows multiple users
to share a single session while maintaining per-user memory isolation.

Use Case:
---------
- Group chat scenarios (Slack threads, Discord channels, team chats)
- Multiple users collaborating in the same conversation
- Each user's messages are part of shared history
- Each user's memories remain isolated

SECURITY WARNING:
-----------------
This feature is designed for use with authenticated interfaces (Slack, Discord, etc.)
where user identity is verified by the platform. For production use:

1. Always use with proper authentication (OAuth, JWT, etc.)
2. Never trust user-supplied user_id without verification
3. Consider using `hitl_owner_only=True` to restrict approvals to run owners
4. For Teams/Workflows, ensure proper session isolation

Run: .venvs/demo/bin/python cookbook/02_agents/05_state_and_session/shared_sessions_multiuser.py
"""

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.openai import OpenAIResponses

db = SqliteDb(db_file="tmp/shared_session_demo.db")

agent = Agent(
    name="GroupChatBot",
    model=OpenAIResponses(id="gpt-5.5"),
    db=db,
    instructions=[
        "You are a helpful assistant in a group chat.",
        "Multiple users may be talking to you in the same conversation.",
        "Address users by their user_id when relevant.",
        "Remember that you can see all messages from all users in this session.",
    ],
    # Enable shared sessions - all users share one session
    shared_sessions=True,
    # Enable memory so we can demonstrate per-user isolation
    enable_user_memories=True,
    add_history_to_context=True,
    num_history_runs=10,
    markdown=True,
)

# Simulate a group chat thread with multiple users
SHARED_SESSION_ID = "group-chat-thread-123"

if __name__ == "__main__":
    print("=" * 70)
    print("SHARED SESSIONS DEMO: Multiple Users, One Session")
    print("=" * 70)
    print()
    print("Scenario: Three users (Alice, Bob, Charlie) in a shared chat thread.")
    print("All messages are visible to all users (shared history).")
    print("Memories are isolated per user.")
    print()
    print("-" * 70)

    # Alice starts the conversation
    print("\n[Alice joins the chat]")
    agent.print_response(
        "Hi! I'm Alice. Can you help us plan a team lunch?",
        user_id="alice",
        session_id=SHARED_SESSION_ID,
        stream=True,
    )

    # Bob joins and adds to the conversation
    print("\n[Bob joins the chat]")
    agent.print_response(
        "Hey, I'm Bob. I'm vegetarian, so please keep that in mind.",
        user_id="bob",
        session_id=SHARED_SESSION_ID,
        stream=True,
    )

    # Charlie joins
    print("\n[Charlie joins the chat]")
    agent.print_response(
        "Charlie here! I love spicy food. What restaurants are we considering?",
        user_id="charlie",
        session_id=SHARED_SESSION_ID,
        stream=True,
    )

    # Alice asks a question that requires context from all users
    print("\n[Alice asks for a summary]")
    agent.print_response(
        "Can you summarize everyone's food preferences so far?",
        user_id="alice",
        session_id=SHARED_SESSION_ID,
        stream=True,
    )

    # Bob asks about what Alice said earlier (demonstrates shared history)
    print("\n[Bob references earlier context]")
    agent.print_response(
        "What did Alice originally ask us to plan?",
        user_id="bob",
        session_id=SHARED_SESSION_ID,
        stream=True,
    )

    print()
    print("=" * 70)
    print("KEY OBSERVATIONS:")
    print("=" * 70)
    print()
    print("1. SHARED HISTORY: Bob could ask about Alice's earlier message")
    print("   - All users see the full conversation context")
    print("   - The agent remembers messages from all participants")
    print()
    print("2. USER ATTRIBUTION: Each message is tagged with its user_id")
    print("   - run_context.user_id is set per-message")
    print("   - Useful for tool execution, memory, and attribution")
    print()
    print("3. SESSION OWNERSHIP: First user (Alice) 'owns' the session")
    print("   - Session stored with user_id='alice' in DB")
    print("   - Other users can read/write via shared_session=True")
    print()
    print("4. MEMORY ISOLATION: If enable_user_memories=True")
    print("   - 'Remember my birthday is Jan 5' stays with that user only")
    print("   - Other users don't see or affect each other's memories")
    print()
    print("-" * 70)
    print("For Slack/Discord integration, use the interface's multi_user_threads=True")
    print("which automatically enables shared_session for group conversations.")
    print("-" * 70)
