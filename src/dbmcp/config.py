"""Strict configuration with explicit environment variable expansion."""

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Database(StrictModel):
    kind: Literal["sql", "mongodb"] = "sql"
    url: SecretStr
    database: str | None = None
    read_only: bool = True
    pool_size: int = Field(default=5, ge=1, le=50)
    connect_args: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def mongo_database(self):
        if self.kind == "mongodb" and not self.database:
            raise ValueError("MongoDB requires an explicit database name")
        return self


class Limits(StrictModel):
    max_rows: int = Field(default=500, ge=1, le=10000)
    max_response_bytes: int = Field(default=1000000, ge=1024, le=10000000)
    max_sql_chars: int = Field(default=50000, ge=100, le=1000000)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_concurrency: int = Field(default=8, ge=1, le=64)
    max_batch_statements: int = Field(default=20, ge=1, le=100)
    max_hybrid_rows: int = Field(default=10000, ge=1, le=100000)


class LLM(StrictModel):
    enabled: bool = False
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "qwen2.5-coder:7b"
    api_key: SecretStr = SecretStr("")
    timeout_seconds: int = Field(default=60, ge=1, le=300)


class Settings(StrictModel):
    databases: dict[str, Database] = Field(min_length=1)
    limits: Limits = Field(default_factory=Limits)
    llm: LLM = Field(default_factory=LLM)


def load_settings(path: str | Path) -> Settings:
    def expand(value):
        if isinstance(value, str):

            def replace(match):
                name = match.group(1)
                if name not in os.environ:
                    raise ValueError(f"Missing environment variable: {name}")
                return os.environ[name]

            return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)
        if isinstance(value, dict):
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    return Settings.model_validate(expand(yaml.safe_load(Path(path).read_text("utf-8"))))
