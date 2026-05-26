import json
import os
from datetime import date, datetime, timedelta
from typing import List, Optional
from models import Service

DATA_PATH = os.environ.get("DATA_PATH", "/app/data/digiseva.json")


def _load_raw() -> dict:
    if not os.path.exists(DATA_PATH):
        os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
        _save_raw({"services": []})
        return {"services": []}
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_raw(data: dict) -> None:
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _reset_overdue(services: List[dict]) -> List[dict]:
    today = date.today()
    for s in services:
        if not s.get("paid_current_cycle", False):
            continue
        try:
            due = date.fromisoformat(s["next_due"])
            if today > due:
                s["paid_current_cycle"] = False
        except (ValueError, KeyError):
            pass
    return services


def load_services() -> List[Service]:
    raw = _load_raw()
    services = _reset_overdue(raw.get("services", []))
    _save_raw({"services": services})
    return [Service(**s) for s in services]


def save_services(services: List[Service]) -> None:
    _save_raw({"services": [s.model_dump() for s in services]})


def get_service(service_id: str) -> Optional[Service]:
    for s in load_services():
        if s.id == service_id:
            return s
    return None


def add_service(service: Service) -> Service:
    services = load_services()
    services.append(service)
    save_services(services)
    return service


def update_service(service_id: str, updates: dict) -> Optional[Service]:
    services = load_services()
    for i, s in enumerate(services):
        if s.id == service_id:
            updated = s.model_dump()
            updated.update({k: v for k, v in updates.items() if v is not None})
            services[i] = Service(**updated)
            save_services(services)
            return services[i]
    return None


def delete_service(service_id: str) -> bool:
    services = load_services()
    filtered = [s for s in services if s.id != service_id]
    if len(filtered) == len(services):
        return False
    save_services(filtered)
    return True


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
        import calendar
        day = min(current.day, calendar.monthrange(year, month)[1])
        next_due = date(year, month, day)
    elif cycle == "bi-monthly":
        month = current.month + 2
        year = current.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        import calendar
        day = min(current.day, calendar.monthrange(year, month)[1])
        next_due = date(year, month, day)
    elif cycle == "quarterly":
        month = current.month + 3
        year = current.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        import calendar
        day = min(current.day, calendar.monthrange(year, month)[1])
        next_due = date(year, month, day)
    elif cycle == "half-yearly":
        month = current.month + 6
        year = current.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        import calendar
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


def get_data_path() -> str:
    return DATA_PATH
