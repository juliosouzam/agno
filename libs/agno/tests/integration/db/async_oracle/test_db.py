"""Integration tests for the setup and main methods of the AsyncOracleDb class"""

import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from agno.db.oracle import AsyncOracleDb


@pytest.mark.asyncio
async def test_init_with_db_url():
    """Test initialization with actual database URL format"""
    db_url = "oracle+oracledb_async://ai:ai@localhost:1521/?service_name=FREEPDB1"

    db = AsyncOracleDb(db_url=db_url, session_table="test_async_oracle_sessions")
    assert db.db_url == db_url
    assert db.session_table_name == "test_async_oracle_sessions"

    # Test connection
    async with db.async_session_factory() as sess:
        result = await sess.execute(text("SELECT 1 FROM dual"))
        assert result.scalar() == 1

    await db.db_engine.dispose()


@pytest.mark.asyncio
async def test_create_session_table_integration(async_oracle_db_real):
    """Test actual session table creation with Oracle"""
    await async_oracle_db_real._create_table("test_async_oracle_sessions", "sessions")

    # Oracle stores unquoted identifiers uppercase in the data dictionary
    async with async_oracle_db_real.async_session_factory() as sess:
        result = await sess.execute(
            text("SELECT table_name FROM user_tables WHERE table_name = 'TEST_ASYNC_ORACLE_SESSIONS'")
        )
        assert result.scalar() == "TEST_ASYNC_ORACLE_SESSIONS"

    assert await async_oracle_db_real.table_exists("test_async_oracle_sessions")


@pytest.mark.asyncio
async def test_create_metrics_table_with_constraints(async_oracle_db_real):
    """Test creating metrics table with unique constraints"""
    await async_oracle_db_real._create_table("test_metrics", "metrics")

    # Verify unique constraint exists (named f"{table_name}_{constraint_name}" by _create_table)
    async with async_oracle_db_real.async_session_factory() as sess:
        result = await sess.execute(
            text(
                "SELECT constraint_name FROM user_constraints "
                "WHERE table_name = 'TEST_METRICS' AND constraint_type = 'U'"
            )
        )
        constraints = [row[0] for row in result.fetchall()]
        assert any("UQ_METRICS_DATE_PERIOD" in c for c in constraints)


@pytest.mark.asyncio
async def test_create_table_with_indexes(async_oracle_db_real):
    """Test that indexes are created correctly"""
    await async_oracle_db_real._create_table("test_memories", "memories")

    # Verify indexes exist (named idx_{table_name}_{col} by _create_table, uppercased by Oracle)
    async with async_oracle_db_real.async_session_factory() as sess:
        result = await sess.execute(text("SELECT index_name FROM user_indexes WHERE table_name = 'TEST_MEMORIES'"))
        indexes = [row[0] for row in result.fetchall()]

        # Should have indexes on user_id and updated_at
        assert any("USER_ID" in idx for idx in indexes)
        assert any("UPDATED_AT" in idx for idx in indexes)


@pytest.mark.asyncio
async def test_get_or_create_existing_table(async_oracle_db_real):
    """Test getting an existing table"""
    # First create the table
    await async_oracle_db_real._create_table("test_async_oracle_sessions", "sessions")

    # Clear the cached table attribute
    if hasattr(async_oracle_db_real, "session_table"):
        delattr(async_oracle_db_real, "session_table")

    # Now get it again - should not recreate
    with patch.object(async_oracle_db_real, "_create_table", new=AsyncMock()) as mock_create:
        table = await async_oracle_db_real._get_or_create_table(
            table_name="test_async_oracle_sessions", table_type="sessions", create_table_if_not_found=True
        )

        # Should not call create since table exists
        mock_create.assert_not_called()

    assert table.name == "test_async_oracle_sessions"


@pytest.mark.asyncio
async def test_full_workflow(async_oracle_db_real):
    """Test a complete workflow of creating and using tables"""
    # Get tables (will create them)
    session_table = await async_oracle_db_real._get_table("sessions", create_table_if_not_found=True)
    await async_oracle_db_real._get_table("memories", create_table_if_not_found=True)

    # Verify tables are cached
    assert hasattr(async_oracle_db_real, "session_table")
    assert hasattr(async_oracle_db_real, "memory_table")

    # Verify we can insert data (basic smoke test)
    async with async_oracle_db_real.async_session_factory() as sess:
        # Insert a test session
        await sess.execute(
            session_table.insert().values(
                session_id="test-session-123",
                session_type="agent",
                created_at=int(datetime.now(timezone.utc).timestamp() * 1000),
                session_data={"test": "data"},
            )
        )
        await sess.commit()

        # Query it back
        result = await sess.execute(session_table.select().where(session_table.c.session_id == "test-session-123"))
        row = result.fetchone()

        assert row is not None
        assert row.session_type == "agent"


# -- Oracle-specific extra cases --


@pytest.mark.asyncio
async def test_json_column_round_trips_payload_over_4000_bytes(async_oracle_db_real):
    """Round-trip a JSON payload bigger than Oracle's classic VARCHAR2(4000) limit.

    Confirms the CLOB-backed OracleJSON column type used across oracle/schemas.py
    does not truncate/reject large documents.
    """
    from agno.session import AgentSession

    large_value = "x" * 4500
    assert len(large_value.encode("utf-8")) > 4000

    session = AgentSession(
        session_id="test-large-json-session",
        agent_id="test-agent",
        user_id="test-user",
        session_data={"large_field": large_value, "nested": {"more_data": large_value}},
        created_at=int(time.time()),
    )

    result = await async_oracle_db_real.upsert_session(session)
    assert result is not None

    retrieved = await async_oracle_db_real.get_session(session_id="test-large-json-session", session_type=None)
    assert retrieved is not None
    assert retrieved.session_data["large_field"] == large_value
    assert retrieved.session_data["nested"]["more_data"] == large_value


@pytest.mark.asyncio
async def test_upsert_schema_version_merge_is_idempotent(async_oracle_db_real):
    """MERGE-based upsert_schema_version must not create duplicate rows on a second call.

    Uses a unique `table_name` key so this test is independent from other tests/suites
    that share the (un-prefixed, not test-table-scoped) `agno_schema_versions` table.
    """
    await async_oracle_db_real._get_table("versions", create_table_if_not_found=True)
    version_key = f"test_merge_idempotent_{uuid.uuid4().hex[:8]}"

    await async_oracle_db_real.upsert_schema_version(table_name=version_key, version="2.0.0")
    await async_oracle_db_real.upsert_schema_version(table_name=version_key, version="2.1.0")

    versions_table_name = async_oracle_db_real.versions_table_name
    async with async_oracle_db_real.async_session_factory() as sess:
        result = (
            await sess.execute(
                text(f"SELECT version, COUNT(*) FROM {versions_table_name} WHERE table_name = :t GROUP BY version"),
                {"t": version_key},
            )
        ).fetchall()

    # A single row for the table_name key, holding the latest version written
    assert len(result) == 1
    assert result[0][0] == "2.1.0"
    assert result[0][1] == 1
