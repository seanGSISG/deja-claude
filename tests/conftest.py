"""Shared pytest fixtures for claude-memory tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from storage import init_db, store_observation, store_session


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temp SQLite DB, yield its path, clean up after."""
    db_path = str(tmp_path / "test-memory.db")
    conn = init_db(db_path)
    yield db_path
    conn.close()


@pytest.fixture
def populated_db(tmp_db):
    """Temp DB pre-loaded with sample observations and sessions."""
    store_session("session-1", branch="main", working_dir="/project", db_path=tmp_db)
    store_session("session-2", branch="feature", working_dir="/project", db_path=tmp_db)

    store_observation(
        session_id="session-1",
        content="Authentication uses JWT tokens with RS256 signing",
        entities=["JWT", "RS256", "auth-service"],
        topics=["authentication", "security"],
        priority=1,
        importance=0.9,
        source_file="src/auth.py",
        db_path=tmp_db,
    )
    store_observation(
        session_id="session-1",
        content="Database migrations use Alembic with auto-generation",
        entities=["Alembic", "PostgreSQL"],
        topics=["database", "migrations"],
        priority=2,
        importance=0.7,
        source_file="alembic/env.py",
        db_path=tmp_db,
    )
    store_observation(
        session_id="session-2",
        content="API rate limiting is set to 100 requests per minute",
        entities=["rate-limiter", "API"],
        topics=["api", "performance"],
        priority=3,
        importance=0.5,
        db_path=tmp_db,
    )
    store_observation(
        session_id="session-2",
        content="The caching layer uses Redis with 5-minute TTL",
        entities=["Redis", "cache"],
        topics=["caching", "performance"],
        priority=2,
        importance=0.6,
        db_path=tmp_db,
    )

    yield tmp_db
