"""Bounded, read-only federation. Plans contain data, never executable Python."""

import asyncio
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import anyio
from pydantic import Field, model_validator

from .config import StrictModel
from .policy import PolicyError


class Source(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    database: str
    operation: Literal["query_sql", "find_documents", "aggregate_documents"]
    sql: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    collection: str | None = None
    filter: dict[str, Any] = Field(default_factory=dict)
    projection: dict[str, Any] | None = None
    pipeline: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_operation(self):
        if self.operation == "query_sql":
            if not self.sql or self.collection or self.filter or self.projection or self.pipeline:
                raise ValueError("SQL sources require sql and cannot contain MongoDB arguments")
        elif not self.collection or self.sql or self.params:
            raise ValueError("MongoDB sources require collection and cannot contain SQL arguments")
        if self.operation == "find_documents" and self.pipeline:
            raise ValueError("Find sources cannot contain a pipeline")
        if self.operation == "aggregate_documents" and (self.filter or self.projection):
            raise ValueError("Aggregation sources use pipeline, not filter/projection")
        return self


class Join(StrictModel):
    source: str
    left: str
    right: str
    how: Literal["inner", "left"] = "inner"


class Metric(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    operation: Literal["count", "sum", "avg", "min", "max"]
    field: str | None = None

    @model_validator(mode="after")
    def required_field(self):
        if self.operation != "count" and not self.field:
            raise ValueError("This metric requires a field")
        return self


class HybridPlan(StrictModel):
    sources: list[Source] = Field(min_length=1, max_length=8)
    joins: list[Join] = Field(default_factory=list, max_length=7)
    group_by: list[str] = Field(default_factory=list, max_length=16)
    metrics: list[Metric] = Field(default_factory=list, max_length=16)
    limit: int = Field(default=100, ge=1)

    @model_validator(mode="after")
    def valid_plan(self):
        names = [source.name for source in self.sources]
        if len(set(names)) != len(names):
            raise ValueError("Source names must be unique")
        used = {names[0]}
        for join in self.joins:
            if join.source not in names or join.source in used:
                raise ValueError("Each join must introduce one unused source")
            if join.left.split(".")[0] not in used or not join.left.partition(".")[2]:
                raise ValueError("Left key must reference an already joined source.field")
            if not join.right.startswith(join.source + ".") or not join.right.partition(".")[2]:
                raise ValueError("Right key must reference the new source.field")
            used.add(join.source)
        if used != set(names):
            raise ValueError("Every source must be connected by a join")
        if self.group_by and not self.metrics:
            raise ValueError("group_by requires metrics")
        outputs = [metric.name for metric in self.metrics]
        if len(set(outputs)) != len(outputs) or len(set(self.group_by)) != len(self.group_by):
            raise ValueError("Metric names and group fields must be unique")
        for field in self.group_by + [m.field for m in self.metrics if m.field]:
            if field.split(".")[0] not in used or not field.partition(".")[2]:
                raise ValueError("Fields must use source.field notation")
        return self


def value_at(row, field):
    source, _, path = field.partition(".")
    value = row[source]
    for part in path.split("."):
        if value is None:
            return None
        if not isinstance(value, dict) or part not in value:
            raise PolicyError(f"Missing field: {field}; project or alias it explicitly")
        value = value[part]
    return value


def key(value):
    if isinstance(value, (dict, list)):
        raise PolicyError("Join and group keys must be scalar values")
    # Keep strings distinct from numbers, and booleans distinct from integers.
    return ("number" if type(value) in (int, float) else type(value).__name__, value)


def numeric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise PolicyError("Numeric metrics require numbers or decimal strings")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise PolicyError("Numeric metric contains a nonnumeric value") from None
    if not number.is_finite():
        raise PolicyError("Numeric metrics require finite values")
    return number


def combine(plan, datasets, limits):
    rows = [{plan.sources[0].name: item} for item in datasets[plan.sources[0].name]]
    for join in plan.joins:
        index = {}
        for item in datasets[join.source]:
            value = value_at({join.source: item}, join.right)
            if value is not None:
                index.setdefault(key(value), []).append(item)
        output = []
        for row in rows:
            value = value_at(row, join.left)
            matches = index.get(key(value), []) if value is not None else []
            if not matches and join.how == "left":
                matches = [None]
            for match in matches:
                if len(output) >= limits.max_hybrid_rows:
                    raise PolicyError("Hybrid join exceeds max_hybrid_rows; reduce source results")
                output.append({**row, join.source: match})
        rows = output
    joined_count = len(rows)
    if plan.metrics:
        groups = {}
        if not plan.group_by:
            groups[()] = ([], [])
        for row in rows:
            values = [value_at(row, field) for field in plan.group_by]
            group_key = tuple(key(value) for value in values)
            groups.setdefault(group_key, (values, []))[1].append(row)
        output = []
        for values, members in groups.values():
            result = dict(zip(plan.group_by, values, strict=True))
            for metric in plan.metrics:
                values_for_metric = (
                    [value_at(row, metric.field) for row in members]
                    if metric.field
                    else [1] * len(members)
                )
                present = [value for value in values_for_metric if value is not None]
                if metric.operation == "count":
                    result[metric.name] = len(present)
                elif not present:
                    result[metric.name] = None
                else:
                    numbers = [numeric(value) for value in present]
                    if metric.operation == "sum":
                        answer = sum(numbers, Decimal(0))
                    elif metric.operation == "avg":
                        answer = sum(numbers, Decimal(0)) / len(numbers)
                    elif metric.operation == "min":
                        answer = min(numbers)
                    else:
                        answer = max(numbers)
                    result[metric.name] = str(answer)
            output.append(result)
        rows = output
    result = {
        "rows": rows[: plan.limit],
        "truncated": len(rows) > plan.limit,
        "result_rows": len(rows),
        "joined_rows": joined_count,
        "sources": [
            {
                "name": source.name,
                "database": source.database,
                "operation": source.operation,
                "rows": len(datasets[source.name]),
            }
            for source in plan.sources
        ],
        "consistent_snapshot": False,
    }
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > limits.max_response_bytes:
        raise PolicyError("Hybrid response exceeds byte limit; reduce output limit or projections")
    return result


async def execute_hybrid(service, plan: HybridPlan):
    limits = service.settings.limits
    if plan.limit > limits.max_rows:
        raise PolicyError("Hybrid output limit exceeds max_rows")
    # Validate aliases and backend types before starting any source.
    for source in plan.sources:
        config = service.settings.databases.get(source.database)
        kind = "sql" if source.operation == "query_sql" else "mongodb"
        if config is None or config.kind != kind:
            raise PolicyError("Hybrid source has an unknown alias or incompatible backend")

    async def fetch(source):
        if source.operation == "query_sql":
            result = await service.call(source.database, "sql", "query", source.sql, source.params)
            if len(set(result["columns"])) != len(result["columns"]):
                raise PolicyError("Hybrid SQL columns must have unique aliases")
            documents = [dict(zip(result["columns"], row, strict=True)) for row in result["rows"]]
        elif source.operation == "find_documents":
            result = await service.call(
                source.database,
                "mongodb",
                "find",
                source.collection,
                source.filter,
                source.projection,
            )
            documents = result["documents"]
        else:
            result = await service.call(
                source.database, "mongodb", "aggregate", source.collection, source.pipeline
            )
            documents = result["documents"]
        if result["truncated"]:
            raise PolicyError(f"Source {source.name} is truncated; narrow or aggregate its query")
        return documents

    # Settle all reads before returning an error; no abandoned database tasks.
    results = await asyncio.gather(
        *(fetch(source) for source in plan.sources), return_exceptions=True
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    if sum(len(result) for result in results) > limits.max_hybrid_rows:
        raise PolicyError("Hybrid inputs exceed max_hybrid_rows")
    datasets = dict(zip((source.name for source in plan.sources), results, strict=True))
    return await anyio.to_thread.run_sync(combine, plan, datasets, limits, limiter=service.limiter)
