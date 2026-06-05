import calendar
import uuid
from datetime import date, datetime, timedelta
from typing import List, Optional

from models import Service
from database import get_db
from crypto import encrypt_fields, decrypt_fields

# Columns allowed in dynamic UPDATE statements
_UPDATABLE = {
    "name", "type", "category", "amount", "currency", "cycle", "next_due",
    "payment_method", "auto_debit", "paid_current_cycle", "notes", "active",
    "tenure_months", "paid_instalments", "credit_limit", "outstanding_balance",
    "statement_amount",
}

# Sensitive fields encrypted per table (plain values replaced with placeholders when encrypted)
_SENS_SERVICE    = ("name", "amount", "category", "payment_method", "notes",
                    "credit_limit", "outstanding_balance", "statement_amount")
_SENS_INVESTMENT = ("name", "category", "current_value", "invested_amount",
                    "institution", "notes")
_SENS_PAID_LOG   = ("service_name", "amount", "category")

# Safe placeholder values written to plain columns when data is encrypted
_PLACEHOLDERS = {
    "name": "•••", "service_name": "•••", "institution": "•••",
    "amount": 0.0, "current_value": 0.0, "invested_amount": 0.0,
    "outstanding_balance": 0.0, "statement_amount": 0.0, "credit_limit": None,
    "category": "encrypted", "payment_method": "", "notes": "",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_service(row, data_key: Optional[bytes] = None) -> Service:
    d = dict(row)
    d.pop("user_id", None)
    enc_data  = d.pop("enc_data",  None)
    enc_nonce = d.pop("enc_nonce", None)
    if data_key and enc_data and enc_nonce:
        try:
            d.update(decrypt_fields(data_key, enc_data, enc_nonce))
        except Exception:
            pass  # decryption failed — use plain placeholder values
    d["auto_debit"]         = bool(d.get("auto_debit", False))
    d["paid_current_cycle"] = bool(d.get("paid_current_cycle", False))
    d["active"]             = bool(d.get("active", True))
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

def _apply_encryption(d: dict, sensitive_fields: tuple, data_key: Optional[bytes]) -> dict:
    """Encrypt sensitive fields into enc_data; replace plain values with placeholders."""
    if data_key:
        payload = {k: d[k] for k in sensitive_fields if k in d}
        d["enc_data"], d["enc_nonce"] = encrypt_fields(data_key, payload)
        for k in sensitive_fields:
            if k in d:
                d[k] = _PLACEHOLDERS.get(k, d[k])
    else:
        d["enc_data"]  = None
        d["enc_nonce"] = None
    return d


def load_services(user_id: str, include_inactive: bool = False,
                  data_key: Optional[bytes] = None) -> List[Service]:
    with get_db() as conn:
        _reset_overdue(conn)
        q = "SELECT * FROM services WHERE user_id = ?"
        if not include_inactive:
            q += " AND active = 1"
        rows = conn.execute(q, (user_id,)).fetchall()
    return [_row_to_service(r, data_key) for r in rows]


def get_service(service_id: str, user_id: str,
                data_key: Optional[bytes] = None) -> Optional[Service]:
    with get_db() as conn:
        _reset_overdue(conn)
        row = conn.execute(
            "SELECT * FROM services WHERE id = ? AND user_id = ?", (service_id, user_id)
        ).fetchone()
    return _row_to_service(row, data_key) if row else None


def add_service(service: Service, user_id: str, data_key: Optional[bytes] = None) -> Service:
    d = service.model_dump()
    d["user_id"] = user_id
    _apply_encryption(d, _SENS_SERVICE, data_key)
    with get_db() as conn:
        conn.execute("""
            INSERT INTO services (
                id, user_id, name, type, category, amount, currency, cycle, next_due,
                payment_method, auto_debit, paid_current_cycle, notes, active, created_at,
                tenure_months, paid_instalments, credit_limit, outstanding_balance,
                statement_amount, enc_data, enc_nonce
            ) VALUES (
                :id, :user_id, :name, :type, :category, :amount, :currency, :cycle, :next_due,
                :payment_method, :auto_debit, :paid_current_cycle, :notes, :active, :created_at,
                :tenure_months, :paid_instalments, :credit_limit, :outstanding_balance,
                :statement_amount, :enc_data, :enc_nonce
            )
        """, d)
    return service


def update_service(service_id: str, updates: dict, user_id: str,
                   data_key: Optional[bytes] = None) -> Optional[Service]:
    clean = {k: v for k, v in updates.items() if k in _UPDATABLE and v is not None}
    if not clean:
        return get_service(service_id, user_id, data_key)
    # If updating sensitive fields and we have a data_key, re-encrypt the full row
    if data_key and any(k in _SENS_SERVICE for k in clean):
        # Merge with existing decrypted values, then re-encrypt
        existing = get_service(service_id, user_id, data_key)
        if existing:
            merged = existing.model_dump()
            merged.update({k: v for k, v in clean.items()})
            payload = {k: merged[k] for k in _SENS_SERVICE if k in merged}
            enc_data, enc_nonce = encrypt_fields(data_key, payload)
            clean["enc_data"]  = enc_data
            clean["enc_nonce"] = enc_nonce
            for k in _SENS_SERVICE:
                if k in clean:
                    clean[k] = _PLACEHOLDERS.get(k, clean[k])
            _UPDATABLE_WITH_ENC = _UPDATABLE | {"enc_data", "enc_nonce"}
            clean2 = {k: v for k, v in clean.items() if k in _UPDATABLE_WITH_ENC and v is not None}
            clean = clean2
    set_clause = ", ".join(f"{k} = :{k}" for k in clean)
    clean["_id"]  = service_id
    clean["_uid"] = user_id
    with get_db() as conn:
        conn.execute(f"UPDATE services SET {set_clause} WHERE id = :_id AND user_id = :_uid", clean)
        row = conn.execute(
            "SELECT * FROM services WHERE id = ? AND user_id = ?", (service_id, user_id)
        ).fetchone()
    return _row_to_service(row, data_key) if row else None


def delete_service(service_id: str, user_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM services WHERE id = ? AND user_id = ?", (service_id, user_id)
        )
    return cursor.rowcount > 0


def save_services(services: List[Service], user_id: str,
                  data_key: Optional[bytes] = None) -> None:
    """Bulk upsert — CSV import."""
    with get_db() as conn:
        for s in services:
            d = s.model_dump()
            d["user_id"] = user_id
            _apply_encryption(d, _SENS_SERVICE, data_key)
            conn.execute("""
                INSERT OR REPLACE INTO services (
                    id, user_id, name, type, category, amount, currency, cycle, next_due,
                    payment_method, auto_debit, paid_current_cycle, notes, active, created_at,
                    tenure_months, paid_instalments, credit_limit, outstanding_balance,
                    statement_amount, enc_data, enc_nonce
                ) VALUES (
                    :id, :user_id, :name, :type, :category, :amount, :currency, :cycle, :next_due,
                    :payment_method, :auto_debit, :paid_current_cycle, :notes, :active, :created_at,
                    :tenure_months, :paid_instalments, :credit_limit, :outstanding_balance,
                    :statement_amount, :enc_data, :enc_nonce
                )
            """, d)


# ---------------------------------------------------------------------------
# Payment history (credit cards)
# ---------------------------------------------------------------------------

def add_payment_history(service_id: str, amount_paid: float, user_id: str, notes: str = "") -> dict:
    record = {
        "id": str(uuid.uuid4()),
        "service_id": service_id,
        "user_id": user_id,
        "amount_paid": amount_paid,
        "paid_at": datetime.now().isoformat(),
        "notes": notes,
    }
    with get_db() as conn:
        conn.execute(
            "INSERT INTO payment_history (id, service_id, user_id, amount_paid, paid_at, notes) "
            "VALUES (:id, :service_id, :user_id, :amount_paid, :paid_at, :notes)",
            record,
        )
    return record


def get_payment_history(service_id: str, user_id: str) -> List[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM payment_history WHERE service_id = ? AND user_id = ? ORDER BY paid_at DESC",
            (service_id, user_id),
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

def get_linked_users() -> list:
    """Return all users who have linked a Telegram account."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE telegram_chat_id IS NOT NULL"
        ).fetchall()
    return [dict(row) for row in rows]


def auto_mark_paid(user_id: Optional[str] = None,
                   data_key: Optional[bytes] = None) -> List[Service]:
    """Mark active auto-debit services as paid when their due date has arrived.

    If user_id is given, only that user's services are processed.
    data_key is used to decrypt service fields and encrypt the paid_log entry.
    Returns the list of services just auto-marked (used in the morning notification).
    """
    today = date.today().isoformat()
    marked: List[Service] = []
    to_log: list = []   # (service_with_real_fields, row_user_id)

    with get_db() as conn:
        if user_id:
            rows = conn.execute("""
                SELECT * FROM services
                WHERE active = 1 AND auto_debit = 1 AND paid_current_cycle = 0
                AND next_due <= ? AND user_id = ?
            """, (today, user_id)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM services
                WHERE active = 1 AND auto_debit = 1 AND paid_current_cycle = 0
                AND next_due <= ?
            """, (today,)).fetchall()

        for row in rows:
            row_uid = dict(row).get("user_id", user_id or "")
            # Decrypt so we have real field values for the paid_log entry
            s = _row_to_service(row, data_key=data_key)
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
            marked.append(_row_to_service(updated_row, data_key=data_key))
            to_log.append((s, row_uid))  # original `s` has the decrypted real fields

    # Write paid_log entries AFTER the service transaction commits
    for svc, uid in to_log:
        try:
            add_paid_log(svc, uid, data_key=data_key)
        except Exception:
            pass  # never let logging failure abort the auto-mark result

    return marked


def get_db_path() -> str:
    from database import DB_PATH
    return DB_PATH


# ---------------------------------------------------------------------------
# Paid log (monthly payment history for all service types)
# ---------------------------------------------------------------------------

def add_paid_log(service: Service, user_id: str, data_key: Optional[bytes] = None) -> None:
    cycle_month = datetime.now().strftime("%Y-%m")
    record: dict = {
        "id":           str(uuid.uuid4()),
        "user_id":      user_id,
        "service_id":   service.id,
        "service_name": service.name,
        "amount":       service.amount,
        "type":         service.type,
        "category":     service.category,
        "cycle_month":  cycle_month,
        "paid_at":      datetime.now().isoformat(),
    }
    _apply_encryption(record, _SENS_PAID_LOG, data_key)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO paid_log "
            "(id, user_id, service_id, service_name, amount, type, category, cycle_month, paid_at, enc_data, enc_nonce) "
            "VALUES (:id, :user_id, :service_id, :service_name, :amount, :type, :category, :cycle_month, :paid_at, :enc_data, :enc_nonce)",
            record,
        )


