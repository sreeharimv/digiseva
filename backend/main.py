import csv
import io
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile, File, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from models import (
    Service, ServiceCreate, ServiceUpdate, PaymentRecord,
    Investment, InvestmentCreate, InvestmentUpdate,
    UserCreate, UserLogin, TokenResponse,
)
from storage import (
    load_services,
    save_services,
    add_service,
    get_service,
    update_service,
    delete_service,
    advance_next_due,
    add_payment_history,
    get_payment_history,
    add_paid_log,
    get_history_months,
    get_month_log,
    load_investments,
    get_investment,
    add_investment,
    update_investment,
    delete_investment,
    create_user,
    get_user_by_username,
    get_user_by_id,
    get_user_count,
    update_user,
)
from auth import (
    hash_pin,
    verify_pin,
    create_access_token,
    get_current_user,
    is_rate_limited,
    record_failed_attempt,
    create_data_key,
    unlock_data_key,
)
from storage import migrate_encrypt_user_data
from fastapi import BackgroundTasks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

_bot_app = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_app

    # --- Database init + one-time JSON → SQLite migration ---
    from database import init_db
    init_db()
    try:
        from migrate import run_migration
        n = run_migration()
        if n:
            logger.info(f"Auto-migration: moved {n} service(s) from JSON to SQLite")
    except Exception as e:
        logger.warning(f"Migration check failed ({e}) — continuing")

    # --- Telegram bot ---
    _placeholder = {"your_telegram_bot_token", "your_telegram_chat_id", ""}
    if BOT_TOKEN not in _placeholder and CHAT_ID not in _placeholder:
        try:
            from bot import create_bot_app
            from scheduler import start_scheduler

            _bot_app = create_bot_app(BOT_TOKEN)
            await _bot_app.initialize()
            await _bot_app.start()
            await _bot_app.updater.start_polling(drop_pending_updates=True)
            logger.info("Telegram bot started")

            start_scheduler(_bot_app, CHAT_ID)
        except Exception as e:
            logger.warning(f"Telegram bot failed to start ({e}) — running without bot")
            _bot_app = None
    else:
        logger.warning("BOT_TOKEN/CHAT_ID not configured — Telegram bot disabled")

    yield

    if _bot_app:
        from scheduler import stop_scheduler
        stop_scheduler()
        await _bot_app.updater.stop()
        await _bot_app.stop()
        await _bot_app.shutdown()
        logger.info("Telegram bot stopped")


