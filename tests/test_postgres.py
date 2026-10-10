import os
import time
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.db import Database, normalize_url
from app.models import Base, Paper


def test_database_urls_are_normalised_for_psycopg():
    assert normalize_url("postgres://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"
    assert normalize_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_url("sqlite:///x.db") == "sqlite:///x.db"


def test_schema_compiles_for_postgresql():
    dialect = postgresql.dialect()
    ddl = "\n".join(str(CreateTable(t).compile(dialect=dialect)) for t in Base.metadata.sorted_tables)
    assert "CREATE TABLE papers" in ddl and "JSON" in ddl and "heartbeat_at TIMESTAMP" in ddl


def test_added_not_null_columns_get_postgres_valid_defaults():
    stub = SimpleNamespace(engine=SimpleNamespace(dialect=postgresql.dialect()))
    cols = Paper.__table__.columns
    assert Database._column_default(stub, cols["attempts"]) == " DEFAULT 0"
    assert Database._column_default(stub, cols["heartbeat_at"]) == ""  # nullable needs none
    assert Database._column_default(stub, cols["content"]) == " DEFAULT '{}'"
    assert Database._column_default(stub, cols["worker"]) == " DEFAULT ''"
    from app.models import User
    assert Database._column_default(stub, User.__table__.columns["is_active"]) == " DEFAULT false"


@pytest.mark.skipif(not os.environ.get("EXAMFORGE_TEST_POSTGRES_URL"),
                    reason="set EXAMFORGE_TEST_POSTGRES_URL to run against a live PostgreSQL")
def test_full_paper_flow_on_live_postgres(settings):
    import dataclasses

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(dataclasses.replace(settings, database_url=os.environ["EXAMFORGE_TEST_POSTGRES_URL"]))
    with TestClient(app) as client:
        email = f"pg-{uuid.uuid4().hex[:8]}@example.com"
        r = client.post("/api/auth/register", json={"email": email, "password": "correct-horse-battery", "name": "PG"})
        h = {"Authorization": "Bearer " + r.json()["token"]}
        pid = client.post("/api/papers", headers=h, json={"subject": "Mathematics", "pattern_id": "unit-test-20",
                                                         "seed": 5}).json()["id"]
        end = time.time() + 60
        while time.time() < end:
            paper = client.get(f"/api/papers/{pid}", headers=h).json()
            if paper["status"] in ("ready", "failed"):
                break
            time.sleep(0.3)
        assert paper["status"] == "ready", paper.get("message")
        assert client.get(f"/api/papers/{pid}/export?format=pdf", headers=h).content[:4] == b"%PDF"
