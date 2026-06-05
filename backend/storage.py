import calendar
import uuid
from datetime import date, datetime, timedelta
from typing import List, Optional

from models import Service
from database import get_db

# Columns allowed in dynamic UPDATE statements — guards against injection from internal callers
_UPDATABLE = {
    "name", "type", "category", "amount", "currency", "cycle", "next_due",
    "payment_method", "auto_debit", "paid_current_cycle", "notes", "active",
    "tenure_months", "paid_instalments", "credit_limit", "outstanding_balance",
    "statement_amount",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_service(row) -> Service:
    d = dict(row)
    d["auto_debit"] = bool(d["auto_debit"])
    d["paid_current_cycle"] = bool(d["paid_current_cycle"])
    d["active"] = bool(d["active"])
    return Service(**d)


def _reset_overdue(conn) -> None:
    """Reset paid_current_cycle for entries whose next_due has passed.

    This is the cycle-rollover logic: once the period you paid for has elapsed,
    the next period is due again.
    """
    conn.execute(
        "UPDATE services SET paid_current_cycle = 0 "
        "WHERE paid_current_cycle = 1 AND next_due < date('now')"
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def load_services(include_inactive: bool = False) -> List[Service]:
    """Load services from the database.

    By default only active entries are returned. Pass include_inactive=True
    to also include entries where active=0 (e.g. for CSV export or admin views).
    """
    with get_db() as conn:
        _reset_overdue(conn)
        if include_inactive:
            rows = conn.execute("SELECT * FROM services").fetchall()
        else:
            rows = conn.execute("SELECT * FROM services WHERE active = 1").fetchall()
    return [_row_to_service(r) for r in rows]


def get_service(service_id: str) -> Optional[Service]:
    with get_db() as conn:
        _reset_overdue(conn)
        row = conn.execute(
            "SELECT * FROM services WHERE id = ?", (service_id,)
        ).fetchone()
    return _row_to_service(row) if row else None


def add_service(service: Service) -> Service:
    d = service.model_dump()
    with get_db() as conn:
        conn.execute("""
            INSERT INTO services (
                id, name, type, category, amount, currency, cycle, next_due,
                payment_method, auto_debit, paid_current_cycle, notes, active, created_at,
                tenure_months, paid_instalments, credit_limit, outstanding_balance, statement_amount
            ) VALUES (
                :id, :name, :type, :category, :amount, :currency, :cycle, :next_due,
                :payment_method, :auto_debit, :paid_current_cycle, :notes, :active, :created_at,
                :tenure_months, :paid_instalments, :credit_limit, :outstanding_balance, :statement_amount
            )
        """, d)
    return service


def update_service(service_id: str, updates: dict) -> Optional[Service]:
    # Strip unknown columns and explicit None values (but keep False / 0 / "")
    clean = {k: v for k, v in updates.items() if k in _UPDATABLE and v is not None}
    if not clean:
        return get_service(service_id)
    set_clause = ", ".join(f"{k} = :{k}" for k in clean)
    clean["_id"] = service_id
    with get_db() as conn:
        conn.execute(f"UPDATE services SET {set_clause} WHERE id = :_id", clean)
        row = conn.execute(
            "SELECT * FROM services WHERE id = ?", (service_id,)
        ).fetchone()
    return _row_to_service(row) if row else None


def delete_service(service_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM services WHERE id = ?", (service_id,))
    return cursor.rowcount > 0


def save_services(services: List[Service]) -> None:
    """Bulk upsert — used by the CSV import endpoint."""
    with get_db() as conn:
        for s in services:
            d = s.model_dump()
            conn.execute("""
                INSERT OR REPLACE INTO services (
                    id, name, type, category, amount, currency, cycle, next_due,
                    payment_method, auto_debit, paid_current_cycle, notes, active, created_at,
                    tenure_months, paid_instalments, credit_limit, outstanding_balance, statement_amount
                ) VALUES (
                    :id, :name, :type, :category, :amount, :currency, :cycle, :next_due,
                    :payment_method, :auto_debit, :paid_current_cycle, :notes, :active, :created_at,
                    :tenure_months, :paid_instalments, :credit_limit, :outstanding_balance, :statement_amount
                )
            """, d)


# ---------------------------------------------------------------------------
# Payment history (credit cards)
# ---------------------------------------------------------------------------

def add_payment_history(service_id: str, amount_paid: float, notes: str = "") -> dict:
    record = {
        "id": str(uuid.uuid4()),
        "service_id": service_id,
        "amount_paid": amount_paid,
        "paid_at": datetime.now().isoformat(),
        "notes": notes,
    }
    with get_db() as conn:
        conn.execute(
            "INSERT INTO payment_history (id, service_id, amount_paid, paid_at, notes) "
            "VALUES (:id, :service_id, :amount_paid, :paid_at, :notes)",
            record,
        )
    return record


def get_payment_history(service_id: str) -> List[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM payment_history WHERE service_id = ? ORDER BY paid_at DESC",
            (service_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Cycle advancement
# ---------------------------------------------------------------------------

def advance_next_due(service: Service) -> str:
    cycle = service.cycle
    try:
        current = date.fromisoformat(service.next_due)
    except ValueError:
        current = date.today()

    if cycle == "weekly":
        next_due = current + timedelta(weeks=1)
    elif cycle == "monthly":
        month = current.month + 1
        year = current.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        day = min(current.day, calendar.monthrange(year, month)[1])
        next_due = date(year, month, day)
    elif cycle == "bi-monthly":
        month = current.month + 2
        year = current.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        day = min(current.day, calendar.monthrange(year, month)[1])
        next_due = date(year, month, day)
    elif cycle == "quarterly":
        month = current.month + 3
        year = current.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        day = min(current.day, calendar.monthrange(year, month)[1])
        next_due = date(year, month, day)
    elif cycle == "half-yearly":
        month = current.month + 6
        year = current.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        day = min(current.day, calendar.monthrange(year, month)[1])
        next_due = date(year, month, day)
    elif cycle == "yearly":
        try:
            next_due = date(current.year + 1, current.month, current.day)
        except ValueError:
            next_due = date(current.year + 1, current.month, 28)
    else:  # one-time
        next_due = current

    return next_due.isoformat()


# ---------------------------------------------------------------------------
# Scheduler helper
# ---------------------------------------------------------------------------

def auto_mark_paid() -> List[Service]:
    """Mark active auto-debit services as paid when their due date has arrived.

    For EMI entries: increments paid_instalments and deactivates the entry when
    the full tenure is complete.

    Returns the list of services just auto-marked (used in the morning notification).
    """
    today = date.today().isoformat()
    marked: List[Service] = []

    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM services
            WHERE active = 1 AND auto_debit = 1 AND paid_current_cycle = 0
            AND next_due <= ?
        """, (today,)).fetchall()

        for row in rows:
            s = _row_to_service(row)
            new_due = advance_next_due(s)
            updates: dict = {"paid_current_cycle": 1, "next_due": new_due}

            # EMI: track instalments, auto-close when tenure completes
            if s.tenure_months is not None:
                new_paid = s.paid_instalments + 1
                updates["paid_instalments"] = new_paid
                if new_paid >= s.tenure_months:
                    updates["active"] = 0

            set_clause = ", ".join(f"{k} = :{k}" for k in updates)
            updates["_id"] = s.id
            conn.execute(f"UPDATE services SET {set_clause} WHERE id = :_id", updates)

            updated_row = conn.execute(
                "SELECT * FROM services WHERE id = ?", (s.id,)
            ).fetchone()
            marked.append(_row_to_service(updated_row))

    return marked


def get_db_path() -> str:
    from database import DB_PATH
    return DB_PATH


# ---------------------------------------------------------------------------
# Paid log (monthly payment history for all service types)
# ---------------------------------------------------------------------------

def add_paid_log(service: Service) -> None:
    """Record a payment event when any service is marked as paid/received."""
    cycle_month = datetime.now().strftime("%Y-%m")
    record = {
        "id": str(uuid.uuid4()),
        "service_id": service.id,
        "service_name": service.name,
        "amount": service.amount,
        "type": service.type,
        "category": service.category,
        "cycle_month": cycle_month,
        "paid_at": datetime.now().isoformat(),
    }
    with get_db() as conn:
        conn.execute(
            "INSERT INTO paid_log (id, service_id, service_name, amount, type, category, cycle_month, paid_at) "
            "VALUES (:id, :service_id, :service_name, :amount, :type, :category, :cycle_month, :paid_at)",
            record,
        )


def get_history_months() -> list:
    """Return months that have log records, with income/outgo totals."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT cycle_month,
                   SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END)  AS total_income,
                   SUM(CASE WHEN type != 'income' THEN amount ELSE 0 END) AS total_outgo,
                   COUNT(*) AS count
            FROM paid_log
            GROUP BY cycle_month
            ORDER BY cycle_month DESC
        """).fetchall()
    return [dict(r) for r in rows]


def get_month_log(year_month: str) -> list:
    """Return all paid_log records for a specific YYYY-MM month."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM paid_log WHERE cycle_month = ? ORDER BY type, paid_at",
            (year_month,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Investments
# ---------------------------------------------------------------------------

_INV_UPDATABLE = {"name", "category", "current_value", "invested_amount",
                  "institution", "notes", "active", "last_updated"}


def _row_to_investment(row) -> dict:
    d = dict(row)
    d["active"] = bool(d["active"])
    return d


def load_investments() -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM investments WHERE active = 1 ORDER BY category, name"
        ).fetchall()
    return [_row_to_investment(r) for r in rows]


def get_investment(inv_id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM investments WHERE id = ?", (inv_id,)).fetchone()
    return _row_to_investment(row) if row else None


def add_investment(inv: dict) -> dict:
    with get_db() as conn:
        conn.execute("""
            INSERT INTO investments
                (id, name, category, current_value, invested_amount,
                 institution, notes, active, last_updated, created_at)
            VALUES
                (:id, :name, :category, :current_value, :invested_amount,
                 :institution, :notes, :active, :last_updated, :created_at)
        """, inv)
    return inv


def update_investment(inv_id: str, updates: dict) -> Optional[dict]:
    clean = {k: v for k, v in updates.items() if k in _INV_UPDATABLE and v is not None}
    if clean:
        set_clause = ", ".join(f"{k} = :{k}" for k in clean)
        clean["_id"] = inv_id
        with get_db() as conn:
            conn.execute(f"UPDATE investments SET {set_clause} WHERE id = :_id", clean)
    return get_investment(inv_id)


def delete_investment(inv_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM investments WHERE id = ?", (inv_id,))
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def create_user(user: dict) -> dict:
    with get_db() as conn:
        conn.execute("""
            INSERT INTO users
                (id, username, pin_hash, encrypted_data_key, key_nonce, created_at)
            VALUES
                (:id, :username, :pin_hash, :encrypted_data_key, :key_nonce, :created_at)
        """, user)
    return user


def get_user_by_username(username: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_chat_id(chat_id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_chat_id = ?", (str(chat_id),)
        ).fetchone()
    return dict(row) if row else None


def get_user_count() -> int:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def update_user(user_id: str, updates: dict) -> Optional[dict]:
    _UPDATABLE_USER = {"pin_hash", "encrypted_data_key", "key_nonce",
                       "telegram_chat_id", "link_code", "link_code_expires"}
    clean = {k: v for k, v in updates.items() if k in _UPDATABLE_USER}
    if not clean:
        return get_user_by_id(user_id)
    set_clause = ", ".join(f"{k} = :{k}" for k in clean)
    clean["_id"] = user_id
    with get_db() as conn:
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = :_id", clean)
    return get_user_by_id(user_id)
