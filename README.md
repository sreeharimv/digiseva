# DigiSeva

A personal finance tracker for subscriptions, bills, income, and investments — with end-to-end encryption and a Telegram bot for quick access.

---

## Getting Started

### 1. Open the web app

**[https://sreeharimv.github.io/digiseva/](https://sreeharimv.github.io/digiseva/)**

The app works in any browser. On mobile, you can add it to your home screen for an app-like experience.

---

### 2. Create your account

1. Tap **New here? Register**
2. Enter your **name** (e.g. "Anu", "Sreehari")
3. Enter the **invite code** — ask the admin for this
4. Choose a **6-digit PIN** — this is your encryption key

> ⚠️ **Your PIN is the only key to your data.** If you forget it, your data cannot be recovered by anyone — including the admin. Write it down somewhere safe.

---

### 3. Log in

Enter your name and 6-digit PIN on the lock screen. Your session stays active for **2 hours**, after which you'll need to enter your PIN again.

To lock manually, click the **🔒** button in the top-right corner.

---

## What You Can Track

| Section | What goes here |
|---------|---------------|
| **Subscriptions** | OTT, cloud storage, SaaS, apps |
| **Bills** | Electricity, internet, LPG, maintenance |
| **Income** | Salary, freelance, rental, dividends |
| **Expenses** | EMIs, credit cards, one-time costs |
| **Investments** | Bank accounts, FDs, mutual funds, stocks, gold |

Each entry tracks the amount, due date, payment method, and payment status for the current cycle.

---

## Daily Use

- **Dashboard** — overview of this month's income vs outgo, upcoming dues, and portfolio snapshot
- **Mark as paid** — tap any entry and click **Mark Paid** to advance the due date to the next cycle
- **Search** — use the search bar to quickly find any entry
- **Export / Import** — download or upload your data as CSV from the sidebar

---

## Telegram Bot

The bot lets you check your finances and mark payments without opening the web app.

### Link your account

1. In the web app sidebar, tap **Link Telegram**
2. Click **Generate Code** — you'll get an 8-character code (valid for 10 minutes)
3. Open your Telegram bot and send:
   ```
   /link YOURCODE
   ```
4. Unlock the bot by sending your PIN:
   ```
   /unlock 123456
   ```
   Or just type your 6-digit PIN directly — the message is deleted automatically.

### Bot commands

| Command | What it does |
|---------|-------------|
| `/start` | Quick overview — income, outgo, due count |
| `/summary` | Full overview with overdue and upcoming items |
| `/list` | All entries grouped by type and category |
| `/due` | Items due in the next 7 days |
| `/overdue` | Overdue and unreceived items |
| `/paid` | Mark an item as paid or received |
| `/update` | Update any amount or bank balance |
| `/monthly` | Income and spend breakdown by category |
| `/investments` | Portfolio, bank balance, and returns |
| `/history` | Last 3 months of payment history |
| `/export` | Download your data as CSV |
| `/lock` | Lock your bot session immediately |
| `/help` | Show all commands |

Bot sessions last **2 hours**. After that, send your PIN again to unlock.

---

## Changing Your PIN

Sidebar → **Change PIN** → enter your current PIN, then your new PIN twice.

After a successful PIN change you'll be logged out and need to sign in with the new PIN.

---

## Privacy & Security

- All sensitive data (names, amounts, categories) is **encrypted with your PIN** before being stored. The server only sees ciphertext.
- Your PIN is never stored — only an Argon2id hash is kept for verification.
- Sessions are short-lived (2 hours) and stored only in memory on your device.
- Telegram bot sessions are also in-memory and expire after 2 hours.
- The daily alert bot uses a separate server-side key so notifications work without your PIN.

---

## Access Without Tailscale

The app is publicly accessible via GitHub Pages + a Cloudflare tunnel. No VPN needed.

If the tunnel URL has changed and the app shows a connection error, the URL usually refreshes within 5 minutes automatically.

---

*DigiSeva is self-hosted on a home server. Your data never leaves your own infrastructure.*
