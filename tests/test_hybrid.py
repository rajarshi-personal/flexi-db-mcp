import sqlite3
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from dbmcp.config import Database, Limits, Settings
from dbmcp.hybrid import HybridPlan, combine, execute_hybrid
from dbmcp.mongo import MongoAdapter
from dbmcp.policy import PolicyError
from dbmcp.server import Service


def plan(**changes):
    data = {
        "sources": [
            {
                "name": "orders",
                "database": "sql",
                "operation": "query_sql",
                "sql": "SELECT id, amount FROM orders ORDER BY id",
            },
            {
                "name": "customers",
                "database": "mongo",
                "operation": "find_documents",
                "collection": "customers",
            },
        ],
        "joins": [
            {"source": "customers", "left": "orders.id", "right": "customers.id", "how": "left"}
        ],
        "group_by": ["customers.region"],
        "metrics": [
            {"name": "total", "operation": "sum", "field": "orders.amount"},
            {"name": "count", "operation": "count"},
        ],
    }
    return HybridPlan.model_validate({**data, **changes})


async def test_real_sql_and_mocked_mongo_driver(tmp_path):
    path = tmp_path / "hybrid.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE orders (id INTEGER, amount NUMERIC)")
        conn.executemany("INSERT INTO orders VALUES (?, ?)", [(1, 10), (2, 20), (3, 30)])
    settings = Settings(
        databases={
            "sql": Database(url=f"sqlite:///{path.as_posix()}"),
            "mongo": Database(kind="mongodb", url="mongodb://localhost", database="app"),
        }
    )
    service = Service(settings)
    mongo = MongoAdapter.__new__(MongoAdapter)
    mongo.config = settings.databases["mongo"]
    mongo.limits = settings.limits
    mongo.client = MagicMock()
    mongo.db = MagicMock()
    cursor = mongo.db.__getitem__.return_value.find.return_value.limit.return_value.max_time_ms.return_value
    cursor.__enter__.return_value = iter([{"id": 1, "region": "west"}, {"id": 2, "region": "west"}])
    service.adapters["mongo"] = mongo
    try:
        result = await execute_hybrid(service, plan())
        assert result["rows"] == [
            {"customers.region": "west", "total": "30", "count": 2},
            {"customers.region": None, "total": "30", "count": 1},
        ]
        assert result["joined_rows"] == 3
        assert not result["consistent_snapshot"]
    finally:
        service.close()


def test_null_and_typed_join_keys():
    result = combine(
        plan(metrics=[], group_by=[]),
        {
            "orders": [{"id": None}, {"id": 1}, {"id": "1"}],
            "customers": [{"id": None}, {"id": "1"}],
        },
        Limits(),
    )
    assert [row["customers"] for row in result["rows"]] == [None, None, {"id": "1"}]


def test_join_expansion_limit():
    with pytest.raises(PolicyError, match="max_hybrid_rows"):
        combine(
            plan(),
            {"orders": [{"id": 1}] * 3, "customers": [{"id": 1}] * 3},
            Limits(max_hybrid_rows=5),
        )


def test_empty_global_aggregates():
    result = combine(plan(group_by=[]), {"orders": [], "customers": []}, Limits())
    assert result["rows"] == [{"total": None, "count": 0}]


def test_numeric_metrics_and_output_limit():
    metrics = [
        {"name": op, "operation": op, "field": "orders.amount"}
        for op in ["sum", "avg", "min", "max", "count"]
    ]
    result = combine(
        plan(metrics=metrics, limit=1),
        {
            "orders": [
                {"id": 1, "amount": "0.1"},
                {"id": 2, "amount": "0.2"},
                {"id": 3, "amount": "5"},
            ],
            "customers": [
                {"id": 1, "region": "a"},
                {"id": 2, "region": "a"},
                {"id": 3, "region": "b"},
            ],
        },
        Limits(),
    )
    assert result["rows"][0] == {
        "customers.region": "a",
        "sum": "0.3",
        "avg": "0.15",
        "min": "0.1",
        "max": "0.2",
        "count": 2,
    }
    assert result["truncated"] and result["result_rows"] == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"joins": []},
        {
            "sources": [
                {"name": "x", "database": "sql", "operation": "execute_sql", "sql": "DELETE FROM x"}
            ]
        },
    ],
)
def test_invalid_plan(changes):
    with pytest.raises(ValidationError):
        plan(**changes)


async def test_truncated_inputs_fail():
    class FakeService:
        settings = Settings(
            databases={
                "sql": Database(url="sqlite:///unused"),
                "mongo": Database(kind="mongodb", url="mongodb://localhost", database="app"),
            }
        )

        async def call(self, name, *_):
            if name == "sql":
                return {"columns": ["id"], "rows": [[1]], "truncated": True}
            return {"documents": [], "truncated": False}

    with pytest.raises(PolicyError, match="truncated"):
        await execute_hybrid(FakeService(), plan())


def test_missing_and_nonnumeric_fields_fail():
    with pytest.raises(PolicyError, match="Missing field"):
        combine(plan(), {"orders": [{"id": 1}], "customers": []}, Limits())
    with pytest.raises(PolicyError, match="nonnumeric"):
        combine(plan(), {"orders": [{"id": 1, "amount": "bad"}], "customers": []}, Limits())


def test_response_byte_limit():
    with pytest.raises(PolicyError, match="byte limit"):
        combine(
            plan(metrics=[], group_by=[]),
            {"orders": [{"id": 1, "text": "x" * 2000}], "customers": []},
            Limits(max_response_bytes=1024),
        )
