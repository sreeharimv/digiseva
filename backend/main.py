import csv
import io
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from models import (
    Service, ServiceCreate, ServiceUpdate, PaymentRecord,
    Investment, InvestmentCreate, InvestmentUpdate,
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
)

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
# Services CRUD
# ---------------------------------------------------------------------------

@app.get("/api/services")
def list_services(
    type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    include_inactive: bool = Query(False),
):
    services = load_services(include_inactive=include_inactive)
    if type:
        services = [s for s in services if s.type == type]
    if category:
        services = [s for s in services if s.category == category]
    return [s.model_dump() for s in services]


@app.post("/api/services", status_code=201)
def create_service(data: ServiceCreate):
    service = Service(**data.model_dump())
    add_service(service)
    return service.model_dump()


@app.put("/api/services/{service_id}")
def edit_service(service_id: str, data: ServiceUpdate):
    updated = update_service(service_id, data.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Service not found")
    return updated.model_dump()


@app.delete("/api/services/{service_id}")
def remove_service(service_id: str):
    if not delete_service(service_id):
        raise HTTPException(status_code=404, detail="Service not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Paid toggle
# ---------------------------------------------------------------------------

@app.post("/api/services/{service_id}/paid")
def toggle_paid(service_id: str):
    service = get_service(service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    if service.paid_current_cycle:
        # Untoggle — revert to unpaid for this cycle
        updated = update_service(service_id, {"paid_current_cycle": False})
    else:
        new_due = advance_next_due(service)
        extra: dict = {"paid_current_cycle": True, "next_due": new_due}

        # EMI: track instalments; deactivate when tenure is complete
        if service.tenure_months is not None:
            new_paid = service.paid_instalments + 1
            extra["paid_instalments"] = new_paid
            if new_paid >= service.tenure_months:
                extra["active"] = False

        updated = update_service(service_id, extra)
        # Write to paid log (only when marking as paid, not when untoggling)
        add_paid_log(service)

    return updated.model_dump()


# ---------------------------------------------------------------------------
# Credit card payments
# ---------------------------------------------------------------------------

@app.post("/api/services/{service_id}/payment")
def record_payment(service_id: str, data: PaymentRecord):
    """Record a payment against a credit card's outstanding balance.

    If the payment amount covers the full statement_amount the entry is
    automatically marked paid for this cycle and next_due is advanced.
    """
    service = get_service(service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    if service.category != "Credit Card":
        raise HTTPException(
            status_code=400,
            detail="Payment recording is only supported for Credit Card entries",
        )

    new_outstanding = max(0.0, service.outstanding_balance - data.amount)
    updates: dict = {"outstanding_balance": new_outstanding}

    # Auto-mark paid when the full statement is settled
    if service.statement_amount > 0 and data.amount >= service.statement_amount:
        updates["paid_current_cycle"] = True
        updates["next_due"] = advance_next_due(service)

    updated = update_service(service_id, updates)
    add_payment_history(service_id, data.amount, data.notes)

    return updated.model_dump()


@app.get("/api/services/{service_id}/payment-history")
def payment_history(service_id: str):
    if not get_service(service_id):
        raise HTTPException(status_code=404, detail="Service not found")
    return get_payment_history(service_id)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _monthly_equiv(s: Service) -> float:
    """Convert any billing cycle's amount to a monthly equivalent figure."""
    return {
        "weekly":      s.amount * 4,
        "monthly":     s.amount,
        "bi-monthly":  s.amount / 2,
        "quarterly":   s.amount / 3,
        "half-yearly": s.amount / 6,
        "yearly":      s.amount / 12,
        "one-time":    0.0,
    }.get(s.cycle, 0.0)


@app.get("/api/summary")
def summary():
    services = load_services()
    today = date.today()
    seven_days = today + timedelta(days=7)

    monthly_income = 0.0
    monthly_outgo = 0.0
    upcoming = []
    overdue = []
    paid_count = 0

    for s in services:
        equiv = _monthly_equiv(s)
        if s.type == "income":
            monthly_income += equiv
        else:
            # subscription, bill, expense all count as outgo
            monthly_outgo += equiv

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

    monthly_income = round(monthly_income, 2)
    monthly_outgo = round(monthly_outgo, 2)

    return {
        "monthly_income": monthly_income,
        "monthly_outgo":  monthly_outgo,
        "net_cashflow":   round(monthly_income - monthly_outgo, 2),
        "monthly_total":  monthly_outgo,   # backward-compat alias
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
def export_csv():
    services = load_services(include_inactive=True)  # full backup — include inactive
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
async def import_csv(file: UploadFile = File(...)):
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")  # handle Excel BOM
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    required = {"name", "type", "category", "amount", "cycle", "next_due"}
    if not required.issubset(set(reader.fieldnames or [])):
        raise HTTPException(status_code=400, detail=f"CSV must have columns: {required}")

    services = load_services(include_inactive=True)  # must include inactive to avoid duplicate inserts
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

    save_services(services)
    return {"created": created, "updated": updated_count, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# Paid history
# ---------------------------------------------------------------------------

@app.get("/api/history")
def history_months():
    """Return list of months with payment totals."""
    return get_history_months()


@app.get("/api/history/{year_month}")
def history_month(year_month: str):
    """Return all paid_log entries for a YYYY-MM month."""
    return get_month_log(year_month)


# ---------------------------------------------------------------------------
# Investments
# ---------------------------------------------------------------------------

@app.get("/api/investments")
def list_investments_endpoint():
    return load_investments()


@app.post("/api/investments", status_code=201)
def create_investment(data: InvestmentCreate):
    inv = Investment(**data.model_dump())
    return add_investment(inv.model_dump())


@app.put("/api/investments/{inv_id}")
def edit_investment(inv_id: str, data: InvestmentUpdate):
    updates = data.model_dump(exclude_none=True)
    updates["last_updated"] = datetime.now().isoformat()
    result = update_investment(inv_id, updates)
    if not result:
        raise HTTPException(status_code=404, detail="Investment not found")
    return result


@app.delete("/api/investments/{inv_id}")
def remove_investment(inv_id: str):
    if not delete_investment(inv_id):
        raise HTTPException(status_code=404, detail="Investment not found")
    return {"ok": True}


app.mount("/", StaticFiles(directory="/app/static", html=True), name="static")
