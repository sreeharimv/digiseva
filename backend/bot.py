import logging
import os
from datetime import date, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logger = logging.getLogger(__name__)

_pending_paid_session: dict = {}  # chat_id -> list of pending service names


def _build_summary_text(services) -> str:
    today = date.today()
    seven_days = today + timedelta(days=7)
    monthly_total = 0.0
    pending = []
    overdue = []

    for s in services:
        if not s.active:
            continue
        if s.cycle == "monthly":
            monthly_total += s.amount
        elif s.cycle == "weekly":
            monthly_total += s.amount * 4
        elif s.cycle == "quarterly":
            monthly_total += s.amount / 3
        elif s.cycle == "half-yearly":
            monthly_total += s.amount / 6
        elif s.cycle == "yearly":
            monthly_total += s.amount / 12

        if s.paid_current_cycle:
            continue
        try:
            due = date.fromisoformat(s.next_due)
        except ValueError:
            continue
        if due < today:
            overdue.append(s)
        elif due <= seven_days:
            pending.append(s)

    lines = [
        "📊 *DigiSeva Summary*",
        f"Monthly burn: ₹{monthly_total:,.0f}",
        f"Pending (7d): {len(pending)} items",
        f"Overdue: {len(overdue)} items",
    ]
    return "\n".join(lines)


async def cmd_digiseva(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services, update_service, advance_next_due, get_service

    args = context.args or []
    sub = args[0].lower() if args else ""
    services = load_services()
    today = date.today()
    seven_days = today + timedelta(days=7)

    if sub == "":
        await update.message.reply_text(_build_summary_text(services), parse_mode="Markdown")

    elif sub == "due":
        due_items = []
        for s in services:
            if s.paid_current_cycle or not s.active:
                continue
            try:
                d = date.fromisoformat(s.next_due)
            except ValueError:
                continue
            if today <= d <= seven_days:
                diff = (d - today).days
                label = "today" if diff == 0 else f"in {diff}d"
                due_items.append(f"• {s.name} — ₹{s.amount:,.0f} ({label})")
        msg = "⏳ *Due in next 7 days:*\n" + ("\n".join(due_items) if due_items else "Nothing due soon.")
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif sub == "overdue":
        items = []
        for s in services:
            if s.paid_current_cycle or not s.active:
                continue
            try:
                d = date.fromisoformat(s.next_due)
            except ValueError:
                continue
            if d < today:
                items.append(f"• {s.name} — ₹{s.amount:,.0f} (due {s.next_due})")
        msg = "🔴 *Overdue items:*\n" + ("\n".join(items) if items else "No overdue items.")
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif sub == "monthly":
        from collections import defaultdict
        breakdown = defaultdict(float)
        for s in services:
            if not s.active:
                continue
            if s.cycle == "monthly":
                breakdown[s.category] += s.amount
            elif s.cycle == "weekly":
                breakdown[s.category] += s.amount * 4
            elif s.cycle == "quarterly":
                breakdown[s.category] += s.amount / 3
            elif s.cycle == "half-yearly":
                breakdown[s.category] += s.amount / 6
            elif s.cycle == "yearly":
                breakdown[s.category] += s.amount / 12
        lines = ["📅 *Monthly breakdown by category:*"]
        for cat, amt in sorted(breakdown.items(), key=lambda x: -x[1]):
            lines.append(f"  {cat}: ₹{amt:,.0f}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    elif sub == "paid":
        pending = [s for s in services if not s.paid_current_cycle and s.active]
        if not pending:
            await update.message.reply_text("No pending items to mark paid.")
            return
        chat_id = str(update.effective_chat.id)
        _pending_paid_session[chat_id] = {s.name.lower(): s.id for s in pending}
        names = "\n".join(f"• {s.name}" for s in pending)
        await update.message.reply_text(
            f"Pending items:\n{names}\n\nReply with the name to mark as paid.",
            parse_mode="Markdown"
        )

    elif sub == "export":
        from storage import get_data_path
        data_path = get_data_path()
        try:
            with open(data_path, "rb") as f:
                await update.message.reply_document(f, filename="digiseva.json")
        except FileNotFoundError:
            await update.message.reply_text("No data file found.")

    else:
        await update.message.reply_text(
            "Unknown command. Try:\n/digiseva\n/digiseva due\n/digiseva overdue\n"
            "/digiseva paid\n/digiseva monthly\n/digiseva export"
        )


async def handle_paid_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services, update_service, advance_next_due

    chat_id = str(update.effective_chat.id)
    if chat_id not in _pending_paid_session:
        return

    name_map = _pending_paid_session[chat_id]
    text = update.message.text.strip().lower()

    if text not in name_map:
        await update.message.reply_text(f"'{update.message.text}' not found in pending list. Try again or /digiseva.")
        return

    service_id = name_map[text]
    services = load_services()
    for i, s in enumerate(services):
        if s.id == service_id:
            new_due = advance_next_due(s)
            s.paid_current_cycle = True
            s.next_due = new_due
            services[i] = s
            from storage import save_services
            save_services(services)
            del _pending_paid_session[chat_id]
            await update.message.reply_text(f"✅ {s.name} marked as paid. Next due: {new_due}")
            return


def create_bot_app(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("digiseva", cmd_digiseva))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_paid_reply))
    return app
