"""Pooled SQL access with bounded fetches and atomic DML batches."""

import time
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text

from .config import Database, Limits
from .policy import PolicyError, validate_sql


class SQLAdapter:
    def __init__(self, config: Database, limits: Limits):
        self.config, self.limits = config, limits
        options = {"pool_pre_ping": True, "connect_args": config.connect_args}
        if not config.url.get_secret_value().startswith("sqlite"):
            options.update(pool_size=config.pool_size, max_overflow=0, pool_timeout=10)
        self.engine = create_engine(config.url.get_secret_value(), **options)
        self.dialect = self.engine.dialect.name
        if self.dialect == "sqlite":

            @event.listens_for(self.engine, "connect")
            def configure(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                if config.read_only:
                    connection.execute("PRAGMA query_only=ON")

    def close(self):
        self.engine.dispose()

    @contextmanager
    def connection(self):
        with self.engine.connect() as conn:
            raw = conn.connection.driver_connection
            deadline = time.monotonic() + self.limits.timeout_seconds
            if self.dialect == "sqlite":
                raw.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            elif self.dialect == "postgresql":
                conn.exec_driver_sql(
                    f"SET LOCAL statement_timeout = {self.limits.timeout_seconds * 1000}"
                )
                if self.config.read_only:
                    conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            elif self.dialect == "oracle":
                raw.call_timeout = self.limits.timeout_seconds * 1000
            elif self.dialect == "mssql":
                raw.timeout = self.limits.timeout_seconds
            elif self.dialect in {"mysql", "mariadb"} and self.config.read_only:
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            try:
                yield conn
            finally:
                if self.dialect == "sqlite":
                    raw.set_progress_handler(None, 0)

    def check(self, sql, write=False):
        if len(sql) > self.limits.max_sql_chars:
            raise PolicyError("SQL exceeds configured length limit")
        if write and self.config.read_only:
            raise PolicyError("Writes are disabled for this database")
        validate_sql(sql, self.dialect, write)

    def health(self):
        with self.connection() as conn:
            conn.exec_driver_sql("SELECT 1 FROM DUAL" if self.dialect == "oracle" else "SELECT 1")
        return {"healthy": True, "dialect": self.dialect}

    def schemas(self):
        with self.connection() as conn:
            return inspect(conn).get_schema_names()

    def tables(self, schema=None):
        with self.connection() as conn:
            inspector = inspect(conn)
            return {
                "tables": inspector.get_table_names(schema),
                "views": inspector.get_view_names(schema),
            }

    def describe(self, table, schema=None):
        with self.connection() as conn:
            inspector = inspect(conn)
            return {
                "name": table,
                "schema": schema,
                "columns": [
                    {**column, "type": str(column["type"])}
                    for column in inspector.get_columns(table, schema)
                ],
                "primary_key": inspector.get_pk_constraint(table, schema),
                "foreign_keys": inspector.get_foreign_keys(table, schema),
                "indexes": inspector.get_indexes(table, schema),
            }

    def query(self, sql, params=None, limit=None, explain=False):
        self.check(sql)
        limit = self.limits.max_rows if limit is None else limit
        if not 1 <= limit <= self.limits.max_rows:
            raise PolicyError("Requested limit is outside configured bounds")
        if explain:
            prefixes = {
                "sqlite": "EXPLAIN QUERY PLAN ",
                "postgresql": "EXPLAIN (FORMAT JSON) ",
                "mysql": "EXPLAIN ",
                "mariadb": "EXPLAIN ",
            }
            if self.dialect not in prefixes:
                raise PolicyError("Explain is supported for SQLite, PostgreSQL, MySQL/MariaDB")
            sql = prefixes[self.dialect] + sql
        with (
            self.connection() as conn,
            conn.execution_options(stream_results=not explain).execute(
                text(sql), params or {}
            ) as result,
        ):
            columns = list(result.keys())
            rows = [list(row) for row in result.fetchmany(limit + 1)]
        return {"columns": columns, "rows": rows[:limit], "truncated": len(rows) > limit}

    def execute(self, statements):
        if not 1 <= len(statements) <= self.limits.max_batch_statements:
            raise PolicyError("Invalid transaction batch size")
        for statement in statements:
            if set(statement) - {"sql", "params"} or not isinstance(statement.get("sql"), str):
                raise PolicyError("Each statement requires sql and optional params")
            self.check(statement["sql"], write=True)
        with self.connection() as conn:
            counts = []
            for statement in statements:
                with conn.execute(text(statement["sql"]), statement.get("params") or {}) as result:
                    counts.append(result.rowcount)
            conn.commit()
        return {"committed": True, "affected_rows": counts}
