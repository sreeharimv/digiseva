import asyncio
import csv
import io
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from models import Service, ServiceCreate, ServiceUpdate
from storage import (
    load_services,
    save_services,
    add_service,
    get_service,
    update_service,
    delete_service,
    advance_next_due,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

_bot_app = None
_bot_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_app, _bot_task

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


@app.get("/api/services")
def list_services(
    type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
):
    services = load_services()
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


@app.post("/api/services/{service_id}/paid")
def toggle_paid(service_id: str):
    service = get_service(service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    if service.paid_current_cycle:
        updated = update_service(service_id, {"paid_current_cycle": False})
    else:
        new_due = advance_next_due(service)
        updated = update_service(service_id, {"paid_current_cycle": True, "next_due": new_due})

    return updated.model_dump()


@app.get("/api/summary")
def summary():
    services = load_services()
    today = date.today()
    seven_days = today + timedelta(days=7)

    monthly_total = 0.0
    upcoming = []
    overdue = []
    paid_count = 0

    for s in services:
        if not s.active:
            continue

        if s.cycle == "monthly":
            monthly_total += s.amount
        elif s.cycle == "weekly":
            monthly_total += s.amount * 4
        elif s.cycle == "bi-monthly":
            monthly_total += s.amount / 2
        elif s.cycle == "quarterly":
            monthly_total += s.amount / 3
        elif s.cycle == "half-yearly":
            monthly_total += s.amount / 6
        elif s.cycle == "yearly":
            monthly_total += s.amount / 12

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
        "monthly_total": round(monthly_total, 2),
        "upcoming": upcoming,
        "overdue": overdue,
        "paid_count": paid_count,
        "total_count": len([s for s in services if s.active]),
    }


@app.get("/api/export/csv")
def export_csv():
    services = load_services()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "name", "type", "category", "amount", "currency",
        "cycle", "next_due", "payment_method", "auto_debit",
        "paid_current_cycle", "notes", "active", "created_at"
    ])
    for s in services:
        d = s.model_dump()
        writer.writerow([d[k] for k in [
            "id", "name", "type", "category", "amount", "currency",
            "cycle", "next_due", "payment_method", "auto_debit",
            "paid_current_cycle", "notes", "active", "created_at"
        ]])
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

    services = load_services()
    existing_ids = {s.id for s in services}

    created, updated, skipped = 0, 0, 0
    errors = []

    for i, row in enumerate(reader, start=2):
        try:
            row_id = row.get("id", "").strip()
            payload = {
                "name":               row["name"].strip(),
                "type":               row["type"].strip(),
                "category":           row["category"].strip(),
                "amount":             float(row["amount"]),
                "currency":           row.get("currency", "INR").strip() or "INR",
                "cycle":              row["cycle"].strip(),
                "next_due":           row["next_due"].strip(),
                "payment_method":     row.get("payment_method", "").strip(),
                "auto_debit":         str(row.get("auto_debit", "false")).lower() in ("true", "1", "yes"),
                "paid_current_cycle": str(row.get("paid_current_cycle", "false")).lower() in ("true", "1", "yes"),
                "notes":              row.get("notes", "").strip(),
                "active":             str(row.get("active", "true")).lower() not in ("false", "0", "no"),
            }

            if row_id and row_id in existing_ids:
                # update existing
                for j, s in enumerate(services):
                    if s.id == row_id:
                        updated_data = s.model_dump()
                        updated_data.update(payload)
                        services[j] = Service(**updated_data)
                        break
                updated += 1
            else:
                # create new
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
    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors}


app.mount("/", StaticFiles(directory="/app/static", html=True), name="static")