def _decrypt_paid_log_row(d: dict, data_key: Optional[bytes]) -> dict:
    """Decrypt enc_data into the row dict if data_key is provided."""
    if data_key and d.get("enc_data") and d.get("enc_nonce"):
        try:
            d.update(decrypt_fields(data_key, d["enc_data"], d["enc_nonce"]))
        except Exception:
            pass  # leave placeholders on failure
    return d


def get_history_months(user_id: str, data_key: Optional[bytes] = None) -> list:
    """Return months that have log records for a user, with income/outgo totals.

    Aggregation is done in Python after decryption — the plain `amount` column
    is a 0.0 placeholder for encrypted rows, so SQL SUM would always return 0.
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM paid_log WHERE user_id = ? ORDER BY cycle_month DESC",
            (user_id,)
        ).fetchall()

    from collections import defaultdict
    months: dict = defaultdict(lambda: {"total_income": 0.0, "total_outgo": 0.0, "count": 0})
    for row in rows:
        d = _decrypt_paid_log_row(dict(row), data_key)
        m = d["cycle_month"]
        amt = float(d.get("amount") or 0)
        if d.get("type") == "income":
            months[m]["total_income"] += amt
        else:
            months[m]["total_outgo"] += amt
        months[m]["count"] += 1
        months[m]["cycle_month"] = m

    return sorted(months.values(), key=lambda x: x["cycle_month"], reverse=True)


def get_month_log(year_month: str, user_id: str,
                  data_key: Optional[bytes] = None) -> list:
    """Return all paid_log records for a specific YYYY-MM month, decrypted."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM paid_log WHERE cycle_month = ? AND user_id = ? ORDER BY type, paid_at",
            (year_month, user_id),
        ).fetchall()
    return [_decrypt_paid_log_row(dict(r), data_key) for r in rows]


