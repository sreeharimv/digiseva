import calendar
import csv
import io
import logging
from collections import defaultdict
from datetime import date, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logger = logging.getLogger(__name__)

# chat_id -> {index: service_id}
_pending_paid_session: dict = {}

DIV = "─" * 22

CAT_EMOJI = {
    # subscriptions
    "OTT": "🎬", "Cloud": "☁️", "AI": "🤖", "Telecom": "📡",
    "Domain": "🌐", "VPN": "🔒", "Software": "💻",
    # bills
    "Electricity": "⚡", "FTTH": "🌐", "LPG": "🔥", "DTH": "📺",
    "Water": "💧", "Maintenance": "🔧",
    # income
    "Salary": "💼", "Freelance": "🖥️", "Rental": "🏠", "Dividend": "📈",
    # expense
    "EMI": "🏦", "Credit Card": "💳", "Misc": "🛒",
    # fallback
    "Other": "📦",
}

INV_EMOJI = {
    "Bank Account": "🏦", "Fixed Deposit": "📋", "Mutual Fund": "📈",
    "Stocks": "📊", "Gold": "🪙", "PPF": "🏛", "EPF": "🏛",
    "NPS": "🏛", "Other": "📦",
}

CYCLE_SHORT = {
    "monthly": "/mo", "yearly": "/yr", "weekly": "/wk",
    "bi-monthly": "/2mo", "quarterly": "/qtr",
    "half-yearly": "/6mo", "one-time": "",
}

