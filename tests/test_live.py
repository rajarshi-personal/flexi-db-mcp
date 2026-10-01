"""Opt-in health and metadata checks against real configured databases."""

import os

import pytest

from dbmcp.config import load_settings
from dbmcp.server import Service


@pytest.mark.skipif(
    not os.environ.get("DBMCP_LIVE_CONFIG"), reason="No live database configuration"
)
async def test_live_databases():
    settings = load_settings(os.environ["DBMCP_LIVE_CONFIG"])
    service = Service(settings)
    try:
        for name, config in settings.databases.items():
            assert (await service.call(name, None, "health"))["healthy"]
            if config.kind == "sql":
                await service.call(name, "sql", "tables")
                adapter = service.adapter(name)
                sql = "SELECT 1 FROM DUAL" if adapter.dialect == "oracle" else "SELECT 1"
                assert (await service.call(name, "sql", "query", sql))["rows"]
            else:
                await service.call(name, "mongodb", "collections")
    finally:
        service.close()