# ---------------------------------------------------------------------------
# Investments
# ---------------------------------------------------------------------------

_INV_UPDATABLE = {"name", "category", "current_value", "invested_amount",
                  "institution", "notes", "active", "last_updated"}


def _row_to_investment(row, data_key: Optional[bytes] = None) -> dict:
    d = dict(row)
    d.pop("user_id", None)
    enc_data  = d.pop("enc_data",  None)
    enc_nonce = d.pop("enc_nonce", None)
    if data_key and enc_data and enc_nonce:
        try:
            d.update(decrypt_fields(data_key, enc_data, enc_nonce))
        except Exception:
            pass
    d["active"] = bool(d.get("active", True))
    return d


def load_investments(user_id: str, data_key: Optional[bytes] = None) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM investments WHERE user_id = ? AND active = 1 ORDER BY category, name",
            (user_id,)
        ).fetchall()
    return [_row_to_investment(r, data_key) for r in rows]


def get_investment(inv_id: str, user_id: str, data_key: Optional[bytes] = None) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM investments WHERE id = ? AND user_id = ?", (inv_id, user_id)
        ).fetchone()
    return _row_to_investment(row, data_key) if row else None


def add_investment(inv: dict, user_id: str, data_key: Optional[bytes] = None) -> dict:
    inv["user_id"] = user_id
    _apply_encryption(inv, _SENS_INVESTMENT, data_key)
    with get_db() as conn:
        conn.execute("""
            INSERT INTO investments
                (id, user_id, name, category, current_value, invested_amount,
                 institution, notes, active, last_updated, created_at, enc_data, enc_nonce)
            VALUES
                (:id, :user_id, :name, :category, :current_value, :invested_amount,
                 :institution, :notes, :active, :last_updated, :created_at, :enc_data, :enc_nonce)
        """, inv)
    inv.pop("user_id", None)
    inv.pop("enc_data", None)
    inv.pop("enc_nonce", None)
    return inv


