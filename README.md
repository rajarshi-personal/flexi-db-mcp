# Portable Database MCP

A Python MCP server for **SQLite, PostgreSQL, MySQL/MariaDB, Oracle, SQL Server and MongoDB**, with 16 tools, a database catalog resource, and an investigation prompt. Run it on a workstation, a VM or your own servers. No cloud account, managed database, or LLM is required.

Python was chosen for SQLAlchemy's database dialects, mature vendor drivers, PyMongo, and the official MCP SDK. Blocking drivers run in a bounded worker pool so they do not block the MCP event loop. Connections are created lazily and pooled. Each SQL write request owns its transaction; no transaction is held open between tool calls.

This implementation deliberately uses the supported **MCP Python SDK 1.x FastMCP API**, with an explicit `<2` dependency boundary. It does not mix the incompatible SDK 2.x API into the application.

## Quick start: no external database

Requires Python 3.11 or newer. Run commands from this repository's root.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe scripts/seed_demo.py
.\.venv\Scripts\python.exe examples/client.py
.\.venv\Scripts\python.exe -m pytest -q
```

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python scripts/seed_demo.py
.venv/bin/python examples/client.py
.venv/bin/python -m pytest -q
```

The seed creates `customers` and `orders` in `demo.db` without deleting existing records. The example starts a real MCP subprocess, initializes a session, lists all tools and checks the demo database. The default configuration is read-only. SQLite file paths are relative to the **server process working directory**; use absolute URLs in desktop client configurations.

Start the server directly:

```powershell
.\.venv\Scripts\dbmcp.exe --config config.yaml
```

The default transport is stdio. Waiting silently for a client is normal; do not type ordinary text into its input. Logs go to stderr so stdout remains an MCP protocol stream.

## Database configuration

Copy `config.example.yaml` to `config.local.yaml`. Delete unused aliases, then provide the environment variables for those retained. Configuration is validated strictly: unknown fields and missing environment variables fail startup. `${VARIABLE}` substitution happens after YAML parsing. `.env` files are **not automatically loaded**; set environment variables in your shell or process manager.

```yaml
databases:
  reporting:
    kind: sql
    url: ${REPORTING_DB_URL}
    read_only: true
    pool_size: 5
    connect_args:
      connect_timeout: 10
limits:
  max_rows: 500
  max_response_bytes: 1000000
  max_sql_chars: 50000
  timeout_seconds: 30
  max_concurrency: 8
  max_batch_statements: 20
  max_hybrid_rows: 10000
llm:
  enabled: false
  base_url: http://127.0.0.1:11434/v1
  model: qwen2.5-coder:7b
  api_key: ""
  timeout_seconds: 60
```

`connect_args` are vendor-specific SQL driver keyword arguments, not interchangeable across backends. Omit the PostgreSQL `connect_timeout` example when using a driver that does not accept it. MongoDB uses the configured pool and timeout settings directly. Keep secret configuration files out of version control. All configured aliases are accessible to the connected MCP client; deploy separate processes and database accounts for different trust levels.

### Drivers and connection URLs

Install only the drivers you need, or combine extras, for example `pip install -e '.[postgres,mongo]'`.

| Database | Install extra | Example URL |
|---|---|---|
| SQLite | Included | `sqlite:///demo.db` |
| PostgreSQL | `.[postgres]` | `postgresql+psycopg://reader:password@localhost:5432/app` |
| MySQL/MariaDB | `.[mysql]` | `mysql+pymysql://reader:password@localhost:3306/app?charset=utf8mb4` |
| Oracle | `.[oracle]` | `oracle+oracledb://reader:password@localhost:1521/?service_name=FREEPDB1` |
| SQL Server | `.[sqlserver]` | `mssql+pyodbc://reader:password@localhost:1433/app?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no` |
| MongoDB | `.[mongo]` | `mongodb://reader:password@localhost:27017/?authSource=app` |

