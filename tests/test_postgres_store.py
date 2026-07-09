"""Tests for PostgresRuntimeStore.

The store's SQL is exercised in CI against an in-memory-compatible SQLite
connection that translates the Postgres dialect (``%s`` params, ``BIGSERIAL``,
``DOUBLE PRECISION``) so the full RuntimeStore contract runs without a server.
A real-server round trip runs only when ``LEOS_TEST_POSTGRES_DSN`` is set.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from leos_agent.postgres_store import PostgresRuntimeStore
from leos_agent.runtime_store import RuntimeStoreError
from tests.store_contract import RuntimeStoreContract


def _translate(sql: str) -> str:
    return (
        sql.replace("%s", "?")
        .replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
        .replace("DOUBLE PRECISION", "REAL")
    )


class _PgLikeCursor:
    """Cursor adapter translating Postgres SQL to SQLite for tests."""

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> _PgLikeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        self._cursor.close()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _PgLikeCursor:
        self._cursor.execute(_translate(sql), params)
        return self

    def _as_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {desc[0]: row[idx] for idx, desc in enumerate(self._cursor.description)}

    def fetchone(self) -> dict[str, Any] | None:
        return self._as_dict(self._cursor.fetchone())

    def fetchall(self) -> list[dict[str, Any]]:
        rows = self._cursor.fetchall()
        return [d for d in (self._as_dict(r) for r in rows) if d is not None]


class _PgLikeConnection:
    """Minimal Postgres-shaped connection backed by a real SQLite file."""

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(str(path))

    def cursor(self) -> _PgLikeCursor:
        return _PgLikeCursor(self._conn.cursor())

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


class PostgresStoreContractTests(RuntimeStoreContract, unittest.TestCase):
    def make_store(self) -> Any:
        self._dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self._dir, ignore_errors=True)
        self._db = self._dir / "pg.sqlite"
        return PostgresRuntimeStore(connection=_PgLikeConnection(self._db))

    def reopen(self, store: Any) -> Any:
        store.close()
        return PostgresRuntimeStore(connection=_PgLikeConnection(self._db))


class PostgresStoreConnectionTests(unittest.TestCase):
    def test_missing_psycopg_raises_runtime_store_error(self) -> None:
        # importlib.import_module bypasses builtins.__import__, so simulate the
        # missing package via sys.modules; this holds whether or not the real
        # psycopg is installed (the CI integration job installs it).
        with (
            mock.patch.dict(sys.modules, {"psycopg": None}),
            self.assertRaises(RuntimeStoreError) as ctx,
        ):
            PostgresRuntimeStore(dsn="postgresql://localhost/none")
        self.assertIn("psycopg", str(ctx.exception))

    def test_close_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PostgresRuntimeStore(connection=_PgLikeConnection(Path(tmp) / "pg.sqlite"))
            store.close()
            store.close()

    def test_write_error_wrapped_as_runtime_store_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PostgresRuntimeStore(connection=_PgLikeConnection(Path(tmp) / "pg.sqlite"))
            store.close()  # closed connection -> cursor() will raise
            with self.assertRaises(RuntimeStoreError):
                store.save_checkpoint("k", {"value": 1})

    def test_read_error_wrapped_as_runtime_store_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PostgresRuntimeStore(connection=_PgLikeConnection(Path(tmp) / "pg.sqlite"))
            store.close()
            with self.assertRaises(RuntimeStoreError):
                store.load_goal("any")

    def test_corrupt_plan_payload_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "pg.sqlite"
            store = PostgresRuntimeStore(connection=_PgLikeConnection(db))
            # Insert a plan row with an unpar-seable payload directly.
            store._execute(  # type: ignore[attr-defined]
                "INSERT INTO plans (plan_id, payload_json, created_at, updated_at) VALUES (%s, %s, %s, %s)",
                ("p1", "not-json", 0.0, 0.0),
            )
            with self.assertRaises(RuntimeStoreError):
                store.load_plan("p1")


@unittest.skipUnless(os.environ.get("LEOS_TEST_POSTGRES_DSN"), "requires a live PostgreSQL server")
class PostgresStoreRealServerTests(RuntimeStoreContract, unittest.TestCase):
    def setUp(self) -> None:
        # The store schema is append-friendly and never dropped, so a shared
        # database accumulates rows across tests and runs. Start each test
        # from empty tables to keep the contract's global-count assertions
        # meaningful against a persistent server.
        store = PostgresRuntimeStore(os.environ["LEOS_TEST_POSTGRES_DSN"])
        try:
            store._execute("DELETE FROM runtime_events", ())
            store._execute("DELETE FROM checkpoints", ())
            store._execute("DELETE FROM plans", ())
            store._execute("DELETE FROM goals", ())
        finally:
            store.close()

    def make_store(self) -> Any:
        return PostgresRuntimeStore(os.environ["LEOS_TEST_POSTGRES_DSN"])

    def reopen(self, store: Any) -> Any:
        store.close()
        return PostgresRuntimeStore(os.environ["LEOS_TEST_POSTGRES_DSN"])


if __name__ == "__main__":
    unittest.main()
