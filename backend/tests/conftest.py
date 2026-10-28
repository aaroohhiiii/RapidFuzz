from __future__ import annotations

import os

from sqlalchemy import BigInteger
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


# Importing app.db must never point tests at a developer or production database.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"


@compiles(BigInteger, "sqlite")
def compile_big_integer_sqlite(_type, _compiler, **_kwargs):
    return "INTEGER"


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"