Percent-encode special characters in URL usernames/passwords. The URL is configuration, never a tool argument. Oracle uses python-oracledb's default thin mode; installations requiring Oracle thick mode need an explicit initialization extension and Instant Client. SQL Server requires the native Microsoft ODBC Driver 18 separately from the Python `pyodbc` package. On Linux this also requires unixODBC and the appropriate vendor packages. TLS certificate trust must match your deployment; do not disable certificate validation for production convenience.

Windows SQLite absolute URL: `sqlite:///C:/data/reporting.db`. Linux absolute URL: `sqlite:////var/lib/dbmcp/reporting.db`. Use file-backed SQLite: in-memory SQLite databases are connection/thread-local and unsuitable for this pooled worker model.

Example environment setup:

```powershell
$env:REPORTING_DB_URL = 'postgresql+psycopg://reader:password@localhost:5432/app'
.\.venv\Scripts\dbmcp.exe --config config.local.yaml
```

`DBMCP_CONFIG` can set the default configuration path. `--config` overrides it. Configuration is loaded at startup; restart the process to change it.

## Connect an MCP client

For a client accepting `mcpServers` JSON, use absolute paths:

```json
{
  "mcpServers": {
    "database": {
      "command": "C:/MyWork/codebase/mcps/DBMcp/.venv/Scripts/python.exe",
      "args": ["-m", "dbmcp.server", "--config", "C:/MyWork/codebase/mcps/DBMcp/config.local.yaml"]
    }
  }
}
```

Client configuration formats vary. Ensure the launched process inherits the database environment variables; GUI clients do not always inherit variables set in a separate terminal. Use the client's environment configuration or your operating system's credential-aware launcher. On Linux/macOS use the absolute `.venv/bin/python` path.

For an interactive tool browser, use the MCP Inspector with Node.js installed:

```powershell
npx -y @modelcontextprotocol/inspector .\.venv\Scripts\python.exe -m dbmcp.server --config config.yaml
```

Open the Inspector URL it prints, connect, list tools and call `query_sql` with the examples below.

### Local Streamable HTTP

```powershell
.\.venv\Scripts\dbmcp.exe --config config.yaml --transport streamable-http
```

Connect a Streamable HTTP client to `http://127.0.0.1:8000/mcp`. HTTP intentionally binds to loopback and has no application authentication. Use stdio for local hosts. Remote deployment requires a separately configured authenticated TLS gateway and network controls; this repository does not implement OAuth, per-user authorization or a public multi-tenant service. Do not expose the endpoint directly to an untrusted network.

## Tool reference

`database` always means a configured alias, not an arbitrary connection string.

| Tool | Purpose / primary arguments |
|---|---|
| `list_databases` | Aliases, backend kind and write policy; no secrets |
| `health_check` | `database`; connect and ping |
| `list_schemas` | `database`; SQL schemas |
| `list_tables` | `database`, optional `schema`; tables and views |
| `describe_table` | `database`, `table`, optional `schema`; columns, PK, FK, indexes |
| `query_sql` | `database`, `sql`, optional `params`, `limit`; one SELECT |
| `explain_query` | `database`, `sql`, optional `params`; estimated plan |
| `execute_sql` | `database`, `sql`, optional `params`; one committed DML operation |
| `execute_transaction` | `database`, `statements`; atomic DML batch |
| `list_collections` | `database`; MongoDB collections |
| `describe_collection` | `database`, `collection`; MongoDB indexes |
| `find_documents` | `database`, `collection`, optional `filter`, `projection`, `limit` |
| `aggregate_documents` | `database`, `collection`, `pipeline`; bounded read pipeline |
| `write_document` | `database`, `collection`, `operation`, optional `document`, `filter` |
| `suggest_sql` | `database`, `question`, `tables`, optional `schema`; reviewable SQL draft |
| `hybrid_query` | `plan`; concurrent SQL/MongoDB reads, server-side joins and grouped aggregates |

Read the `dbmcp://catalog` resource for alias discovery. The `investigate_database` prompt accepts a `question` and guides schema inspection and parameterized querying.

### SQL query

Tool: `query_sql`

