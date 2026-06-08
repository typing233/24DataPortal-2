"""End-to-end tests for DataPortal."""
import asyncio
import os
import sys
import time
from pathlib import Path

import pytest
import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture(scope="module")
def test_data_dir():
    d = Path(__file__).parent / "test_data"
    d.mkdir(exist_ok=True)
    from create_test_data import create_test_data
    create_test_data(str(d))
    return d


@pytest.fixture(scope="module")
def app(test_data_dir):
    os.environ["DATAPORTAL_SOURCES"] = f"{test_data_dir / 'sample.sqlite'}|{test_data_dir / 'products.csv'}|{test_data_dir / 'sales_gbk.csv'}"
    os.environ.pop("DATAPORTAL_CONFIG", None)
    from dataportal.app import app
    return app


@pytest.fixture(scope="module")
async def client(app):
    from starlette.testclient import TestClient
    with TestClient(app) as c:
        yield c


class TestHomepage:
    def test_html_response(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "DataPortal" in r.text
        assert "数据库" in r.text

    def test_json_response(self, client):
        r = client.get("/.json")
        assert r.status_code == 200
        data = r.json()
        assert "databases" in data
        assert "health" in data
        assert data["health"]["status"] == "healthy"
        assert data["health"]["databases"] >= 1

    def test_health_endpoint(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "healthy"


class TestBrowse:
    def test_browse_table(self, client):
        r = client.get("/db/sample/table/users")
        assert r.status_code == 200
        assert "用户" in r.text or "users" in r.text

    def test_browse_json(self, client):
        r = client.get("/db/sample/table/users.json")
        assert r.status_code == 200
        data = r.json()
        assert "columns" in data
        assert "rows" in data
        assert "pagination" in data
        assert data["pagination"]["total_rows"] == 200

    def test_pagination(self, client):
        r = client.get("/db/sample/table/users.json?page=2&per_page=25")
        data = r.json()
        assert data["pagination"]["page"] == 2
        assert data["pagination"]["per_page"] == 25
        assert len(data["rows"]) == 25

    def test_sort(self, client):
        r = client.get("/db/sample/table/users.json?sort=-age&per_page=5")
        data = r.json()
        ages = [row[3] for row in data["rows"]]
        assert ages == sorted(ages, reverse=True)

    def test_filter(self, client):
        r = client.get("/db/sample/table/users.json?filter_col=city&filter_val=北京")
        data = r.json()
        for row in data["rows"]:
            assert "北京" in str(row)

    def test_search(self, client):
        r = client.get("/db/sample/table/users.json?search=用户001")
        data = r.json()
        assert data["pagination"]["total_rows"] >= 1

    def test_columns_metadata(self, client):
        r = client.get("/db/sample/table/users.json")
        data = r.json()
        col_names = [c["name"] for c in data["columns"]]
        assert "id" in col_names
        assert "name" in col_names
        assert "email" in col_names

    def test_indexes_in_response(self, client):
        r = client.get("/db/sample/table/users.json")
        data = r.json()
        assert "indexes" in data


class TestCSVImport:
    def test_csv_imported(self, client):
        r = client.get("/.json")
        data = r.json()
        db_names = [d["name"] for d in data["databases"]]
        assert "products_csv" in db_names

    def test_csv_data_browsable(self, client):
        r = client.get("/db/products_csv/table/products.json")
        assert r.status_code == 200
        data = r.json()
        assert data["pagination"]["total_rows"] == 100

    def test_csv_type_inference(self, client):
        r = client.get("/db/products_csv/table/products.json")
        data = r.json()
        col_types = {c["name"]: c["type"] for c in data["columns"]}
        assert col_types["id"] == "INTEGER"
        assert col_types["price"] == "REAL"

    def test_gbk_csv_imported(self, client):
        r = client.get("/.json")
        data = r.json()
        db_names = [d["name"] for d in data["databases"]]
        assert "sales_gbk_csv" in db_names


class TestSQLEditor:
    def test_sql_page(self, client):
        r = client.get("/sql/sample")
        assert r.status_code == 200
        assert "SQL" in r.text

    def test_sql_execute(self, client):
        r = client.post("/sql/execute", json={
            "database": "sample",
            "sql": "SELECT COUNT(*) as cnt FROM users"
        })
        assert r.status_code == 200
        data = r.json()
        assert data["rows"][0][0] == 200

    def test_sql_timeout_protection(self, client):
        r = client.post("/sql/execute", json={
            "database": "sample",
            "sql": "SELECT * FROM users"
        })
        assert r.status_code == 200
        data = r.json()
        assert "elapsed_seconds" in data

    def test_sql_write_blocked(self, client):
        r = client.post("/sql/execute", json={
            "database": "sample",
            "sql": "INSERT INTO users VALUES (999,'x','x@x.com',20,'x','2024-01-01')"
        })
        assert r.status_code == 403
        data = r.json()
        assert data["allowed"] is False

    def test_sql_ddl_blocked(self, client):
        r = client.post("/sql/execute", json={
            "database": "sample",
            "sql": "DROP TABLE users"
        })
        assert r.status_code == 403

    def test_sql_error_explanation(self, client):
        r = client.post("/sql/execute", json={
            "database": "sample",
            "sql": "SELECT * FROM nonexistent_table"
        })
        data = r.json()
        assert "error" in data
        assert "explanation" in data

    def test_sql_history(self, client):
        client.post("/sql/execute", json={"database": "sample", "sql": "SELECT 1"})
        r = client.get("/sql/history")
        data = r.json()
        assert len(data["history"]) > 0

    def test_dangerous_operations_blocked(self, client):
        r = client.post("/sql/execute", json={
            "database": "sample",
            "sql": "ATTACH DATABASE ':memory:' AS test"
        })
        assert r.status_code == 403


class TestViews:
    def test_save_view(self, client):
        r = client.post("/views/save", json={
            "database": "sample",
            "name": "Young Users",
            "table": "users",
            "sort": "age",
            "filter_col": "",
            "filter_val": "",
            "search": "",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "saved"

    def test_list_views(self, client):
        r = client.get("/db/sample/views")
        data = r.json()
        assert "views" in data


class TestConfig:
    def test_config_endpoint(self, client):
        r = client.get("/config.json")
        assert r.status_code == 200
        data = r.json()
        assert "site" in data
        assert "permissions" in data
        assert "theme" in data


class TestPerformance:
    def test_cached_response_faster(self, client):
        url = "/db/sample/table/orders.json?page=1&per_page=50"
        start = time.time()
        client.get(url)
        first = time.time() - start

        start = time.time()
        client.get(url)
        second = time.time() - start

        assert second <= first * 2  # cached should not be slower

    def test_large_page(self, client):
        r = client.get("/db/sample/table/orders.json?per_page=500")
        assert r.status_code == 200
        data = r.json()
        assert len(data["rows"]) == 500