def update_investment(inv_id: str, updates: dict, user_id: str,
                      data_key: Optional[bytes] = None) -> Optional[dict]:
    clean = {k: v for k, v in updates.items() if k in _INV_UPDATABLE and v is not None}
    if clean:
        if data_key and any(k in _SENS_INVESTMENT for k in clean):
            existing = get_investment(inv_id, user_id, data_key)
            if existing:
                merged = {**existing, **{k: v for k, v in clean.items()}}
                payload = {k: merged[k] for k in _SENS_INVESTMENT if k in merged}
                enc_data, enc_nonce = encrypt_fields(data_key, payload)
                clean["enc_data"]  = enc_data
                clean["enc_nonce"] = enc_nonce
                for k in _SENS_INVESTMENT:
                    if k in clean:
                        clean[k] = _PLACEHOLDERS.get(k, clean[k])
        _INV_WITH_ENC = _INV_UPDATABLE | {"enc_data", "enc_nonce"}
        clean2 = {k: v for k, v in clean.items() if k in _INV_WITH_ENC and v is not None}
        set_clause = ", ".join(f"{k} = :{k}" for k in clean2)
        clean2["_id"]  = inv_id
        clean2["_uid"] = user_id
        with get_db() as conn:
            conn.execute(
                f"UPDATE investments SET {set_clause} WHERE id = :_id AND user_id = :_uid", clean2
            )
    return get_investment(inv_id, user_id, data_key)


def delete_investment(inv_id: str, user_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM investments WHERE id = ? AND user_id = ?", (inv_id, user_id)
        )
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


def get_user_by_link_code(code: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE link_code = ?", (code,)
        ).fetchone()
    return dict(row) if row else None


