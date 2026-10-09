import os
import secrets

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

# Fail rather than silently testing SQLite or touching the development database.
test_url = os.environ.get("TEST_DATABASE_URL")
if not test_url or not (make_url(test_url).database or "").endswith("_test"):
    raise RuntimeError("Set TEST_DATABASE_URL to a dedicated PostgreSQL database ending in _test")
os.environ["DATABASE_URL"] = test_url
os.environ["API_TOKEN"] = secrets.token_urlsafe(48)
os.environ["APP_USERNAME"] = "admin-test"
os.environ["APP_PASSWORD"] = secrets.token_urlsafe(32)

from app.database import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.auth import COOKIE_NAME, create_session  # noqa: E402


@pytest.fixture(scope="session")
def engine():
    command.upgrade(Config("alembic.ini"), "head")
    command.check(Config("alembic.ini"))
    engine = create_engine(test_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(engine):
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()


@pytest.fixture
def client(db_session):
    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app, cookies={COOKIE_NAME: create_session()}, headers={"Authorization": "Bearer " + os.environ["API_TOKEN"]}) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def payload():
    return {
        "raw_text": "오늘 벤치프레스 60kg 5x5 했음",
        "category": "운동",
        "subcategory": "웨이트",
        "title": "벤치프레스",
        "summary": "벤치프레스 60kg으로 5세트 5회 수행",
        "tags": ["벤치프레스", "60kg", "5x5"],
        "importance": 3,
        "occurred_at": "2026-10-07T18:00:00+09:00",
    }
