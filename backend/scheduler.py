import asyncio
import logging
from datetime import date, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")


def get_alert_message(services) -> str | None:
    today = date.today()
    three_days = today + timedelta(days=3)

    due_soon = []
    overdue = []

    for s in services:
        if not s.active or s.paid_current_cycle:
            continue
        try:
            due = date.fromisoformat(s.next_due)
        except ValueError:
            continue
        if due < today:
            overdue.append(s)
        elif due <= three_days:
            due_soon.append(s)

    if not due_soon and not overdue:
        return None

    lines = ["🔔 *DigiSeva Daily Alert*\n"]

    if overdue:
        lines.append("🔴 *Overdue:*")
        for s in overdue:
            lines.append(f"  • {s.name} — ₹{s.amount:,.0f} (was due {s.next_due})")

    if due_soon:
        lines.append("\n⏳ *Due within 3 days:*")
        for s in due_soon:
            diff = (date.fromisoformat(s.next_due) - today).days
            label = "today" if diff == 0 else f"in {diff}d"
            lines.append(f"  • {s.name} — ₹{s.amount:,.0f} ({label})")

    return "\n".join(lines)


def start_scheduler(bot_app, chat_id: str):
    from storage import load_services

    async def daily_alert():
        try:
            services = load_services()
            msg = get_alert_message(services)
            if msg:
                await bot_app.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.error(f"Scheduler alert failed: {e}")

    scheduler.add_job(daily_alert, CronTrigger(hour=9, minute=0, timezone="Asia/Kolkata"))
    scheduler.start()
    logger.info("APScheduler started — daily alert at 09:00 IST")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