def get_user_count() -> int:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def update_user(user_id: str, updates: dict) -> Optional[dict]:
    _UPDATABLE_USER = {"pin_hash", "encrypted_data_key", "key_nonce",
                       "telegram_chat_id", "link_code", "link_code_expires",
                       "scheduler_encrypted_key", "scheduler_key_nonce"}
    clean = {k: v for k, v in updates.items() if k in _UPDATABLE_USER}
    if not clean:
        return get_user_by_id(user_id)
    set_clause = ", ".join(f"{k} = :{k}" for k in clean)
    clean["_id"] = user_id
    with get_db() as conn:
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = :_id", clean)
    return get_user_by_id(user_id)


# ---------------------------------------------------------------------------
# Phase 3: background encryption migration
# ---------------------------------------------------------------------------

def migrate_encrypt_user_data(user_id: str, data_key: bytes) -> int:
    """Encrypt any plaintext rows for user_id. Returns count of rows migrated."""
    count = 0
    with get_db() as conn:
        # Services
        rows = conn.execute(
            "SELECT * FROM services WHERE user_id = ? AND enc_data IS NULL", (user_id,)
        ).fetchall()
        for row in rows:
            d = dict(row)
            payload = {k: d[k] for k in _SENS_SERVICE if k in d and d[k] is not None}
            enc_data, enc_nonce = encrypt_fields(data_key, payload)
            ph = {k: _PLACEHOLDERS.get(k, d[k]) for k in _SENS_SERVICE if k in d}
            conn.execute(
                "UPDATE services SET enc_data=?, enc_nonce=?, "
                "name=?, amount=?, category=?, payment_method=?, notes=?, "
                "credit_limit=?, outstanding_balance=?, statement_amount=? "
                "WHERE id=?",
                (enc_data, enc_nonce,
                 ph.get("name", d.get("name")),
                 ph.get("amount", d.get("amount", 0.0)),
                 ph.get("category", d.get("category")),
                 ph.get("payment_method", d.get("payment_method", "")),
                 ph.get("notes", d.get("notes", "")),
                 ph.get("credit_limit", d.get("credit_limit")),
                 ph.get("outstanding_balance", d.get("outstanding_balance", 0.0)),
                 ph.get("statement_amount", d.get("statement_amount", 0.0)),
                 d["id"])
            )
            count += 1

        # Investments
        rows = conn.execute(
            "SELECT * FROM investments WHERE user_id = ? AND enc_data IS NULL", (user_id,)
        ).fetchall()
        for row in rows:
            d = dict(row)
            payload = {k: d[k] for k in _SENS_INVESTMENT if k in d and d[k] is not None}
            enc_data, enc_nonce = encrypt_fields(data_key, payload)
            ph = {k: _PLACEHOLDERS.get(k, d[k]) for k in _SENS_INVESTMENT if k in d}
            conn.execute(
                "UPDATE investments SET enc_data=?, enc_nonce=?, "
                "name=?, category=?, current_value=?, invested_amount=?, institution=?, notes=? "
                "WHERE id=?",
                (enc_data, enc_nonce,
                 ph.get("name", d.get("name")),
                 ph.get("category", d.get("category")),
                 ph.get("current_value", d.get("current_value", 0.0)),
                 ph.get("invested_amount", d.get("invested_amount", 0.0)),
                 ph.get("institution", d.get("institution", "")),
                 ph.get("notes", d.get("notes", "")),
                 d["id"])
            )
            count += 1

        # Paid log
        rows = conn.execute(
            "SELECT * FROM paid_log WHERE user_id = ? AND enc_data IS NULL", (user_id,)
        ).fetchall()
        for row in rows:
            d = dict(row)
            payload = {k: d[k] for k in _SENS_PAID_LOG if k in d and d[k] is not None}
            enc_data, enc_nonce = encrypt_fields(data_key, payload)
            ph = {k: _PLACEHOLDERS.get(k, d[k]) for k in _SENS_PAID_LOG if k in d}
            conn.execute(
                "UPDATE paid_log SET enc_data=?, enc_nonce=?, service_name=?, amount=?, category=? WHERE id=?",
                (enc_data, enc_nonce,
                 ph.get("service_name", d.get("service_name")),
                 ph.get("amount", d.get("amount", 0.0)),
                 ph.get("category", d.get("category")),
                 d["id"])
            )
            count += 1

    return count
