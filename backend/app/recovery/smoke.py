from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_CRITICAL_TABLES = (
    "roles",
    "signals",
    "safety_events",
    "authentication_audit_events",
)


class RepositorySmokeError(Exception):
    """A read-only repository schema or representative-data check failed."""


@dataclass(frozen=True, slots=True)
class TableSmokeResult:
    table: str
    bounded_count: int
    truncated: bool
    fingerprint_sha256: str


@dataclass(frozen=True, slots=True)
class RepositorySmokeResult:
    revision: str
    tables: tuple[TableSmokeResult, ...]


class ReadOnlyRepositorySmokeChecker:
    """Run bounded SQL-only checks without importing application repositories."""

    def __init__(
        self,
        critical_tables: tuple[str, ...] = DEFAULT_CRITICAL_TABLES,
        *,
        row_limit: int = 128,
    ) -> None:
        if row_limit <= 0 or row_limit > 10_000:
            raise ValueError("smoke row limit must be between 1 and 10000")
        if not critical_tables or any(
            _IDENTIFIER.fullmatch(table) is None for table in critical_tables
        ):
            raise ValueError("critical tables must be a non-empty identifier allowlist")
        if len(set(critical_tables)) != len(critical_tables):
            raise ValueError("critical table allowlist contains duplicates")
        self.critical_tables = critical_tables
        self.row_limit = row_limit

    def check(self, database: Path, *, expected_revision: str) -> RepositorySmokeResult:
        if not expected_revision:
            raise ValueError("expected revision is required")
        if not database.is_file():
            raise RepositorySmokeError("candidate database is unavailable")
        uri = f"file:{database.resolve().as_posix()}?mode=ro&immutable=1"
        try:
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                connection.execute("PRAGMA query_only=ON")
                revisions = connection.execute(
                    "SELECT version_num FROM alembic_version LIMIT 2"
                ).fetchall()
                if revisions != [(expected_revision,)]:
                    raise RepositorySmokeError("revision row is invalid")
                available = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                missing = set(self.critical_tables) - available
                if missing:
                    raise RepositorySmokeError("critical table is missing")
                foreign_key_failure = connection.execute(
                    "PRAGMA foreign_key_check"
                ).fetchone()
                if foreign_key_failure is not None:
                    raise RepositorySmokeError("foreign key check failed")
                tables = tuple(
                    self._inspect_table(connection, table)
                    for table in self.critical_tables
                )
        except RepositorySmokeError:
            raise
        except sqlite3.DatabaseError as error:
            raise RepositorySmokeError("repository query failed") from error
        return RepositorySmokeResult(expected_revision, tables)

    def _inspect_table(
        self, connection: sqlite3.Connection, table: str
    ) -> TableSmokeResult:
        quoted = _quote_identifier(table)
        columns = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
        if not columns:
            raise RepositorySmokeError("critical table schema is unavailable")
        names = [str(column[1]) for column in columns]
        primary_keys = [
            str(column[1])
            for column in sorted(columns, key=lambda item: int(item[5]))
            if int(column[5]) > 0
        ]
        order_columns = primary_keys or names
        order_clause = ",".join(_quote_identifier(name) for name in order_columns)
        rows = connection.execute(
            f"SELECT * FROM {quoted} ORDER BY {order_clause} LIMIT ?",
            (self.row_limit + 1,),
        ).fetchall()
        truncated = len(rows) > self.row_limit
        bounded = rows[: self.row_limit]
        digest = hashlib.sha256()
        for row in bounded:
            encoded = json.dumps(
                [_canonical_value(value) for value in row],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        return TableSmokeResult(table, len(bounded), truncated, digest.hexdigest())


def _quote_identifier(value: str) -> str:
    if _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("identifier is not allowlisted")
    return f'"{value}"'


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    return {"type": type(value).__name__, "value": str(value)}
