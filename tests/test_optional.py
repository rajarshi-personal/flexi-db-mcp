"""Optional integrations tested without external network services."""

from unittest.mock import MagicMock

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from dbmcp.config import LLM, Database, Limits, Settings
from dbmcp.mongo import MongoAdapter
from dbmcp.policy import PolicyError
from dbmcp.server import create_server


@pytest.fixture
def mongo():
    adapter = MongoAdapter.__new__(MongoAdapter)
    adapter.config = Database(kind="mongodb", url="mongodb://localhost", database="app")
    adapter.limits = Limits(max_rows=2)
    adapter.db = MagicMock()
    return adapter


def test_mongo_denies_writes_before_contact(mongo):
    with pytest.raises(PolicyError, match="disabled"):
        mongo.write("items", "insert_one", {"name": "test"})
    mongo.db.__getitem__.assert_not_called()


@pytest.mark.parametrize(
    "pipeline", [[{"$out": "other"}], [{"$lookup": {}}], [{"$match": {}, "$limit": 1}]]
)
def test_mongo_denies_unsafe_pipelines(mongo, pipeline):
    with pytest.raises(PolicyError):
        mongo.aggregate("items", pipeline)
    mongo.db.__getitem__.assert_not_called()


def test_mongo_pipeline_bounds(mongo):
    collection = mongo.db.__getitem__.return_value
    collection.aggregate.return_value.__enter__.return_value = iter([{"x": 1}, {"x": 2}, {"x": 3}])
    result = mongo.aggregate("items", [{"$match": {}}])
    assert result == {"documents": [{"x": 1}, {"x": 2}], "truncated": True}
    assert collection.aggregate.call_args.args[0][-1] == {"$limit": 3}


async def test_disabled_llm_never_contacts_endpoint(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(httpx, "AsyncClient", client)
    server = create_server(Settings(databases={"test": Database(url="sqlite:///unused.db")}))
    with pytest.raises(ToolError, match="disabled"):
        await server.call_tool(
            "suggest_sql", {"database": "test", "question": "test", "tables": ["x"]}
        )
    client.assert_not_called()


async def test_llm_suggestion_is_not_executed(tmp_path, monkeypatch):
    from sqlalchemy import create_engine

    url = f"sqlite:///{(tmp_path / 'llm.db').as_posix()}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE items (id INTEGER PRIMARY KEY)")
    engine.dispose()
    requests = []

    def respond(request):
        requests.append(request)
        # This nonexistent table proves the suggested SQL is not executed.
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "SELECT * FROM absent"}}]}
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw),
    )
    server = create_server(Settings(databases={"test": Database(url=url)}, llm=LLM(enabled=True)))
    result = await server.call_tool(
        "suggest_sql", {"database": "test", "question": "List items", "tables": ["items"]}
    )
    assert "absent" in str(result)
    assert len(requests) == 1
    assert url.encode() not in requests[0].content