```json
{
  "database": "demo",
  "sql": "SELECT c.name, o.total FROM customers c JOIN orders o ON o.customer_id=c.id WHERE o.total > :minimum ORDER BY o.id",
  "params": {"minimum": 40},
  "limit": 20
}
```

Results contain `columns`, `rows`, and `truncated`. Rows are arrays to preserve duplicate column names. Decimal values are strings to preserve precision, dates are ISO strings, bytes are base64 objects, and MongoDB ObjectIds are strings. Input MongoDB filters currently accept JSON values only; automatic Extended JSON/ObjectId conversion is not implemented.

SQL is parsed using the configured backend dialect, but is **not translated** between databases. Use `:name` value binds for every SQL driver. Identifiers cannot be bound as values; select them from inspected metadata and use appropriate dialect quoting. Pagination belongs in SQL (`ORDER BY` plus dialect-appropriate keyset or offset pagination). The tool's `limit` only bounds returned rows; it does not rewrite or limit server-side execution work.

### SQL writes and transactions

Set `read_only: false` on an explicitly chosen alias and grant that database account only the necessary DML permissions. Client tool approval is recommended: tool annotations describe effects but are not an approval mechanism enforced by this server.

Tool: `execute_transaction`

```json
{
  "database": "writer",
  "statements": [
    {"sql": "INSERT INTO customers (id, name) VALUES (:id, :name)", "params": {"id": 3, "name": "Lin"}},
    {"sql": "INSERT INTO orders (id, customer_id, total) VALUES (:id, :customer, :total)", "params": {"id": 3, "customer": 3, "total": 25}}
  ]
}
```

Only INSERT, UPDATE and DELETE are accepted. UPDATE and DELETE require a WHERE clause; that does not prevent broad predicates such as `WHERE 1=1`. DDL, stored procedure calls, administrative commands and arbitrary scripts are intentionally excluded. A failed SQL batch rolls back, subject to the storage engine's transaction support (use InnoDB for MySQL). Successful responses report `committed` and driver row counts; some drivers return `-1` when counts are unavailable. A lost connection around commit leaves an uncertain outcome: verify database state before retrying. There is no automatic write retry or idempotency key service.

### MongoDB

Tool: `find_documents`

```json
{"database":"mongo","collection":"orders","filter":{"total":{"$gt":40}},"projection":{"_id":0,"total":1},"limit":20}
```

Tool: `aggregate_documents`

```json
{"database":"mongo","collection":"orders","pipeline":[{"$group":{"_id":"$customer_id","total":{"$sum":"$total"}}},{"$sort":{"total":-1}}]}
```

Supported stages: `$match`, `$project`, `$group`, `$sort`, `$limit`, `$skip`, `$unwind`, `$count`, `$addFields`, `$set`, `$unset`, `$replaceRoot`. Maximum 30 stages, no disk spilling, and a final output limit. `$lookup`, `$unionWith`, `$out`, `$merge`, `$where`, `$function` and `$accumulator` are excluded. There is no arbitrary MongoDB command tool.

`write_document` supports `insert_one`, `update_one` with a `$set` document, and `delete_one`. Update/delete require a nonempty filter and affect at most one matching document. These are single-document atomic operations, not a cross-document transaction API.

## Hybrid queries: SQL and NoSQL in one tool call

Call `hybrid_query` with the contents of `examples/hybrid-plan.json` in Inspector or using `session.call_tool("hybrid_query", arguments)`. The server executes the whole plan and returns calculated results; the client does not need to join data itself. No LLM or additional dependencies are used.

For example, the included plan reads order totals from the SQL alias `reporting`, reads customer regions from the MongoDB alias `mongo`, joins by `customer_id`, and returns sales totals and customer counts by region. Configure those aliases and ensure the named tables/collections exist before running it. Each customer should occur once in the customer collection for that example.

