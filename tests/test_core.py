import sqlite3

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from dbmcp.config import Database, Limits, Settings, load_settings
from dbmcp.policy import PolicyError, validate_mongo, validate_sql
from dbmcp.server import Service, create_server
from dbmcp.sql import SQLAdapter


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "test.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
        conn.executemany("INSERT INTO items VALUES (?, ?)", [(1, "first"), (2, "second")])
    return Database(url=f"sqlite:///{path.as_posix()}", read_only=False)


@pytest.fixture
def adapter(database):
    adapter = SQLAdapter(database, Limits(max_rows=1))
    yield adapter
    adapter.close()


def test_query_bounds_and_parameters(adapter):
    assert adapter.query("SELECT * FROM items ORDER BY id")["truncated"]
    assert adapter.query("SELECT name FROM items WHERE id=:id", {"id": 2})["rows"] == [["second"]]
    assert (
        adapter.query("SELECT * FROM items WHERE name=:name", {"name": "' OR 1=1 --"})["rows"] == []
    )
    with pytest.raises(PolicyError):
        adapter.query("SELECT * FROM items", limit=2)


def test_schema_and_plan(adapter):
    assert "items" in adapter.tables()["tables"]
    assert adapter.describe("items")["primary_key"]["constrained_columns"] == ["id"]
    assert adapter.query("SELECT * FROM items", explain=True)["rows"]


def test_transaction_rollback(adapter):
    with pytest.raises(IntegrityError):
        adapter.execute(
            [
                {"sql": "INSERT INTO items VALUES (3, 'third')"},
                {"sql": "INSERT INTO items VALUES (4, 'first')"},
            ]
        )
    assert adapter.query("SELECT count(*) FROM items")["rows"] == [[2]]
    assert adapter.execute([{"sql": "INSERT INTO items VALUES (3, 'third')"}])["committed"]
    assert adapter.query("SELECT count(*) FROM items")["rows"] == [[3]]
    assert adapter.execute(
        [{"sql": "UPDATE items SET name=:n WHERE id=:i", "params": {"n": "updated", "i": 1}}]
    )["committed"]


def test_read_only(database):
    database.read_only = True
    adapter = SQLAdapter(database, Limits())
    try:
        with pytest.raises(PolicyError):
            adapter.execute([{"sql": "DELETE FROM items WHERE id=1"}])
        with adapter.connection() as conn, pytest.raises(OperationalError):
            conn.exec_driver_sql("DELETE FROM items")
    finally:
        adapter.close()


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM items",
        "SELECT 1; SELECT 2",
        "PRAGMA query_only=OFF",
        "SELECT * INTO new_table FROM items",
        "WITH x AS (DELETE FROM items RETURNING *) SELECT * FROM x",
        "SELECT * FROM items FOR UPDATE",
    ],
)
def test_reject_read_operations(sql):
    with pytest.raises(PolicyError):
        validate_sql(sql, "postgresql")


@pytest.mark.parametrize(
    "sql", ["DROP TABLE items", "DELETE FROM items", "UPDATE items SET name='a'"]
)
def test_reject_writes(sql):
    with pytest.raises(PolicyError):
        validate_sql(sql, "sqlite", write=True)


def test_nested_mongo_policy():
    with pytest.raises(PolicyError):
        validate_mongo([{"$project": {"bad": {"$function": {"body": "code"}}}}])


def test_config(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("databases:\n  test:\n    url: ${TEST_DB_URL}\n")
    monkeypatch.delenv("TEST_DB_URL", raising=False)
    with pytest.raises(ValueError, match="Missing environment"):
        load_settings(path)
    monkeypatch.setenv("TEST_DB_URL", "sqlite:///test.db")
    settings = load_settings(path)
    assert settings.databases["test"].read_only
    assert "sqlite:///test.db" not in repr(settings)


async def test_tools(database):
    server = create_server(Settings(databases={"test": database}))
    tools = await server.list_tools()
    assert len(tools) == 16
    assert next(t for t in tools if t.name == "execute_sql").annotations.destructiveHint
    result = await server.call_tool("query_sql", {"database": "test", "sql": "SELECT 1"})
    assert result


async def test_error_redaction_and_response_cap(database):
    service = Service(
        Settings(databases={"test": database}, limits=Limits(max_response_bytes=1024))
    )
    try:
        with pytest.raises(ValueError) as error:
            await service.call("test", "sql", "query", "SELECT secret_password FROM missing")
        assert "secret_password" not in str(error.value)
        with pytest.raises(PolicyError, match="byte limit"):
            await service.call("test", "sql", "query", "SELECT hex(zeroblob(2000))")
    finally:
        service.close()
