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

CREATE TABLE IF NOT EXISTS paid_log (
    id           TEXT PRIMARY KEY,
    service_id   TEXT NOT NULL,
    service_name TEXT NOT NULL,
    amount       REAL NOT NULL,
    type         TEXT NOT NULL,
    category     TEXT NOT NULL,
    cycle_month  TEXT NOT NULL,
    paid_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS investments (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    current_value   REAL NOT NULL DEFAULT 0,
    invested_amount REAL NOT NULL DEFAULT 0,
    institution     TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    active          INTEGER NOT NULL DEFAULT 1,
    last_updated    TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id                   TEXT PRIMARY KEY,
    username             TEXT UNIQUE NOT NULL,
    pin_hash             TEXT NOT NULL,
    encrypted_data_key   TEXT NOT NULL DEFAULT '',
    key_nonce            TEXT NOT NULL DEFAULT '',
    telegram_chat_id     TEXT,
    link_code            TEXT,
    link_code_expires    TEXT,
    created_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_telegram ON users(telegram_chat_id);
"""


def init_db() -> None:
    """Create tables and run incremental schema migrations."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as conn:
        conn.executescript(_SCHEMA)
        _add_user_id_columns(conn)
        _migrate_default_user_ids(conn)


def _add_user_id_columns(conn) -> None:
    """Phase 2: add user_id column to data tables (idempotent)."""
    for table in ("services", "investments", "paid_log", "payment_history"):
        try:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'"
            )
        except Exception:
            pass  # column already exists — that's fine

    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_services_user      ON services(user_id);
        CREATE INDEX IF NOT EXISTS idx_investments_user   ON investments(user_id);
        CREATE INDEX IF NOT EXISTS idx_paid_log_user      ON paid_log(user_id);
        CREATE INDEX IF NOT EXISTS idx_payment_hist_user  ON payment_history(user_id);
    """)


def _migrate_default_user_ids(conn) -> None:
    """Phase 2: when there is exactly one registered user, assign all
    'default' rows to that user.  Runs on every startup but is a no-op
    once migration is complete."""
    try:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count != 1:
            return
        default_count = conn.execute(
            "SELECT COUNT(*) FROM services WHERE user_id = 'default'"
        ).fetchone()[0]
        if default_count == 0:
            return
        uid = conn.execute("SELECT id FROM users LIMIT 1").fetchone()[0]
        for table in ("services", "investments", "paid_log", "payment_history"):
            conn.execute(
                f"UPDATE {table} SET user_id = ? WHERE user_id = 'default'", (uid,)
            )
    except Exception:
        pass  # users table may not exist yet on very first run


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
