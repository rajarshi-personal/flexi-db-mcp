"""Conservative syntax checks; database grants remain the security boundary."""

import sqlglot
from sqlglot import exp


class PolicyError(ValueError):
    pass


DIALECTS = {
    "postgresql": "postgres",
    "mssql": "tsql",
    "sqlite": "sqlite",
    "oracle": "oracle",
    "mysql": "mysql",
    "mariadb": "mysql",
}


def validate_sql(sql: str, dialect: str, write: bool = False) -> None:
    try:
        statements = sqlglot.parse(sql, read=DIALECTS.get(dialect, dialect))
    except sqlglot.errors.SqlglotError:
        raise PolicyError("SQL could not be parsed for the configured dialect") from None
    if len(statements) != 1 or statements[0] is None:
        raise PolicyError("Exactly one SQL statement is required")
    root = statements[0]
    if write:
        if not isinstance(root, (exp.Insert, exp.Update, exp.Delete)):
            raise PolicyError("Write tools accept only INSERT, UPDATE, DELETE")
        if isinstance(root, (exp.Update, exp.Delete)) and not root.args.get("where"):
            raise PolicyError("UPDATE and DELETE require WHERE")
    elif not isinstance(root, exp.Query):
        raise PolicyError("Read tools accept SELECT queries only")
    forbidden = (exp.DDL, exp.Command, exp.Into, exp.Lock)
    for node in root.walk():
        # SQLGlot classifies INSERT as both DML and DDL. Only the approved root
        # write is exempt; nested writes and administrative nodes remain denied.
        if write and node is root:
            continue
        if isinstance(node, forbidden) or (not write and isinstance(node, exp.DML)):
            raise PolicyError("Statement contains a disallowed operation")


def validate_mongo(value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"$where", "$function", "$accumulator", "$out", "$merge"}:
                raise PolicyError("Server-side code and aggregation writes are disabled")
            validate_mongo(item)
    elif isinstance(value, list):
        for item in value:
            validate_mongo(item)
