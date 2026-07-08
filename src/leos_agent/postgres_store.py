"""PostgreSQL-backed runtime store for production-grade agent persistence.

Implements the same 8-method ``RuntimeStore`` protocol as ``SQLiteRuntimeStore``
with matching semantics (upsert latest-wins, append-only ordered events, secret
rejection before write). The optional ``psycopg`` (v3) driver is imported lazily;
a connection may also be injected (used for tests and for callers that manage
their own pooling).
"""

from __future__ import annotations

import importlib
import json
import time
from collections.abc import Mapping
from contextlib import suppress
from typing import Any, Protocol

from .goals import Goal
from .plans import TransactionPlan
from .runtime_store import RuntimeStoreError, _goal_from_dict, _goal_to_dict
from .sanitization import SanitizationError, assert_no_secrets
from .serialization import SerializationError, deserialize_plan, serialize_plan


class _Cursor(Protocol):
    def execute(self, sql: str, params: tuple[Any, ...] = ...) -> Any: ...

    def fetchone(self) -> Any: ...

    def fetchall(self) -> list[Any]: ...

    def __enter__(self) -> _Cursor: ...

    def __exit__(self, *exc: object) -> None: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...

    def commit(self) -> None: ...

    def close(self) -> None: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
  goal_id TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL,
  updated_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
  plan_id TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL,
  updated_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS runtime_events (
  sequence BIGSERIAL PRIMARY KEY,
  goal_id TEXT,
  payload_json TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
  key TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL,
  updated_at DOUBLE PRECISION NOT NULL
);
"""


class PostgresRuntimeStore:
    """PostgreSQL runtime store implementing the ``RuntimeStore`` protocol."""

    def __init__(self, dsn: str | None = None, *, connection: _Connection | None = None) -> None:
        self._closed = False
        if connection is not None:
            self._conn = connection
        else:
            self._conn = self._connect(dsn)
        try:
            self._init_schema()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeStoreError(f"postgres runtime store schema init failed: {type(exc).__name__}") from exc

    @staticmethod
    def _connect(dsn: str | None) -> _Connection:
        try:
            psycopg = importlib.import_module("psycopg")
        except ImportError as exc:
            raise RuntimeStoreError("PostgresRuntimeStore requires the optional 'psycopg' package") from exc
        try:  # pragma: no cover - needs a live PostgreSQL server
            rows = importlib.import_module("psycopg.rows")
            connection: _Connection = psycopg.connect(dsn, row_factory=rows.dict_row)
            return connection
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - needs a live server
            raise RuntimeStoreError(f"postgres runtime store unavailable: {type(exc).__name__}") from exc

    # -- RuntimeStore protocol ------------------------------------------------

    def save_goal(self, goal: Goal) -> None:
        payload = _goal_to_dict(goal)
        _assert_pg_safe(payload)
        now = time.time()
        self._execute(
            """
            INSERT INTO goals (goal_id, payload_json, created_at, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (goal_id) DO UPDATE SET
              payload_json = EXCLUDED.payload_json,
              updated_at = EXCLUDED.updated_at
            """,
            (goal.goal_id, json.dumps(payload, ensure_ascii=False), now, now),
        )

    def load_goal(self, goal_id: str) -> Goal | None:
        row = self._fetchone("SELECT payload_json FROM goals WHERE goal_id = %s", (goal_id,))
        if row is None:
            return None
        return _goal_from_dict(_loads_object(_col(row, "payload_json"), "goal payload"))

    def save_plan(self, plan: TransactionPlan) -> None:
        try:
            payload = json.loads(serialize_plan(plan))
        except (SerializationError, TypeError, ValueError) as exc:
            raise RuntimeStoreError(f"Could not serialize plan: {type(exc).__name__}") from exc
        _assert_pg_safe(payload)
        now = time.time()
        self._execute(
            """
            INSERT INTO plans (plan_id, payload_json, created_at, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (plan_id) DO UPDATE SET
              payload_json = EXCLUDED.payload_json,
              updated_at = EXCLUDED.updated_at
            """,
            (plan.plan_id, json.dumps(payload, ensure_ascii=False), now, now),
        )

    def load_plan(self, plan_id: str) -> TransactionPlan | None:
        row = self._fetchone("SELECT payload_json FROM plans WHERE plan_id = %s", (plan_id,))
        if row is None:
            return None
        try:
            return deserialize_plan(str(_col(row, "payload_json")))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeStoreError(f"Could not deserialize plan: {type(exc).__name__}") from exc

    def append_runtime_event(self, event: Mapping[str, Any]) -> None:
        _assert_pg_safe(event)
        now = time.time()
        self._execute(
            "INSERT INTO runtime_events (goal_id, payload_json, created_at) VALUES (%s, %s, %s)",
            (event.get("goal_id"), json.dumps(dict(event), ensure_ascii=False), now),
        )

    def list_runtime_events(self, goal_id: str | None = None) -> list[dict[str, Any]]:
        if goal_id is None:
            rows = self._fetchall("SELECT payload_json FROM runtime_events ORDER BY sequence ASC", ())
        else:
            rows = self._fetchall(
                "SELECT payload_json FROM runtime_events WHERE goal_id = %s ORDER BY sequence ASC",
                (goal_id,),
            )
        return [_loads_object(_col(row, "payload_json"), "runtime event") for row in rows]

    def save_checkpoint(self, key: str, value: Mapping[str, Any]) -> None:
        _assert_pg_safe(value)
        now = time.time()
        self._execute(
            """
            INSERT INTO checkpoints (key, payload_json, created_at, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (key) DO UPDATE SET
              payload_json = EXCLUDED.payload_json,
              updated_at = EXCLUDED.updated_at
            """,
            (key, json.dumps(dict(value), ensure_ascii=False), now, now),
        )

    def load_checkpoint(self, key: str) -> dict[str, Any] | None:
        row = self._fetchone("SELECT payload_json FROM checkpoints WHERE key = %s", (key,))
        if row is None:
            return None
        return _loads_object(_col(row, "payload_json"), "checkpoint")

    # -- lifecycle ------------------------------------------------------------

    def close(self) -> None:
        if not self._closed:
            self._conn.close()
            self._closed = True

    def __enter__(self) -> PostgresRuntimeStore:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()

    def __del__(self) -> None:
        with suppress(Exception):
            self.close()

    # -- internals ------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._conn.cursor() as cur:
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    cur.execute(statement)
        self._conn.commit()

    def _execute(self, sql: str, params: tuple[Any, ...]) -> None:
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
            self._conn.commit()
        except RuntimeStoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuntimeStoreError(f"postgres runtime store write failed: {type(exc).__name__}") from exc

    def _fetchone(self, sql: str, params: tuple[Any, ...]) -> Any:
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeStoreError(f"postgres runtime store read failed: {type(exc).__name__}") from exc

    def _fetchall(self, sql: str, params: tuple[Any, ...]) -> list[Any]:
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
        except Exception as exc:  # noqa: BLE001
            raise RuntimeStoreError(f"postgres runtime store read failed: {type(exc).__name__}") from exc


def _col(row: Any, name: str) -> str:
    """Read a column from a dict-row or a positional row."""
    if isinstance(row, Mapping):
        return str(row[name])
    return str(row[0])


def _assert_pg_safe(value: Any) -> None:
    try:
        assert_no_secrets(value)
    except SanitizationError as exc:
        raise RuntimeStoreError(f"PostgresRuntimeStore rejected secret-like value: {exc}") from exc


def _loads_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeStoreError(f"Invalid {label} JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeStoreError(f"Invalid {label}: expected object")
    return value
