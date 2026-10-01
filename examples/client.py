"""Real stdio MCP client: python examples/client.py --config config.yaml --database demo."""

import argparse
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--database", default="demo")
    args = parser.parse_args()
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dbmcp.server", "--config", args.config],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        print("Tools:", [tool.name for tool in (await session.list_tools()).tools])
        for name, arguments in [
            ("list_databases", {}),
            ("health_check", {"database": args.database}),
        ]:
            result = await session.call_tool(name, arguments)
            print(result.model_dump_json(indent=2))
            if result.isError:
                raise RuntimeError(f"{name} failed")


asyncio.run(main())