app = FastAPI(title="DigiSeva", lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS — allow GitHub Pages frontend to call the API via Cloudflare tunnel
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://sreeharimv.github.io", "http://localhost:8200"],
    allow_credentials=False,   # JWT in Authorization header, not cookies
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health check (used by tunnel monitor script)
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

INVITE_CODE = os.environ.get("INVITE_CODE", "")


@app.post("/api/auth/register", response_model=TokenResponse, status_code=201)
def register(data: UserCreate, request: Request):
    # Validate PIN format
    if not re.fullmatch(r"\d{6}", data.pin):
        raise HTTPException(status_code=400, detail="PIN must be exactly 6 digits")

    # Validate username
    username = data.username.strip()
    if not username or len(username) > 32:
        raise HTTPException(status_code=400, detail="Username must be 1–32 characters")

    # Check invite code (if configured)
    if INVITE_CODE and data.invite_code != INVITE_CODE:
        raise HTTPException(status_code=403, detail="Invalid invite code")

    # Check duplicate username
    if get_user_by_username(username):
        raise HTTPException(status_code=409, detail="Username already taken")

    uid = str(uuid.uuid4())
    data_key, enc_dk, nonce_dk, sched_enc, sched_nonce = create_data_key(data.pin, uid)

    user = {
        "id":                      uid,
        "username":                username,
        "pin_hash":                hash_pin(data.pin),
        "encrypted_data_key":      enc_dk,
        "key_nonce":               nonce_dk,
        "created_at":              datetime.now().isoformat(),
    }
    create_user(user)
    if sched_enc:
        update_user(uid, {"scheduler_encrypted_key": sched_enc,
                          "scheduler_key_nonce":     sched_nonce})

    token = create_access_token(uid, username, data_key)
    return TokenResponse(access_token=token, username=username)


@app.post("/api/auth/login", response_model=TokenResponse)
def login(data: UserLogin, request: Request, background_tasks: BackgroundTasks):
    ip = request.client.host
    if is_rate_limited(ip):
        raise HTTPException(status_code=429,
                            detail="Too many failed attempts. Try again in 15 minutes.")

    user = get_user_by_username(data.username.strip())
    if not user or not verify_pin(data.pin, user["pin_hash"]):
        record_failed_attempt(ip)
        raise HTTPException(status_code=401, detail="Invalid username or PIN")

    data_key = unlock_data_key(data.pin, user)
    if data_key is None and not user.get("encrypted_data_key"):
        # Old account without a data_key — generate one now
        dk, enc_dk, nonce_dk, sched_enc, sched_nonce = create_data_key(data.pin, user["id"])
        data_key = dk
        update_user(user["id"], {"encrypted_data_key": enc_dk, "key_nonce": nonce_dk,
                                 "scheduler_encrypted_key": sched_enc,
                                 "scheduler_key_nonce": sched_nonce})

    if data_key:
        # Migrate existing plaintext data in the background
        background_tasks.add_task(migrate_encrypt_user_data, user["id"], data_key)

    token = create_access_token(user["id"], user["username"], data_key)
    return TokenResponse(access_token=token, username=user["username"])


@app.get("/api/auth/me")
def me(current_user: dict = Depends(get_current_user)):
    return {"user_id": current_user["user_id"], "username": current_user["username"]}


@app.get("/api/auth/status")
def auth_status():
    """Returns whether any users exist — frontend uses this to show login vs register."""
    return {"has_users": get_user_count() > 0}


# ---------------------------------------------------------------------------
# Services CRUD
# ---------------------------------------------------------------------------

@app.get("/api/services")
def list_services(
    type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    include_inactive: bool = Query(False),
    current_user: dict = Depends(get_current_user),
):
    uid = current_user["user_id"]
    dk = current_user.get("data_key")
    services = load_services(uid, include_inactive=include_inactive, data_key=dk)
    if type:
        services = [s for s in services if s.type == type]
    if category:
        services = [s for s in services if s.category == category]
    return [s.model_dump() for s in services]


@app.post("/api/services", status_code=201)
def create_service(data: ServiceCreate, current_user: dict = Depends(get_current_user)):
    service = Service(**data.model_dump())
    add_service(service, current_user["user_id"], current_user.get("data_key"))
    return service.model_dump()


@app.put("/api/services/{service_id}")
def edit_service(service_id: str, data: ServiceUpdate, current_user: dict = Depends(get_current_user)):
    updated = update_service(service_id, data.model_dump(exclude_none=True), current_user["user_id"], current_user.get("data_key"))
    if not updated:
        raise HTTPException(status_code=404, detail="Service not found")
    return updated.model_dump()


@app.delete("/api/services/{service_id}")
def remove_service(service_id: str, current_user: dict = Depends(get_current_user)):
    if not delete_service(service_id, current_user["user_id"]):
        raise HTTPException(status_code=404, detail="Service not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Paid toggle
# ---------------------------------------------------------------------------

@app.post("/api/services/{service_id}/paid")
def toggle_paid(service_id: str, current_user: dict = Depends(get_current_user)):
    uid = current_user["user_id"]
    dk = current_user.get("data_key")
    service = get_service(service_id, uid, dk)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    if service.paid_current_cycle:
        updated = update_service(service_id, {"paid_current_cycle": False}, uid, dk)
    else:
        new_due = advance_next_due(service)
        extra: dict = {"paid_current_cycle": True, "next_due": new_due}

        if service.tenure_months is not None:
            new_paid = service.paid_instalments + 1
            extra["paid_instalments"] = new_paid
            if new_paid >= service.tenure_months:
                extra["active"] = False

        updated = update_service(service_id, extra, uid, dk)
        add_paid_log(service, uid, dk)

    return updated.model_dump()


# ---------------------------------------------------------------------------
# Credit card payments
# ---------------------------------------------------------------------------

@app.post("/api/services/{service_id}/payment")
def record_payment(service_id: str, data: PaymentRecord, current_user: dict = Depends(get_current_user)):
    uid = current_user["user_id"]
    service = get_service(service_id, uid)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    if service.category != "Credit Card":
        raise HTTPException(status_code=400, detail="Payment recording is only supported for Credit Card entries")

    new_outstanding = max(0.0, service.outstanding_balance - data.amount)
    updates: dict = {"outstanding_balance": new_outstanding}

    if service.statement_amount > 0 and data.amount >= service.statement_amount:
        updates["paid_current_cycle"] = True
        updates["next_due"] = advance_next_due(service)

    updated = update_service(service_id, updates, uid)
    add_payment_history(service_id, data.amount, uid, data.notes)
    return updated.model_dump()


@app.get("/api/services/{service_id}/payment-history")
def payment_history(service_id: str, current_user: dict = Depends(get_current_user)):
    uid = current_user["user_id"]
    if not get_service(service_id, uid):
        raise HTTPException(status_code=404, detail="Service not found")
    return get_payment_history(service_id, uid)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _monthly_equiv(s: Service) -> float:
    """Normalised monthly equivalent — for avg burn-rate display only."""
    return {
        "weekly":      s.amount * 4,
        "monthly":     s.amount,
        "bi-monthly":  s.amount / 2,
        "quarterly":   s.amount / 3,
        "half-yearly": s.amount / 6,
        "yearly":      s.amount / 12,
        "one-time":    0.0,
    }.get(s.cycle, 0.0)


def _due_this_month(s: Service, today: date) -> bool:
    """True if this service's billing cycle falls in the current calendar month.

    Three cases:
    1. next_due is in this month (paid or not) — always counts.
    2. Unpaid and overdue from a prior month — still counts against this month.
    3. Paid and next_due already advanced past this month — reverse one cycle
       to recover the original due date and check if it landed this month.
    """
    cy, cm = today.year, today.month
    try:
        nxt = date.fromisoformat(s.next_due)
    except ValueError:
        return False

    # Case 1: next_due is still in the current month (paid or unpaid)
    if nxt.year == cy and nxt.month == cm:
        return True

    # Case 2: unpaid and overdue from a previous month
    if not s.paid_current_cycle and nxt < today.replace(day=1):
        return True

    # Case 3: paid — next_due was already advanced; reverse one cycle
    if s.paid_current_cycle:
        try:
            if s.cycle == "weekly":
                prev = nxt - timedelta(weeks=1)
            elif s.cycle == "monthly":
                pm = nxt.month - 1 if nxt.month > 1 else 12
                py = nxt.year if nxt.month > 1 else nxt.year - 1
                prev = nxt.replace(year=py, month=pm)
            elif s.cycle == "bi-monthly":
                m, y = nxt.month - 2, nxt.year
                if m <= 0: m += 12; y -= 1
                prev = nxt.replace(year=y, month=m)
            elif s.cycle == "quarterly":
                m, y = nxt.month - 3, nxt.year
                if m <= 0: m += 12; y -= 1
                prev = nxt.replace(year=y, month=m)
            elif s.cycle == "half-yearly":
                m, y = nxt.month - 6, nxt.year
                if m <= 0: m += 12; y -= 1
                prev = nxt.replace(year=y, month=m)
            elif s.cycle == "yearly":
                prev = nxt.replace(year=nxt.year - 1)
            else:
                return False
            return prev.year == cy and prev.month == cm
        except ValueError:
            return False

    return False


@app.get("/api/summary")
def summary(current_user: dict = Depends(get_current_user)):
    services = load_services(current_user["user_id"], data_key=current_user.get("data_key"))
    today = date.today()
    seven_days = today + timedelta(days=7)

    avg_income = 0.0      # normalised monthly average
    avg_outgo = 0.0
    this_month_income = 0.0   # actual amounts due/paid this calendar month
    this_month_outgo = 0.0
    upcoming = []
    overdue = []
    paid_count = 0

    for s in services:
        equiv = _monthly_equiv(s)
        if s.type == "income":
            avg_income += equiv
        else:
            avg_outgo += equiv

        if _due_this_month(s, today):
            if s.type == "income":
                this_month_income += s.amount
            else:
                this_month_outgo += s.amount

        if s.paid_current_cycle:
            paid_count += 1
            continue

        try:
            due = date.fromisoformat(s.next_due)
        except ValueError:
            continue

        d = s.model_dump()
        if due < today:
            d["days_overdue"] = (today - due).days
            overdue.append(d)
        elif due <= seven_days:
            d["days_until_due"] = (due - today).days
            upcoming.append(d)

    upcoming.sort(key=lambda x: x["next_due"])
    overdue.sort(key=lambda x: x["next_due"])

    return {
        # This month's actual figures (primary)
        "this_month_income": round(this_month_income, 2),
        "this_month_outgo":  round(this_month_outgo, 2),
        "this_month_net":    round(this_month_income - this_month_outgo, 2),
        # Normalised monthly averages (secondary — useful for /monthly breakdown)
        "monthly_income": round(avg_income, 2),
        "monthly_outgo":  round(avg_outgo, 2),
        "net_cashflow":   round(avg_income - avg_outgo, 2),
        "monthly_total":  round(avg_outgo, 2),  # backward-compat alias
        "upcoming":       upcoming,
        "overdue":        overdue,
        "paid_count":     paid_count,
        "total_count":    len(services),  # load_services() already filters active-only
    }


# ---------------------------------------------------------------------------
# CSV export / import
# ---------------------------------------------------------------------------

_CSV_FIELDS = [
    "id", "name", "type", "category", "amount", "currency",
    "cycle", "next_due", "payment_method", "auto_debit",
    "paid_current_cycle", "notes", "active", "created_at",
    "tenure_months", "paid_instalments",
    "credit_limit", "outstanding_balance", "statement_amount",
]


@app.get("/api/export/csv")
def export_csv(current_user: dict = Depends(get_current_user)):
    services = load_services(current_user["user_id"], include_inactive=True, data_key=current_user.get("data_key"))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(_CSV_FIELDS)
    for s in services:
        d = s.model_dump()
        writer.writerow([d[k] for k in _CSV_FIELDS])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=digiseva.csv"},
    )


@app.post("/api/import/csv")
async def import_csv(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    uid = current_user["user_id"]
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")  # handle Excel BOM
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    required = {"name", "type", "category", "amount", "cycle", "next_due"}
    if not required.issubset(set(reader.fieldnames or [])):
        raise HTTPException(status_code=400, detail=f"CSV must have columns: {required}")

    services = load_services(uid, include_inactive=True, data_key=current_user.get("data_key"))
    existing_ids = {s.id for s in services}

    created, updated_count, skipped = 0, 0, 0
    errors = []

    for i, row in enumerate(reader, start=2):
        try:
            row_id = row.get("id", "").strip()

            def _bool(key, default="false"):
                return str(row.get(key, default)).lower() in ("true", "1", "yes")

            def _opt_int(key):
                v = row.get(key, "").strip()
                return int(v) if v else None

            def _opt_float(key):
                v = row.get(key, "").strip()
                return float(v) if v else None

            payload = {
                "name":                row["name"].strip(),
                "type":                row["type"].strip(),
                "category":            row["category"].strip(),
                "amount":              float(row["amount"]),
                "currency":            row.get("currency", "INR").strip() or "INR",
                "cycle":               row["cycle"].strip(),
                "next_due":            row["next_due"].strip(),
                "payment_method":      row.get("payment_method", "").strip(),
                "auto_debit":          _bool("auto_debit"),
                "paid_current_cycle":  _bool("paid_current_cycle"),
                "notes":               row.get("notes", "").strip(),
                "active":              str(row.get("active", "true")).lower() not in ("false", "0", "no"),
                "tenure_months":       _opt_int("tenure_months"),
                "paid_instalments":    int(row.get("paid_instalments", 0) or 0),
                "credit_limit":        _opt_float("credit_limit"),
                "outstanding_balance": float(row.get("outstanding_balance", 0) or 0),
                "statement_amount":    float(row.get("statement_amount", 0) or 0),
            }

            if row_id and row_id in existing_ids:
                for j, s in enumerate(services):
                    if s.id == row_id:
                        updated_data = s.model_dump()
                        updated_data.update(payload)
                        services[j] = Service(**updated_data)
                        break
                updated_count += 1
            else:
                created_at = row.get("created_at", "").strip()
                new_service = Service(**payload)
                if created_at:
                    new_service.created_at = created_at
                if row_id:
                    new_service.id = row_id
                services.append(new_service)
                existing_ids.add(new_service.id)
                created += 1

        except Exception as e:
            errors.append(f"Row {i}: {e}")
            skipped += 1

    save_services(services, uid, current_user.get("data_key"))
    return {"created": created, "updated": updated_count, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# Paid history
# ---------------------------------------------------------------------------

@app.get("/api/history")
def history_months(current_user: dict = Depends(get_current_user)):
    return get_history_months(current_user["user_id"])


@app.get("/api/history/{year_month}")
def history_month(year_month: str, current_user: dict = Depends(get_current_user)):
    return get_month_log(year_month, current_user["user_id"])


# ---------------------------------------------------------------------------
# Investments
# ---------------------------------------------------------------------------

@app.get("/api/investments")
def list_investments_endpoint(current_user: dict = Depends(get_current_user)):
    return load_investments(current_user["user_id"], current_user.get("data_key"))


@app.post("/api/investments", status_code=201)
def create_investment(data: InvestmentCreate, current_user: dict = Depends(get_current_user)):
    inv = Investment(**data.model_dump())
    return add_investment(inv.model_dump(), current_user["user_id"], current_user.get("data_key"))


@app.put("/api/investments/{inv_id}")
def edit_investment(inv_id: str, data: InvestmentUpdate, current_user: dict = Depends(get_current_user)):
    uid = current_user["user_id"]
    updates = data.model_dump(exclude_none=True)
    updates["last_updated"] = datetime.now().isoformat()
    result = update_investment(inv_id, updates, uid, current_user.get("data_key"))
    if not result:
        raise HTTPException(status_code=404, detail="Investment not found")
    return result


@app.delete("/api/investments/{inv_id}")
def remove_investment(inv_id: str, current_user: dict = Depends(get_current_user)):
    if not delete_investment(inv_id, current_user["user_id"]):
        raise HTTPException(status_code=404, detail="Investment not found")
    return {"ok": True}


app.mount("/", StaticFiles(directory="/app/static", html=True), name="static")
