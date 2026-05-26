import logging
from datetime import date, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logger = logging.getLogger(__name__)

# chat_id -> {index: service_id}
_pending_paid_session: dict = {}

DIV = "─" * 22


def _monthly_equiv(s) -> float:
    if s.cycle == "monthly":      return s.amount
    if s.cycle == "weekly":       return s.amount * 4
    if s.cycle == "quarterly":    return s.amount / 3
    if s.cycle == "half-yearly":  return s.amount / 6
    if s.cycle == "yearly":       return s.amount / 12
    return 0.0


def _status(s) -> str:
    if s.paid_current_cycle:
        return "paid"
    today = date.today()
    try:
        due = date.fromisoformat(s.next_due)
    except ValueError:
        return "unknown"
    diff = (due - today).days
    if diff < 0:   return "overdue"
    if diff <= 7:  return "pending"
    return "upcoming"


def _due_label(next_due: str) -> str:
    try:
        due = date.fromisoformat(next_due)
    except ValueError:
        return next_due
    diff = (due - date.today()).days
    day_str = due.strftime("%-d %b")
    if diff < 0:   return f"{day_str} 🔴 ({abs(diff)}d overdue)"
    if diff == 0:  return f"{day_str} ⚠️ (today!)"
    if diff == 1:  return f"{day_str} (tomorrow)"
    return f"{day_str} (in {diff}d)"


def _payment_line(s) -> str:
    if not s.payment_method:
        return ""
    auto = " 🔁 Auto" if s.auto_debit else ""
    return f"   💳 {s.payment_method}{auto}\n"


# ── /digiseva ──────────────────────────────────────────────────
def _build_summary(services) -> str:
    today = date.today()
    seven = today + timedelta(days=7)
    monthly = 0.0
    active = [s for s in services if s.active]
    subs = [s for s in active if s.type == "subscription"]
    bills = [s for s in active if s.type == "bill"]
    paid_list, pending_list, overdue_list = [], [], []

    for s in active:
        monthly += _monthly_equiv(s)
        st = _status(s)
        if st == "paid":    paid_list.append(s)
        elif st == "overdue": overdue_list.append(s)
        elif st == "pending": pending_list.append(s)

    lines = [
        "📊 *DigiSeva Overview*",
        DIV,
        f"💰 *Monthly burn:* ₹{monthly:,.0f}",
        "",
        f"📦 *Active:* {len(active)}  ({len(subs)} subscriptions · {len(bills)} bills)",
        "",
        f"✅ Paid this cycle:  {len(paid_list)}",
        f"⏳ Due in 7 days:    {len(pending_list)}",
        f"🔴 Overdue:          {len(overdue_list)}",
    ]

    if overdue_list:
        lines += ["", "⚠️ *Overdue items:*"]
        for s in overdue_list:
            lines.append(f"  • {s.name} — ₹{s.amount:,.0f}  ({s.next_due})")

    if pending_list:
        lines += ["", "📌 *Coming up:*"]
        for s in pending_list:
            diff = (date.fromisoformat(s.next_due) - today).days
            label = "today" if diff == 0 else f"in {diff}d"
            lines.append(f"  • {s.name} — ₹{s.amount:,.0f}  ({label})")

    return "\n".join(lines)


# ── /digiseva due ──────────────────────────────────────────────
def _build_due(services) -> str:
    today = date.today()
    seven = today + timedelta(days=7)
    items = []
    for s in services:
        if not s.active or s.paid_current_cycle:
            continue
        try:
            d = date.fromisoformat(s.next_due)
        except ValueError:
            continue
        if today <= d <= seven:
            items.append((d, s))
    items.sort(key=lambda x: x[0])

    if not items:
        return "✅ Nothing due in the next 7 days."

    lines = [f"⏳ *Due in 7 days*  ({len(items)} items)", DIV, ""]
    nums = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    for i, (d, s) in enumerate(items):
        diff = (d - today).days
        day_lbl = "today ⚠️" if diff == 0 else ("tomorrow" if diff == 1 else f"in {diff}d")
        num = nums[i] if i < len(nums) else f"{i+1}."
        lines.append(f"{num} *{s.name}*  — ₹{s.amount:,.0f}")
        lines.append(f"   {s.category} · {s.cycle}")
        lines.append(_payment_line(s).rstrip())
        lines.append(f"   📅 {d.strftime('%-d %b')}  ({day_lbl})")
        lines.append("")
    return "\n".join(lines).strip()


# ── /digiseva overdue ──────────────────────────────────────────
def _build_overdue(services) -> str:
    today = date.today()
    items = []
    for s in services:
        if not s.active or s.paid_current_cycle:
            continue
        try:
            d = date.fromisoformat(s.next_due)
        except ValueError:
            continue
        if d < today:
            items.append((d, s))
    items.sort(key=lambda x: x[0])

    if not items:
        return "✅ No overdue items. All caught up!"

    lines = [f"🔴 *Overdue*  ({len(items)} items)", DIV, ""]
    for d, s in items:
        days_late = (today - d).days
        lines.append(f"⚠️ *{s.name}*  — ₹{s.amount:,.0f}")
        lines.append(f"   {s.category} · {s.cycle}")
        lines.append(_payment_line(s).rstrip())
        lines.append(f"   📅 Was due {d.strftime('%-d %b')}  ({days_late}d ago)")
        lines.append("")
    return "\n".join(lines).strip()