_TYPE_ORDER = ["income", "subscription", "bill", "expense"]
_TYPE_HEADER = {
    "income":       "💚 *Income*",
    "subscription": "📦 *Subscriptions*",
    "bill":         "🧾 *Bills*",
    "expense":      "💸 *Expenses*",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _monthly_equiv(s) -> float:
    rates = {"monthly": 1, "weekly": 4, "bi-monthly": 0.5,
             "quarterly": 1/3, "half-yearly": 1/6, "yearly": 1/12, "one-time": 0}
    return s.amount * rates.get(s.cycle, 0)


def _status(s) -> str:
    if s.paid_current_cycle:
        return "paid"
    today = date.today()
    try:
        due = date.fromisoformat(s.next_due)
    except ValueError:
        return "unknown"
    diff = (due - today).days
    if diff < 0:  return "overdue"
    if diff <= 7: return "pending"
    return "upcoming"


def _try_date(d: str) -> date:
    try:
        return date.fromisoformat(d)
    except ValueError:
        return date.max


def _due_label(s) -> str:
    try:
        due = date.fromisoformat(s.next_due)
    except ValueError:
        return s.next_due
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


def _send_chunks(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks, current, current_len = [], [], 0
    for line in text.split("\n"):
        line_len = len(line) + 1
        if current_len + line_len > max_len and current:
            chunks.append("\n".join(current))
            current, current_len = [line], line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


# ---------------------------------------------------------------------------
# /summary
# ---------------------------------------------------------------------------

def _build_summary(services) -> str:
    today = date.today()
    active = [s for s in services if s.active]

    monthly_income = 0.0
    monthly_outgo = 0.0
    paid_list, pending_list, overdue_list = [], [], []
    by_type: dict = {}

    for s in active:
        equiv = _monthly_equiv(s)
        if s.type == "income":
            monthly_income += equiv
        else:
            monthly_outgo += equiv
        by_type[s.type] = by_type.get(s.type, 0) + 1
        st = _status(s)
        if st == "paid":      paid_list.append(s)
        elif st == "overdue": overdue_list.append(s)
        elif st == "pending": pending_list.append(s)

    net = monthly_income - monthly_outgo
    net_arrow = "▲" if net >= 0 else "▼"

    type_parts = [f"{by_type[k]} {k}" for k in _TYPE_ORDER if k in by_type]

    lines = [
        "📊 *DigiSeva Overview*",
        DIV,
        f"💚 *Income:*  ₹{monthly_income:,.0f}/mo",
        f"💸 *Outgo:*   ₹{monthly_outgo:,.0f}/mo",
        f"📈 *Net:*     {net_arrow} ₹{abs(net):,.0f}/mo",
        "",
        f"📦 *Active:* {len(active)}  ({' · '.join(type_parts)})",
        "",
        f"✅ Paid this cycle:  {len(paid_list)}",
        f"⏳ Due in 7 days:   {len(pending_list)}",
        f"🔴 Overdue:         {len(overdue_list)}",
    ]

    # Portfolio snapshot if investments exist
    try:
        from storage import load_investments
        investments = load_investments()
        if investments:
            total_val = sum(i["current_value"] for i in investments)
            bank_bal  = sum(i["current_value"] for i in investments if i["category"] == "Bank Account")
            lines += ["", f"💰 *Portfolio:* ₹{total_val:,.0f}  (Bank: ₹{bank_bal:,.0f})"]
    except Exception:
        pass

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


# ---------------------------------------------------------------------------
# /due
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# /overdue
# ---------------------------------------------------------------------------

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
        verb = "Expected" if s.type == "income" else "Was due"
        lines.append(f"⚠️ *{s.name}*  — ₹{s.amount:,.0f}")
        lines.append(f"   {s.category} · {s.cycle}")
        lines.append(_payment_line(s).rstrip())
        lines.append(f"   📅 {verb} {d.strftime('%-d %b')}  ({days_late}d ago)")
        lines.append("")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# /monthly
# ---------------------------------------------------------------------------

def _build_monthly(services) -> str:
    income_cats: dict = defaultdict(float)
    outgo_cats:  dict = defaultdict(float)
    income_total = outgo_total = 0.0

    for s in services:
        if not s.active:
            continue
        m = _monthly_equiv(s)
        if m <= 0:
            continue
        if s.type == "income":
            income_cats[s.category] += m
            income_total += m
        else:
            outgo_cats[s.category] += m
            outgo_total += m

    if not income_cats and not outgo_cats:
        return "No active entries found."

    lines = ["📅 *Monthly Breakdown*", DIV]

    if income_cats:
        lines += ["", f"💚 *Income:* ₹{income_total:,.0f}/mo"]
        for cat, amt in sorted(income_cats.items(), key=lambda x: -x[1]):
            pct = amt / income_total * 100
            lines.append(f"   {CAT_EMOJI.get(cat, '📌')} {cat}  ₹{amt:,.0f}  ({pct:.0f}%)")

    if outgo_cats:
        lines += ["", f"💸 *Outgo:* ₹{outgo_total:,.0f}/mo"]
        sorted_outgo = sorted(outgo_cats.items(), key=lambda x: -x[1])
        max_amt = sorted_outgo[0][1]
        for cat, amt in sorted_outgo:
            pct = amt / outgo_total * 100
            bar_len = int(amt / max_amt * 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)
            lines.append(f"`{bar}` {pct:4.0f}%")
            lines.append(f"   *{cat}*  ₹{amt:,.0f}/mo")
            lines.append("")

    net = income_total - outgo_total
    lines += [DIV, f"📈 *Net cashflow:* ₹{net:+,.0f}/mo"]
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# /investments
# ---------------------------------------------------------------------------

def _build_investments() -> str:
    from storage import load_investments
    investments = load_investments()

    if not investments:
        return "💰 No investments tracked yet.\n\nAdd bank accounts, FDs, mutual funds and more from the DigiSeva app."

    total_val  = sum(i["current_value"]   for i in investments)
    bank_bal   = sum(i["current_value"]   for i in investments if i["category"] == "Bank Account")
    total_inv  = sum(i["invested_amount"] for i in investments)
    total_ret  = total_val - total_inv
    ret_pct    = total_ret / total_inv * 100 if total_inv > 0 else None

    lines = ["💰 *Investments & Portfolio*", DIV, ""]
    lines.append(f"📊 Portfolio:     ₹{total_val:,.0f}")
    lines.append(f"🏦 Bank Balance:  ₹{bank_bal:,.0f}")
    if ret_pct is not None:
        arrow = "▲" if total_ret >= 0 else "▼"
        lines.append(f"📈 Returns:       {arrow} ₹{abs(total_ret):,.0f}  ({ret_pct:+.1f}%)")

    by_cat: dict = defaultdict(list)
    for inv in investments:
        by_cat[inv["category"]].append(inv)

    for cat, items in sorted(by_cat.items()):
        icon = INV_EMOJI.get(cat, "📦")
        lines += ["", f"{icon} *{cat}*"]
        for inv in items:
            ret = inv["current_value"] - inv["invested_amount"]
            ret_str = ""
            if inv["invested_amount"] > 0:
                pct = ret / inv["invested_amount"] * 100
                ret_str = f"  ({pct:+.1f}%)"
            inst = f" · {inv['institution']}" if inv.get("institution") else ""
            lines.append(f"  • {inv['name']}{inst}  ₹{inv['current_value']:,.0f}{ret_str}")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------

def _build_history(limit: int = 3) -> str:
    from storage import get_history_months
    months = get_history_months()

    if not months:
        return (
            "📅 *Payment History*\n"
            f"{DIV}\n"
            "No history yet.\n\n"
            "History builds automatically as you mark payments each month."
        )

    lines = ["📅 *Payment History*", DIV, ""]
    for m in months[:limit]:
        y, mo = m["cycle_month"].split("-")
        label = f"{calendar.month_name[int(mo)]} {y}"
        net   = m["total_income"] - m["total_outgo"]
        arrow = "▲" if net >= 0 else "▼"
        lines.append(f"*{label}*  _{m['count']} entries_")
        lines.append(f"  💚 Received:  ₹{m['total_income']:,.0f}")
        lines.append(f"  💸 Paid:      ₹{m['total_outgo']:,.0f}")
        lines.append(f"  📈 Net:       {arrow} ₹{abs(net):,.0f}")
        lines.append("")

    if len(months) > limit:
        lines.append(f"_({len(months) - limit} more months in app history)_")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# /paid prompt
# ---------------------------------------------------------------------------

def _build_paid_prompt(services) -> tuple[str, dict]:
    pending = [s for s in services if s.active and not s.paid_current_cycle]
    pending.sort(key=lambda s: _try_date(s.next_due))

    if not pending:
        return "✅ Nothing pending — all paid / received!", {}

    index_map: dict = {}
    lines = ["💳 *Mark as Paid / Received*", DIV, "Reply with a number:\n"]
    for i, s in enumerate(pending, 1):
        index_map[str(i)] = s.id
        st = _status(s)
        flag = " 🔴" if st == "overdue" else (" ⏳" if st == "pending" else "")
        income_tag = " 💚" if s.type == "income" else ""
        lines.append(f"*{i}.* {s.name}  — ₹{s.amount:,.0f}{flag}{income_tag}")
        lines.append(f"   {s.category} · 📅 {_due_label(s)}")
        lines.append("")
    return "\n".join(lines).strip(), index_map


# ---------------------------------------------------------------------------
# /list
# ---------------------------------------------------------------------------

def _build_list(services) -> str:
    today = date.today()
    active = [s for s in services if s.active]
    by_type_cat: dict = defaultdict(lambda: defaultdict(list))
    for s in active:
        by_type_cat[s.type][s.category].append(s)

    lines = [f"📋 *All Entries*  ({len(active)} total)", DIV]

    for type_key in _TYPE_ORDER:
        if type_key not in by_type_cat:
            continue
        by_cat = by_type_cat[type_key]
        lines.append("")
        lines.append(_TYPE_HEADER.get(type_key, f"*{type_key.title()}*"))
        sorted_cats = sorted(by_cat.items(), key=lambda x: -sum(s.amount for s in x[1]))

        for cat, items in sorted_cats:
            icon = CAT_EMOJI.get(cat, "📌")
            lines.append(f"\n{icon} *{cat}* ({len(items)})")

            def item_sort(s):
                order = {"overdue": 0, "pending": 1, "upcoming": 2, "paid": 3}
                return (order.get(_status(s), 9), s.next_due)

            for s in sorted(items, key=item_sort):
                st = _status(s)
                if s.type == "income":
                    status_icon = "✅" if s.paid_current_cycle else "💰"
                else:
                    status_icon = {"paid": "✅", "pending": "⏳", "overdue": "🔴", "upcoming": "🔵"}.get(st, "🔵")

                cycle_short = CYCLE_SHORT.get(s.cycle, "")
                amt = f"₹{s.amount:,.0f}{cycle_short}"

                extra = ""
                if s.tenure_months:
                    remaining = s.tenure_months - s.paid_instalments
                    extra = f"  [{s.paid_instalments}/{s.tenure_months}, {remaining} left]"
                elif s.category == "Credit Card" and s.outstanding_balance > 0:
                    extra = f"  [₹{s.outstanding_balance:,.0f} outstanding]"

                due = _try_date(s.next_due)
                due_str = due.strftime("%-d %b %Y") if due != date.max else s.next_due
                verb = "expected" if s.type == "income" else "due"

                if st == "paid":
                    due_label = f"next {due_str}"
                elif st == "overdue":
                    days = (today - due).days
                    due_label = f"{verb} {days}d ago"
                elif st == "pending":
                    days = (due - today).days
                    due_label = f"{verb} {'today' if days == 0 else f'in {days}d'}"
                else:
                    due_label = f"{verb} {due_str}"

                lines.append(f"  {status_icon} {s.name}  •  {amt}  •  {due_label}{extra}")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Help text
# ---------------------------------------------------------------------------

HELP_TEXT = f"""\
🤖 *DigiSeva Commands*
{DIV}
/summary     — Overview, cashflow & alerts
/list        — All entries by type & category
/due         — Items due in next 7 days
/overdue     — Overdue / unreceived items
/paid        — Mark an item as paid / received
/monthly     — Income & spend by category
/investments — Portfolio, bank balance & returns
/history     — Last 3 months of actuals
/export      — Download data as CSV
/help        — Show this message\
"""


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services
    await update.message.reply_text(_build_summary(load_services()), parse_mode="Markdown")


async def cmd_due(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services
    await update.message.reply_text(_build_due(load_services()), parse_mode="Markdown")


async def cmd_overdue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services
    await update.message.reply_text(_build_overdue(load_services()), parse_mode="Markdown")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services
    text = _build_list(load_services())
    for chunk in _send_chunks(text):
        await update.message.reply_text(chunk, parse_mode="Markdown")


async def cmd_monthly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services
    await update.message.reply_text(_build_monthly(load_services()), parse_mode="Markdown")


async def cmd_investments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_build_investments(), parse_mode="Markdown")


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_build_history(), parse_mode="Markdown")


async def cmd_paid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services
    chat_id = str(update.effective_chat.id)
    msg, index_map = _build_paid_prompt(load_services())
    if index_map:
        _pending_paid_session[chat_id] = index_map
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services
    try:
        services = load_services()
        fields = [
            "id", "name", "type", "category", "amount", "currency",
            "cycle", "next_due", "payment_method", "auto_debit",
            "paid_current_cycle", "notes", "active", "created_at",
            "tenure_months", "paid_instalments",
            "credit_limit", "outstanding_balance", "statement_amount",
        ]
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(fields)
        for s in services:
            d = s.model_dump()
            writer.writerow([d[k] for k in fields])
        output.seek(0)
        today = date.today().isoformat()
        await update.message.reply_document(
            io.BytesIO(output.getvalue().encode("utf-8")),
            filename=f"digiseva_{today}.csv",
            caption=f"📦 DigiSeva export — {today} ({len(services)} entries)",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Export failed: {e}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import load_services, load_investments
    services    = load_services()
    investments = load_investments()
    active = [s for s in services if s.active]
    monthly_income = sum(_monthly_equiv(s) for s in active if s.type == "income")
    monthly_outgo  = sum(_monthly_equiv(s) for s in active if s.type != "income")
    paid     = sum(1 for s in active if s.paid_current_cycle)
    today    = date.today()
    overdue  = sum(1 for s in active if not s.paid_current_cycle and _try_date(s.next_due) < today)
    due_soon = sum(1 for s in active if not s.paid_current_cycle and today <= _try_date(s.next_due) <= today + timedelta(days=7))
    net = monthly_income - monthly_outgo
    net_arrow = "▲" if net >= 0 else "▼"
    name = update.effective_user.first_name or "there"

    inv_line = ""
    if investments:
        total_val = sum(i["current_value"] for i in investments)
        bank_bal  = sum(i["current_value"] for i in investments if i["category"] == "Bank Account")
        inv_line  = f"\n💰 Portfolio:    *₹{total_val:,.0f}*  (Bank ₹{bank_bal:,.0f})"

    msg = (
        f"👋 *Hey {name}!*\n"
        f"{DIV}\n"
        f"DigiSeva is tracking *{len(active)} entries* for you.\n"
        f"\n"
        f"💚 Income:      *₹{monthly_income:,.0f}/mo*\n"
        f"💸 Outgo:       *₹{monthly_outgo:,.0f}/mo*\n"
        f"📈 Net:         *{net_arrow} ₹{abs(net):,.0f}/mo*"
        f"{inv_line}\n"
        f"\n"
        f"✅ Paid this cycle:  {paid}\n"
        f"⏳ Due in 7 days:   {due_soon}\n"
        f"🔴 Overdue:         {overdue}\n"
        f"\n"
        f"{DIV}\n"
        f"/summary  /list  /due  /overdue\n"
        f"/paid  /monthly  /investments  /history\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Paid reply handler
# ---------------------------------------------------------------------------

async def handle_paid_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from storage import get_service, update_service, advance_next_due, add_paid_log

    chat_id = str(update.effective_chat.id)
    if chat_id not in _pending_paid_session:
        return

    index_map = _pending_paid_session[chat_id]
    text = update.message.text.strip()

    if text not in index_map:
        keys = ", ".join(index_map.keys())
        await update.message.reply_text(f"❓ Please reply with one of: {keys}\n\nOr /paid to restart.")
        return

    service_id = index_map[text]
    s = get_service(service_id)
    if not s:
        await update.message.reply_text("❌ Service not found.")
        del _pending_paid_session[chat_id]
        return

    new_due = advance_next_due(s)
    extra: dict = {"paid_current_cycle": True, "next_due": new_due}

    completion_msg = ""
    if s.tenure_months is not None:
        new_paid = s.paid_instalments + 1
        extra["paid_instalments"] = new_paid
        if new_paid >= s.tenure_months:
            extra["active"] = False
            completion_msg = f"\n   🎉 EMI fully paid! ({s.tenure_months}/{s.tenure_months} instalments)"
        else:
            remaining = s.tenure_months - new_paid
            completion_msg = f"\n   📊 Progress: {new_paid}/{s.tenure_months} paid · {remaining} remaining"

    update_service(service_id, extra)
    add_paid_log(s)          # ← record in monthly history
    del _pending_paid_session[chat_id]

    verb = "received" if s.type == "income" else "paid"
    await update.message.reply_text(
        f"✅ *{s.name}* marked as {verb}\n"
        f"   ₹{s.amount:,.0f} · {s.category}\n"
        f"   📅 Next due: {new_due}"
        f"{completion_msg}",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_bot_app(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("summary",     cmd_summary))
    app.add_handler(CommandHandler("list",        cmd_list))
    app.add_handler(CommandHandler("due",         cmd_due))
    app.add_handler(CommandHandler("overdue",     cmd_overdue))
    app.add_handler(CommandHandler("paid",        cmd_paid))
    app.add_handler(CommandHandler("monthly",     cmd_monthly))
    app.add_handler(CommandHandler("investments", cmd_investments))
    app.add_handler(CommandHandler("history",     cmd_history))
    app.add_handler(CommandHandler("export",      cmd_export))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_paid_reply))
    return app
