import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "/app/data/digiseva.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS services (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    type                TEXT NOT NULL,
    category            TEXT NOT NULL,
    amount              REAL NOT NULL,
    currency            TEXT NOT NULL DEFAULT 'INR',
    cycle               TEXT NOT NULL,
    next_due            TEXT NOT NULL,
    payment_method      TEXT NOT NULL DEFAULT '',
    auto_debit          INTEGER NOT NULL DEFAULT 0,
    paid_current_cycle  INTEGER NOT NULL DEFAULT 0,
    notes               TEXT NOT NULL DEFAULT '',
    active              INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    tenure_months       INTEGER,
    paid_instalments    INTEGER NOT NULL DEFAULT 0,
    credit_limit        REAL,
    outstanding_balance REAL NOT NULL DEFAULT 0,
    statement_amount    REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS payment_history (
    id          TEXT PRIMARY KEY,
    service_id  TEXT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    amount_paid REAL NOT NULL,
    paid_at     TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT ''
);
"""


def init_db() -> None:
    """Create tables if they don't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def get_db():
    """Context manager that yields a connection and commits/rolls back automatically."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_db_path() -> str:
    return DB_PATH