```json
{
  "plan": {
    "sources": [
      {"name":"orders","database":"reporting","operation":"query_sql",
       "sql":"SELECT customer_id, SUM(total) AS total FROM orders GROUP BY customer_id"},
      {"name":"customers","database":"mongo","operation":"find_documents",
       "collection":"customers","projection":{"_id":0,"customer_id":1,"region":1}}
    ],
    "joins":[{"source":"customers","left":"orders.customer_id",
              "right":"customers.customer_id","how":"left"}],
    "group_by":["customers.region"],
    "metrics":[{"name":"sales","operation":"sum","field":"orders.total"},
               {"name":"customer_count","operation":"count"}],
    "limit":100
  }
}
```

### Example 1: try federation locally with SQLite

This example uses two reads of the existing `demo` alias to demonstrate the same server-side joining machinery without installing MongoDB. It is a federation demonstration, not a mixed-backend test.

Run `python scripts/seed_demo.py` with the virtual environment's Python, then open Inspector using the command in [Connect an MCP client](#connect-an-mcp-client). Select **hybrid_query**. In its JSON arguments editor, paste the complete object below. If the UI displays a separate `plan` field, paste only the object inside `plan` into that field.

```json
{
  "plan": {
    "sources": [
      {
        "name": "customers",
        "database": "demo",
        "operation": "query_sql",
        "sql": "SELECT id, name FROM customers ORDER BY id"
      },
      {
        "name": "orders",
        "database": "demo",
        "operation": "query_sql",
        "sql": "SELECT customer_id, total FROM orders WHERE total >= :minimum ORDER BY id",
        "params": {"minimum": 40}
      }
    ],
    "joins": [
      {"source": "orders", "left": "customers.id", "right": "orders.customer_id", "how": "inner"}
    ],
    "group_by": ["customers.name"],
    "metrics": [
      {"name": "sales", "operation": "sum", "field": "orders.total"},
      {"name": "order_count", "operation": "count"}
    ],
    "limit": 20
  }
}
```

With the original, unmodified demo data, the structured result is:

```json
{
  "rows": [
    {"customers.name": "Ada", "sales": "42.5", "order_count": 1},
    {"customers.name": "Grace", "sales": "80", "order_count": 1}
  ],
  "truncated": false,
  "result_rows": 2,
  "joined_rows": 2,
  "sources": [
    {"name": "customers", "database": "demo", "operation": "query_sql", "rows": 2},
    {"name": "orders", "database": "demo", "operation": "query_sql", "rows": 2}
  ],
  "consistent_snapshot": false
}
```

`sales` is a decimal string; `order_count` is an integer. The server computes both. The MCP protocol wraps this object in `structuredContent` and also provides text content for compatible clients.

### Example 2: SQL sales joined to MongoDB customer regions

This example uses the existing SQLite orders as its SQL source and a real MongoDB collection. Run commands from the repository root. MongoDB must be available through the included Compose service:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[mongo]'
.\.venv\Scripts\python.exe scripts/seed_demo.py
docker compose up -d --wait mongo
```

Save this configuration as `config.local.yaml` (merge these aliases if that file already contains your settings):

```yaml
databases:
  reporting:
    kind: sql
    url: sqlite:///demo.db
    read_only: true
  mongo:
    kind: mongodb
    url: mongodb://dbmcp:local-dev-only@127.0.0.1:27017/?authSource=admin
    database: dbmcp_hybrid_demo
    read_only: true
limits:
  max_rows: 500
  max_hybrid_rows: 10000
llm:
  enabled: false