# ── /digiseva monthly ──────────────────────────────────────────
def _build_monthly(services) -> str:
    from collections import defaultdict
    cat_totals: dict = defaultdict(float)
    grand = 0.0
    for s in services:
        if not s.active:
            continue
        m = _monthly_equiv(s)
        if m > 0:
            cat_totals[s.category] += m
            grand += m

    if not cat_totals:
        return "No active services found."

    lines = ["📅 *Monthly Breakdown*", DIV, f"💰 *Total:* ₹{grand:,.0f}/mo", ""]
    sorted_cats = sorted(cat_totals.items(), key=lambda x: -x[1])
    max_amt = sorted_cats[0][1]
    for cat, amt in sorted_cats:
        pct = (amt / grand * 100)
        bar_len = int(amt / max_amt * 10)
        bar = "█" * bar_len + "░" * (10 - bar_len)
        lines.append(f"`{bar}` {pct:4.0f}%")
        lines.append(f"   *{cat}*  ₹{amt:,.0f}/mo")
        lines.append("")
    return "\n".join(lines).strip()


# ── /digiseva paid ─────────────────────────────────────────────
def _build_paid_prompt(services) -> tuple[str, dict]:
    today = date.today()
    pending = []
    for s in services:
        if s.active and not s.paid_current_cycle:
            pending.append(s)
    # sort: overdue first, then by due date
    def sort_key(s):
        try:
            d = date.fromisoformat(s.next_due)
        except ValueError:
            d = date.today()
        return d
    pending.sort(key=sort_key)

    if not pending:
        return "✅ Nothing pending — all paid!", {}

    index_map = {}  # "1" -> service_id
    lines = ["💳 *Mark as Paid*", DIV, "Reply with a number:\n"]
    for i, s in enumerate(pending, 1):
        index_map[str(i)] = s.id
        st = _status(s)
        flag = " 🔴" if st == "overdue" else (" ⏳" if st == "pending" else "")
        lines.append(f"*{i}.* {s.name}  — ₹{s.amount:,.0f}{flag}")
        lines.append(f"   {s.category} · 📅 {_due_label(s.next_due)}")
        lines.append("")
    return "\n".join(lines).strip(), index_map


# ── Help text ──────────────────────────────────────────────────
HELP_TEXT = f"""\
🤖 *DigiSeva Commands*
{DIV}
/summary — Overview & status
/due — Items due in next 7 days
/overdue — Unpaid overdue items
/paid — Mark an item as paid
/monthly — Spend by category
/export — Download data as JSON
/help — Show this message\
"""


# ── Individual command handlers ────────────────────────────────
async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services
    await update.message.reply_text(_build_summary(load_services()), parse_mode="Markdown")

async def cmd_due(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services
    await update.message.reply_text(_build_due(load_services()), parse_mode="Markdown")

async def cmd_overdue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services
    await update.message.reply_text(_build_overdue(load_services()), parse_mode="Markdown")

async def cmd_monthly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services
    await update.message.reply_text(_build_monthly(load_services()), parse_mode="Markdown")

async def cmd_paid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services
    chat_id = str(update.effective_chat.id)
    msg, index_map = _build_paid_prompt(load_services())
    if index_map:
        _pending_paid_session[chat_id] = index_map
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import get_data_path
    try:
        with open(get_data_path(), "rb") as f:
            today = date.today().isoformat()
            await update.message.reply_document(
                f,
                filename=f"digiseva_{today}.json",
                caption=f"📦 DigiSeva export — {today}"
            )
    except FileNotFoundError:
        await update.message.reply_text("❌ No data file found.")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services
    await update.message.reply_text(_build_summary(load_services()), parse_mode="Markdown")


# ── Paid reply handler ─────────────────────────────────────────
async def handle_paid_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services, save_services, advance_next_due

    chat_id = str(update.effective_chat.id)
    if chat_id not in _pending_paid_session:
        return

    index_map = _pending_paid_session[chat_id]
    text = update.message.text.strip()

    if text not in index_map:
        keys = ", ".join(index_map.keys())
        await update.message.reply_text(
            f"❓ Please reply with one of: {keys}\n\nOr /digiseva to cancel.",
            parse_mode="Markdown"
        )
        return

    service_id = index_map[text]
    services = load_services()
    for i, s in enumerate(services):
        if s.id == service_id:
            new_due = advance_next_due(s)
            services[i].paid_current_cycle = True
            services[i].next_due = new_due
            save_services(services)
            del _pending_paid_session[chat_id]
            await update.message.reply_text(
                f"✅ *{s.name}* marked as paid\n"
                f"   ₹{s.amount:,.0f} · {s.category}\n"
                f"   📅 Next due: {new_due}",
                parse_mode="Markdown"
            )
            return


def create_bot_app(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("due",     cmd_due))
    app.add_handler(CommandHandler("overdue", cmd_overdue))
    app.add_handler(CommandHandler("paid",    cmd_paid))
    app.add_handler(CommandHandler("monthly", cmd_monthly))
    app.add_handler(CommandHandler("export",  cmd_export))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_paid_reply))
    return app
