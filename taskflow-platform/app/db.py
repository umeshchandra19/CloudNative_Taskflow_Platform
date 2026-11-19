"""Tiny database layer with the same API on SQLite (dev/tests) and PostgreSQL (production).

SQL is written once with '?' placeholders; for PostgreSQL they are rewritten to '%s'.
"""
import contextlib
import sqlite3
from datetime import date, datetime, timezone

from flask import current_app, g


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def iso(value):
    """Normalises datetime/date (PostgreSQL) and str (SQLite) values to an ISO string for JSON."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00") if value.tzinfo else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _dict_factory(cursor, row):
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


class Conn:
    def __init__(self, raw, dialect, release_fn, integrity_errors):
        self.raw, self.dialect, self._release, self.IntegrityError = raw, dialect, release_fn, integrity_errors

    def _sql(self, sql):
        return sql.replace("?", "%s") if self.dialect == "postgres" else sql

    def execute(self, sql, params=()):
        return self.raw.execute(self._sql(sql), tuple(params))

    def query(self, sql, params=()):
        return self.execute(sql, params).fetchall()

    def one(self, sql, params=()):
        return self.execute(sql, params).fetchone()

    def scalar(self, sql, params=()):
        row = self.one(sql, params)
        return next(iter(row.values())) if row else None

    def insert(self, sql, params=()):
        """INSERT ... RETURNING id (works on PostgreSQL and SQLite >= 3.35)."""
        return self.execute(sql + " RETURNING id", params).fetchone()["id"]

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def release(self):
        try:
            self.raw.rollback()  # discards anything not committed
        finally:
            self._release(self.raw)


class Database:
    def __init__(self, url, pool_max=20):
        if url.startswith("sqlite:///"):
            self.dialect, self.path = "sqlite", url[len("sqlite:///"):]
            self.pool = None
        elif url.startswith(("postgresql://", "postgres://")):
            import psycopg
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
            self.dialect = "postgres"
            self._integrity = (psycopg.IntegrityError,)
            self.pool = ConnectionPool(url, min_size=1, max_size=pool_max, timeout=10, open=True,
                                       kwargs={"row_factory": dict_row})
        else:
            raise ValueError("DATABASE_URL must start with sqlite:/// or postgresql://")

    def connect(self):
        if self.dialect == "sqlite":
            raw = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            raw.row_factory = _dict_factory
            raw.execute("PRAGMA foreign_keys=ON")
            raw.execute("PRAGMA journal_mode=WAL")
            return Conn(raw, "sqlite", lambda r: r.close(), (sqlite3.IntegrityError,))
        raw = self.pool.getconn()
        return Conn(raw, "postgres", self.pool.putconn, self._integrity)

    @contextlib.contextmanager
    def session(self):
        """A short-lived connection for code that runs outside a request (event poller, init, seeding)."""
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.release()

    def close(self):
        if self.pool is not None:
            self.pool.close()


def get_db():
    if "db" not in g:
        g.db = current_app.extensions["db"].connect()
    return g.db


def release_request_db():
    """Give the connection back early (used by the SSE endpoint so a long stream never holds a pool slot)."""
    conn = g.pop("db", None)
    if conn is not None:
        conn.release()


def close_db(exc=None):
    release_request_db()
