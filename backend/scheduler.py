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
    from storage import load_services, auto_mark_paid, get_linked_users

    async def daily_alert():
        try:
            from auth import get_scheduler_data_key

            linked_users = get_linked_users()
            if not linked_users:
                return  # no linked Telegram accounts, nothing to do

            for user in linked_users:
                try:
                    uid      = user["id"]
                    uchat_id = user["telegram_chat_id"]
                    dk       = get_scheduler_data_key(user)
                    if not dk:
                        continue  # scheduler key not set for this user

                    auto_marked = auto_mark_paid(user_id=uid)
                    services    = load_services(uid, data_key=dk)
                    alert_msg   = get_alert_message(services)

                    parts = []
                    if auto_marked:
                        lines = ["✅ *Auto-marked as paid:*"]
                        for s in auto_marked:
                            lines.append(f"  • {s.name} — ₹{s.amount:,.0f} (next due: {s.next_due})")
                        parts.append("\n".join(lines))
                    if alert_msg:
                        parts.append(alert_msg)

                    if parts:
                        await bot_app.bot.send_message(
                            chat_id=uchat_id,
                            text="\n\n".join(parts),
                            parse_mode="Markdown",
                        )
                except Exception as e:
                    logger.error(f"Scheduler alert failed for user {user.get('username')}: {e}")

        except Exception as e:
            logger.error(f"Scheduler daily_alert outer error: {e}")

    scheduler.add_job(daily_alert, CronTrigger(hour=9, minute=0, timezone="Asia/Kolkata"))
    scheduler.start()
    logger.info("APScheduler started — daily alert at 09:00 IST")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