```

Seed the dedicated demo database through the MongoDB shell, outside the read-only MCP server:

```powershell
docker compose exec mongo mongosh --username dbmcp --password local-dev-only --authenticationDatabase admin dbmcp_hybrid_demo
```

At the `mongosh` prompt, paste:

```javascript
db.customers.updateOne(
  {_id: 1},
  {$set: {customer_id: 1, region: "West"}},
  {upsert: true}
);
db.customers.updateOne(
  {_id: 2},
  {$set: {customer_id: 2, region: "East"}},
  {upsert: true}
);
db.customers.find({}, {_id: 0, customer_id: 1, region: 1});
exit
```

These commands insert or update two demo documents without deleting the collection. Customer IDs are numeric in both databases. The source data is now:

| SQL customer_id | SQL order total | MongoDB region |
|---|---|---|
| 1 | 42.50 | West |
| 2 | 80 | East |

Start Inspector against this configuration:

```powershell
npx -y @modelcontextprotocol/inspector .\.venv\Scripts\python.exe -m dbmcp.server --config config.local.yaml
```

Call `hybrid_query` with [examples/hybrid-plan.json](examples/hybrid-plan.json), also shown at the start of this section. Expected `rows`, ignoring order and insignificant trailing decimal zeros:

```json
[
  {"customers.region": "West", "sales": "42.5", "customer_count": 1},
  {"customers.region": "East", "sales": "80", "customer_count": 1}
]
```

The plan aggregates SQL orders by customer before joining. `customer_count` therefore counts customers with orders in each region, provided MongoDB contains one matching document per customer. If no MongoDB document matches, a left join places that customer's sales in the `null` region group. An inner join would exclude them. Additional demo orders or customer documents can change the results.

To use PostgreSQL, Oracle, or SQL Server instead, change only the `reporting` connection configuration and install its driver extra. Provide an `orders` table with the same columns. Use SQL column aliases to ensure the returned field names match the plan; for example, Oracle queries may need `AS "customer_id"` and `AS "total"` to preserve lowercase names. The SQL must be valid for the selected backend.

### Example 3: join a MongoDB aggregation to SQL customer names

Keep Example 2's configuration. In its MongoDB shell, seed three tickets:

```javascript
db.tickets.updateOne({_id: 101}, {$set: {customer_id: 1, status: "open"}}, {upsert: true});
db.tickets.updateOne({_id: 102}, {$set: {customer_id: 1, status: "open"}}, {upsert: true});
db.tickets.updateOne({_id: 103}, {$set: {customer_id: 2, status: "closed"}}, {upsert: true});
```

Call `hybrid_query` with:

```json
{
  "plan": {
    "sources": [
      {
        "name": "customers",
        "database": "reporting",
        "operation": "query_sql",
        "sql": "SELECT id, name FROM customers ORDER BY id"
      },
      {
        "name": "tickets",
        "database": "mongo",
        "operation": "aggregate_documents",
        "collection": "tickets",
        "pipeline": [
          {"$match": {"status": "open"}},
          {"$group": {"_id": "$customer_id", "open_count": {"$sum": 1}}},
          {"$project": {"_id": 0, "customer_id": "$_id", "open_count": 1}}
        ]
      }
    ],
    "joins": [
      {"source": "tickets", "left": "customers.id", "right": "tickets.customer_id", "how": "left"}
    ],
    "limit": 20
  }
}
```

With only the sample data present, expected `rows` are:

```json
[
  {"customers": {"id": 1, "name": "Ada"}, "tickets": {"customer_id": 1, "open_count": 2}},
  {"customers": {"id": 2, "name": "Grace"}, "tickets": null}
]
```

There are no final `metrics`, so the result contains the joined source objects. `tickets: null` means there was no open-ticket aggregate for Grace; it is not automatically replaced with a zero-valued object. MongoDB does the ticket grouping, and the MCP server joins that result with SQL.

### Call any example from a Python MCP client

Save the complete argument object for your chosen example to a JSON file. Example 2 is already available as `examples/hybrid-plan.json`. Save the following client as `examples/run_hybrid.py`:

```python
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--plan", required=True)
    args = parser.parse_args()
    arguments = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dbmcp.server", "--config", str(Path(args.config).resolve())],
        env=dict(os.environ),
    )
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("hybrid_query", arguments)
        if result.isError:
            raise RuntimeError(result.content)
        print(json.dumps(result.structuredContent, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
```

Run Example 2:

```powershell
.\.venv\Scripts\python.exe examples/run_hybrid.py --config config.local.yaml --plan examples/hybrid-plan.json
```

For Example 1, save its JSON as `examples/local-plan.json` and run:

```powershell
.\.venv\Scripts\python.exe examples/run_hybrid.py --config config.yaml --plan examples/local-plan.json
```

On Linux/macOS use `.venv/bin/python` in place of `.\.venv\Scripts\python.exe`. The client starts and closes its own MCP subprocess; no separately running MCP server or model is required. Docker/MongoDB must remain running for Examples 2 and 3.

### Common hybrid errors and corrections

| Error or unexpected result | Cause | Correction |
|---|---|---|
| `Source ... is truncated` | A source has more than `max_rows` results | Filter or aggregate at the source; raise the configured limit only when appropriate. Final output `limit` does not change source limits. |
| `Hybrid join exceeds max_hybrid_rows` | Too many matching combinations | Deduplicate or pre-aggregate by join key; check for many-to-many matches. |
| `Missing field` | Field absent, incorrectly cased, or removed by projection | Inspect query output and explicitly project/alias every referenced field. |
| No matches for apparently equal IDs | For example, numeric `1` versus string `"1"` | Normalize the source data or query types. The join does not coerce strings to numbers. |
| Sales unexpectedly multiplied | Multiple customer documents match one sales row | Ensure the right-side key is unique or aggregate to the intended grain first. |
| `Hybrid output limit exceeds max_rows` | Plan `limit` exceeds the configured maximum | Lower the plan's limit. With `max_rows` below 100, override the default plan limit too. |
| `truncated: true` in a successful response | Final joined rows or groups exceed the output limit | Aggregation used all fetched inputs; raise output `limit` within `max_rows` to return more groups. |

### Plan semantics and limits

- Supply 1–8 named sources. Supported operations are `query_sql` (`sql`, optional `params`), `find_documents` (`collection`, optional `filter` and `projection`), and `aggregate_documents` (`collection`, optional `pipeline`). Sources may use different aliases or reuse one alias. Existing adapter policies apply; hybrid writes are not supported.
- Independent reads run concurrently under the server's existing worker limit. Start with the first source; each join introduces one unused source. Every source must be joined. Inner and left equality joins are supported; no Cartesian joins or arbitrary expressions.
- Field references use `source.field`, including nested MongoDB paths such as `customers.address.region`. Give SQL columns unique aliases without dots. Missing fields produce an error rather than silently being treated as zero. Explicit null values are allowed; null keys never join. String `"1"` does not join numeric `1`; normalize types in source queries when needed.
- Omit `metrics` and `group_by` to return joined rows as nested source objects. Otherwise, `group_by` defines groups and metrics support `count`, `sum`, `avg`, `min`, `max`. Count without a field counts rows; count with a field ignores nulls. Numeric metrics ignore nulls and accept numbers or decimal strings; their results are decimal strings. Empty numeric aggregates return null. An empty global count is zero. Decimal arithmetic uses Python's default 28-digit precision, so averages and very large totals can round.
- Joins preserve duplicate matches. One-to-many joins can repeat amounts and inflate totals; pre-aggregate or deduplicate the sources to the intended business grain before joining.
- Source results must be complete within `max_rows`. Any truncated source or failed source fails the entire request. This avoids presenting partial aggregates as complete. Explicit filters/LIMIT clauses in source queries still define the selected dataset; the server cannot infer omitted business data.
- Configure `limits.max_hybrid_rows` (default 10,000) to cap total fetched rows and each join's intermediate rows. Join expansion beyond this cap fails. Each source and the final response also respect `max_response_bytes`; this is not a strict process memory cap.
- The final `limit` defaults to 100 and must not exceed `max_rows`. It is applied **after** aggregation. `result_rows`, `joined_rows`, source row counts and `truncated` explain the output. There is no final sort option; sort source queries when applicable, or sort the returned aggregates in the client.
- Reads do not share a distributed snapshot: `consistent_snapshot` is always false. Data can change between source reads. This is a bounded federation tool, not a distributed transaction engine.

The caller supplies the structured plan, normally after inspecting source schemas. Natural-language plan generation and prose answers are still the MCP host's responsibility; the server performs the actual retrieval, joins and calculations. Default tests exercise real SQLite plus a mocked MongoDB driver, and a real MCP subprocess with two SQL sources. Live mixed-backend execution requires your configured databases.

## Optional LLM assistance

The MCP host can use its own model without enabling the server's LLM integration. For server-side SQL suggestions, configure an endpoint implementing the common `/v1/chat/completions` request/response format. It can be a local model service or a self-hosted or remote compatible provider; no vendor SDK or cloud dependency is required.

```yaml
llm:
  enabled: true
  base_url: http://127.0.0.1:11434/v1
  model: qwen2.5-coder:7b
  api_key: ""
  timeout_seconds: 60
```

Run your model service and load that model separately. Use `${LLM_API_KEY}` if the endpoint requires credentials. `base_url` must include the API prefix (typically `/v1`), not `/chat/completions`.

```json
{"database":"demo","question":"Which customers have orders above a supplied minimum?","tables":["customers","orders"]}
```

`suggest_sql` sends the question and metadata for 1–10 selected tables to that endpoint. It does not fetch or send table rows or database credentials. Metadata can include sensitive names, comments or defaults; enable this only for an approved endpoint. The response is parsed as a SELECT and returned with `executed: false` and `review_required: true`. Review semantics and supply bind values yourself. The model is neither a SQL correctness oracle nor an authorization layer. Disabled LLM mode makes no model network requests.

## Safety, efficiency and operational limits

- Read-only is the default. Use real read-only database credentials with table/view grants, restricted routine execution privileges and row-level policies where needed. SQL parsing is a guardrail, **not a security sandbox**: SELECT can invoke vendor functions with effects. The process exposes every object its database account can access; there is no schema/table allowlist.
- SQLite uses `query_only`; PostgreSQL and MySQL/MariaDB use read-only transactions for read-only aliases. Oracle and SQL Server require read-only database grants. Read tools on a write-enabled alias still share that alias's more powerful credentials.
- PostgreSQL gets a statement timeout; SQLite gets a progress deadline; Oracle gets a driver call timeout; SQL Server gets a driver query timeout; MongoDB gets operation and server-side query limits. MySQL/MariaDB should use driver socket timeouts in `connect_args` and server resource policies. These mechanisms differ: there is no universal hard end-to-end cancellation guarantee, and connect/pool/metadata time may differ from query execution time.
- Concurrency is bounded globally and SQL connections are pooled per alias. Worker cancellation does not abandon a running driver thread and release its concurrency slot prematurely. Long-running work can still occupy capacity until the driver/database stops it.
- At most `max_rows + 1` rows are fetched to detect truncation. `max_response_bytes` rejects oversized serialized responses, but is checked after fetching: it is not a strict peak-memory cap for huge BLOBs/documents. Select needed columns, avoid large binary data, and configure database workload limits. Metadata lists are also subject to the response byte cap.
- Audit logs contain alias, operation, duration and status. SQL, parameters, results, credentials and raw driver errors are excluded. Client errors are intentionally generic for driver failures. Diagnose details through protected database-side logs. Treat all database contents as untrusted, including instructions embedded in text fields.
- Explain supports SQLite, PostgreSQL, MySQL/MariaDB and never requests `ANALYZE`. Oracle and SQL Server explain workflows are intentionally unsupported because they need different session/plan handling.

## Tests and real-environment verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
```

The default suite uses real SQLite connections and an actual stdio MCP subprocess. It checks initialization, discovery, tool calls, query parameter binding, truncation, transaction rollback, write restrictions, read-only enforcement, parser guards, configuration, response caps and error redaction. External database tests skip unless explicitly configured.

### Local PostgreSQL and MongoDB integration

With Docker Compose installed:

```powershell
docker compose up -d --wait
.\.venv\Scripts\python.exe -m pip install -e '.[dev,postgres,mongo]'
$env:DBMCP_LIVE_CONFIG = 'config.compose.yaml'
.\.venv\Scripts\python.exe -m pytest tests/test_live.py -q
.\.venv\Scripts\python.exe examples/client.py --config config.compose.yaml --database postgres
.\.venv\Scripts\python.exe examples/client.py --config config.compose.yaml --database mongo
docker compose down
```

The Compose credentials are development-only and privileged. Create restricted users before treating this as a production configuration. The included live checks perform connectivity, metadata discovery and a SQL constant SELECT; they do not insert data or validate every backend capability.

### Oracle, SQL Server and other existing databases

1. Provision a nonproduction schema/database and a least-privilege test account.
2. Install the corresponding Python extra and any native driver prerequisites.
3. Create `config.local.yaml` with the correct URL, database/service name, TLS and connection arguments.
4. Set `DBMCP_LIVE_CONFIG=config.local.yaml` and run `tests/test_live.py`.
5. Run `examples/client.py --config config.local.yaml --database <alias>` to verify the actual MCP transport.
6. Use Inspector to inspect a known table, execute a parameterized SELECT and check a limited result. Test explain only on supported dialects.
7. For write verification, use a separate write-enabled alias and disposable transactional table. Test a successful two-statement batch and a batch whose second statement violates a constraint, then verify that the first statement rolled back. Do not perform write tests against production records.

On Linux/macOS, set `export DBMCP_LIVE_CONFIG=config.local.yaml` and use `.venv/bin/python`. External servers and a model endpoint are not bundled or automatically contacted during default tests. Compatibility for a specific vendor version, auth method and network environment must be verified there; adapter support is not a claim that every vendor was exercised locally.

## Project layout and extension

```text
src/dbmcp/config.py   Strict settings, secrets and environment expansion
src/dbmcp/policy.py   SQL AST and MongoDB operation guards
src/dbmcp/sql.py      SQLAlchemy pooling, metadata, queries and transactions
src/dbmcp/mongo.py    PyMongo discovery, read pipelines and single writes
src/dbmcp/hybrid.py   Read-only federation plans, joins and aggregate calculations
src/dbmcp/server.py   MCP tools, resource, prompt, auditing and optional LLM
examples/client.py   Real MCP client example
scripts/seed_demo.py  Repeatable local SQLite demo
tests/               Local, protocol and opt-in live checks
```

To add a SQLAlchemy-supported backend, install its dialect/driver, add its SQLGlot dialect mapping, implement appropriate timeout/read-only handling, and run live metadata/query/rollback tests. Another NoSQL backend should get an adapter with explicit operations and policy checks, a configuration kind, and corresponding typed MCP tools. Do not map arbitrary client-provided commands directly into a driver.

For deployment, install the package into an isolated environment and launch `dbmcp` under your process manager with controlled environment variables and database permissions. Resolve and pin dependencies in your deployment pipeline; the project specifies compatibility ranges, not a universal cross-platform lockfile.

## Troubleshooting

| Symptom | Check |
|---|---|
| Configuration cannot be loaded | File path, YAML fields, all referenced environment variables |
| SQLite table missing | Absolute database file path and server working directory |
| Driver import error | Correct optional extra installed in the server's Python environment |
| SQL Server cannot open driver | Native ODBC driver installed; exact driver name matches URL |
| Oracle connection fails | Listener, service name, supported thin authentication and network settings |
| Database operation failed | DB grants, bind values, SQL dialect, connectivity, protected DB logs |
| Writes disabled | Correct alias, explicit `read_only: false`, appropriate database grants |
| Response exceeds byte limit | Fewer selected columns/rows; exclude large documents/BLOBs |
| LLM invalid response | Compatible endpoint, available model, SQL-only output without code fences |
| Server appears idle | stdio server is awaiting an MCP client |

## License

This project is licensed under the [MIT License](LICENSE).

Copyright (c) 2026 Rajarshi Ray <rajarshir@gmail.com>.

Third-party dependencies, database servers, drivers, and optional models retain their own licenses.

## Primary references

- [Official MCP Python SDK 1.x](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)
- [SQLAlchemy database dialects](https://docs.sqlalchemy.org/en/20/dialects/)
- [SQLAlchemy engine configuration](https://docs.sqlalchemy.org/en/20/core/engines.html)
- [PyMongo query documentation](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/crud/query/find/)
