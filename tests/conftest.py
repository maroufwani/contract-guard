"""Shared fixtures: every test gets its own temporary SQLite database."""
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.db import session
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """TestClient on a fresh temporary database; never the real data/ file."""
    database_url = f"sqlite:///{(tmp_path / 'contract_guard_test.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    # Tests must never seed the kit, even when docker compose sets SEED_KIT.
    monkeypatch.delenv("SEED_KIT", raising=False)
    session.configure_engine(database_url)

    with TestClient(app) as test_client:
        yield test_client
