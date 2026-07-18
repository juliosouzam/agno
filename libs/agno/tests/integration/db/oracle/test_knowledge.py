"""Integration tests for OracleDb knowledge methods"""

import time

from agno.db.schemas.knowledge import KnowledgeRow


def test_upsert_and_get_knowledge_content(oracle_db_real):
    """Test upserting and retrieving knowledge content"""
    knowledge = KnowledgeRow(
        id="test-knowledge-1",
        name="Test Document",
        description="A test document",
        type="document",
        size=1024,
        created_at=int(time.time()),
        updated_at=int(time.time()),
    )

    # Upsert knowledge
    result = oracle_db_real.upsert_knowledge_content(knowledge)
    assert result is not None
    assert result.id == "test-knowledge-1"

    # Get knowledge back
    retrieved = oracle_db_real.get_knowledge_content("test-knowledge-1")
    assert retrieved is not None
    assert retrieved.name == "Test Document"
    assert retrieved.type == "document"


def test_get_knowledge_contents_with_pagination(oracle_db_real):
    """Test getting knowledge contents with pagination"""
    # Create multiple knowledge items
    for i in range(5):
        knowledge = KnowledgeRow(
            id=f"test-pagination-knowledge-{i}",
            name=f"Document {i}",
            description=f"Test document {i}",
            type="document",
            size=1024 + i * 100,
            created_at=int(time.time()),
            updated_at=int(time.time()),
        )
        oracle_db_real.upsert_knowledge_content(knowledge)

    # Get with pagination
    contents, total = oracle_db_real.get_knowledge_contents(limit=2, page=1)
    assert len(contents) <= 2
    assert total >= 5


def test_delete_knowledge_content(oracle_db_real):
    """Test deleting knowledge content"""
    knowledge = KnowledgeRow(
        id="test-delete-knowledge",
        name="To be deleted",
        description="Document to be deleted",
        type="document",
        size=512,
        created_at=int(time.time()),
        updated_at=int(time.time()),
    )

    # Upsert and then delete
    oracle_db_real.upsert_knowledge_content(knowledge)
    oracle_db_real.delete_knowledge_content("test-delete-knowledge")

    # Verify it's gone
    retrieved = oracle_db_real.get_knowledge_content("test-delete-knowledge")
    assert retrieved is None


def test_upsert_knowledge_updates_existing(oracle_db_real):
    """Test that upserting updates existing knowledge"""
    knowledge = KnowledgeRow(
        id="test-update-knowledge",
        name="Original Name",
        description="Original description",
        type="document",
        size=2048,
        created_at=int(time.time()),
        updated_at=int(time.time()),
    )

    # Initial upsert
    oracle_db_real.upsert_knowledge_content(knowledge)

    # Update
    knowledge.name = "Updated Name"
    oracle_db_real.upsert_knowledge_content(knowledge)

    # Verify update
    retrieved = oracle_db_real.get_knowledge_content("test-update-knowledge")
    assert retrieved is not None
    assert retrieved.name == "Updated Name"
