"""Tests for the write API."""
import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient


@pytest.fixture(scope="module")
def write_db():
    """Create a test database with write API enabled."""
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "write_test.sqlite"

    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL,
            _version INTEGER DEFAULT 1
        )
    """)
    conn.execute("INSERT INTO items (name, price) VALUES ('Item1', 10.0)")
    conn.execute("INSERT INTO items (name, price) VALUES ('Item2', 20.0)")
    conn.execute("INSERT INTO items (name, price) VALUES ('Item3', 30.0)")
    conn.commit()
    conn.close()

    return str(db_path), tmp


@pytest.fixture(scope="module")
def write_config(write_db):
    db_path, tmp = write_db
    config_path = Path(tmp) / "config.json"
    config_path.write_text(json.dumps({
        "write_api": {
            "enabled": True,
            "require_auth": True,
            "auth_tokens": ["test-token-123"],
            "permissions": {
                "write_test.items": ["read", "create", "update", "delete"],
            },
            "audit_log": True,
            "idempotency_window_seconds": 3600,
        }
    }))
    return str(config_path)


@pytest.fixture(scope="module")
def write_client(write_db, write_config):
    db_path, tmp = write_db
    os.environ["DATAPORTAL_SOURCES"] = db_path
    os.environ["DATAPORTAL_CONFIG"] = write_config

    import importlib
    import dataportal.app
    importlib.reload(dataportal.app)
    from dataportal.app import app

    with TestClient(app) as client:
        yield client

    del os.environ["DATAPORTAL_SOURCES"]
    del os.environ["DATAPORTAL_CONFIG"]


AUTH_HEADER = {"Authorization": "Bearer test-token-123"}


class TestWriteAPIAuth:
    def test_no_auth_returns_401(self, write_client):
        resp = write_client.post(
            "/api/db/write_test/table/items",
            json={"name": "New", "price": 5.0},
        )
        assert resp.status_code == 401

    def test_bad_token_returns_401(self, write_client):
        resp = write_client.post(
            "/api/db/write_test/table/items",
            json={"name": "New", "price": 5.0},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_valid_token_works(self, write_client):
        resp = write_client.post(
            "/api/db/write_test/table/items",
            json={"name": "AuthTest", "price": 99.0},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201


class TestWriteAPICreate:
    def test_create_single_row(self, write_client):
        resp = write_client.post(
            "/api/db/write_test/table/items",
            json={"name": "NewItem", "price": 15.0},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "created"
        assert data["count"] == 1
        assert len(data["ids"]) == 1

    def test_create_multiple_rows(self, write_client):
        resp = write_client.post(
            "/api/db/write_test/table/items",
            json=[
                {"name": "Batch1", "price": 1.0},
                {"name": "Batch2", "price": 2.0},
            ],
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["count"] == 2

    def test_create_invalid_db(self, write_client):
        resp = write_client.post(
            "/api/db/nonexistent/table/items",
            json={"name": "X"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 404


class TestWriteAPIUpdate:
    def test_update_row(self, write_client):
        resp = write_client.put(
            "/api/db/write_test/table/items/1",
            json={"name": "UpdatedItem1", "price": 11.0},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"

    def test_update_nonexistent_row(self, write_client):
        resp = write_client.put(
            "/api/db/write_test/table/items/99999",
            json={"name": "Ghost"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 404

    def test_optimistic_concurrency(self, write_client):
        # First update should succeed
        resp = write_client.put(
            "/api/db/write_test/table/items/2",
            json={"name": "V2"},
            headers={**AUTH_HEADER, "If-Match": "1"},
        )
        assert resp.status_code == 200

        # Second update with stale version should fail
        resp = write_client.put(
            "/api/db/write_test/table/items/2",
            json={"name": "V3"},
            headers={**AUTH_HEADER, "If-Match": "1"},
        )
        assert resp.status_code == 409


class TestWriteAPIDelete:
    def test_delete_row(self, write_client):
        # First create a row to delete
        resp = write_client.post(
            "/api/db/write_test/table/items",
            json={"name": "ToDelete", "price": 0.0},
            headers=AUTH_HEADER,
        )
        pk = resp.json()["ids"][0]

        resp = write_client.delete(
            f"/api/db/write_test/table/items/{pk}",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_nonexistent(self, write_client):
        resp = write_client.delete(
            "/api/db/write_test/table/items/99999",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 404


class TestWriteAPIBatch:
    def test_batch_operations(self, write_client):
        resp = write_client.post(
            "/api/db/write_test/table/items/_batch",
            json={
                "operations": [
                    {"operation": "create", "data": {"name": "BatchNew", "price": 50.0}},
                    {"operation": "update", "pk": "1", "data": {"name": "BatchUpdated"}},
                ]
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["count"] == 2

    def test_batch_rolls_back_on_error(self, write_client):
        resp = write_client.post(
            "/api/db/write_test/table/items/_batch",
            json={
                "operations": [
                    {"operation": "create", "data": {"name": "WillFail"}},
                    {"operation": "invalid_op", "data": {}},
                ]
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 400


class TestWriteAPIIdempotency:
    def test_idempotency_key_dedup(self, write_client):
        headers = {**AUTH_HEADER, "Idempotency-Key": "unique-key-001"}

        resp1 = write_client.post(
            "/api/db/write_test/table/items",
            json={"name": "Idempotent", "price": 7.0},
            headers=headers,
        )
        assert resp1.status_code == 201
        ids1 = resp1.json()["ids"]

        # Same key should return cached response
        resp2 = write_client.post(
            "/api/db/write_test/table/items",
            json={"name": "Idempotent", "price": 7.0},
            headers=headers,
        )
        assert resp2.status_code == 201
        ids2 = resp2.json()["ids"]
        assert ids1 == ids2
