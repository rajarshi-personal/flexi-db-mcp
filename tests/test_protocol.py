"""Exercise initialization, discovery and calls through an actual stdio subprocess."""

import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_stdio(tmp_path):
    config = tmp_path / "mcp.yaml"
    config.write_text(
        f"databases:\n  test:\n    url: sqlite:///{(tmp_path / 'db.sqlite').as_posix()}\n"
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dbmcp.server", "--config", str(config)],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
        await client.initialize()
        assert len((await client.list_tools()).tools) == 16
        hybrid = await client.call_tool(
            "hybrid_query",
            {
                "plan": {
                    "sources": [
                        {
                            "name": "a",
                            "database": "test",
                            "operation": "query_sql",
                            "sql": "SELECT 1 AS id",
                        },
                        {
                            "name": "b",
                            "database": "test",
                            "operation": "query_sql",
                            "sql": "SELECT 1 AS id, 42 AS amount",
                        },
                    ],
                    "joins": [{"source": "b", "left": "a.id", "right": "b.id"}],
                    "metrics": [{"name": "total", "operation": "sum", "field": "b.amount"}],
                }
            },
        )
        assert not hybrid.isError
        assert hybrid.structuredContent["rows"] == [{"total": "42"}]
        result = await client.call_tool("query_sql", {"database": "test", "sql": "SELECT 42"})
        assert not result.isError
        assert "42" in str(result.content)
        denied = await client.call_tool(
            "execute_sql", {"database": "test", "sql": "DELETE FROM x WHERE id=1"}
        )
        assert denied.isError
        assert (await client.read_resource("dbmcp://catalog")).contents
