# DigiSeva — Multiuser Redesign

**Status:** Planning
**Date:** June 2026

---

## Goals

1. Multiple users (family), each with fully isolated data
2. Admin cannot read other users' data (PIN-derived encryption)
3. Telegram bot works for every user (PIN unlock + account linking)
4. Accessible from anywhere without Tailscale (GitHub Pages + Cloudflare quick tunnel)
5. Zero extra cost (reuse Grabha's proven tunnel pattern)

---

## Access Architecture

### Current
```
Tailscale → Anjaneya:8200 → FastAPI (single user)
```

### Future
```
Option A (external):
  sreeharimv.github.io/digiseva   ← static frontend (GitHub Pages, free)
          ↓  API calls to tunnel URL read from tunnel-url.txt
  <random>.trycloudflare.com      ← Cloudflare quick tunnel (free, no domain)
          ↓
  Anjaneya:8200 → FastAPI

Option B (direct, unchanged):
  Tailscale → Anjaneya:8200       ← still works, API calls are relative
```

The frontend detects which path to use automatically:
```javascript
// Fetch current tunnel URL from GitHub at startup
const r = await fetch(
  'https://raw.githubusercontent.com/sreeharimv/digiseva/main/tunnel-url.txt?t=' + Date.now(),
  { cache: 'no-store' }
);
API_BASE = (await r.text()).trim();
// If fetch fails (Tailscale direct) → API_BASE = '' → relative URLs
```

Tunnel infrastructure is self-healing (every 5 min cron check), identical to Grabha.

---

## Authentication Design

### PIN-based, not password-based

- 4 or 6 digit numeric PIN (decided at registration — default 6)
- Works naturally in Telegram (just type the digits)
- Argon2id makes brute-forcing expensive despite small search space

### Key derivation

```
PIN + user_id (as salt)
        ↓  Argon2id (memory=256MB, iterations=3, parallelism=1)
   master_key (32 bytes, never stored)
        ↓  AES-256-GCM encrypt a random data_key
   encrypted_data_key  ← stored in users table
```

On every login: PIN → derive master_key → decrypt data_key → use for session.
If PIN is wrong: master_key is wrong → decryption fails → login rejected.

### Sessions

**Web:** JWT token (2-hour expiry)
- Payload: `{ user_id, username, data_key_b64 }`
- data_key is included in the JWT (encrypted by app's JWT_SECRET)
- Client stores JWT in `localStorage`
- Every API request: `Authorization: Bearer <token>`

**Bot:** In-memory dict (same pattern as existing `_pending_paid_session`)
```python
_user_sessions: dict = {
    chat_id: {
        "user_id": str,
        "data_key": bytes,
        "expires_at": datetime
    }
}
```
Session TTL: 2 hours. After expiry, bot asks for PIN again.

---

## Encryption Design

### What gets encrypted

Only fields a human would consider sensitive. Structural fields needed for
scheduling and filtering stay plain.

**services table**

| Field | Plain / Encrypted | Reason |
|-------|------------------|--------|
| id | Plain | Primary key |
| user_id | Plain | Foreign key |
| type | Plain | Used for filtering (income/expense/etc.) |
| cycle | Plain | Used by scheduler |
| next_due | Plain | Used by scheduler |
| paid_current_cycle | Plain | Used by scheduler |
| active | Plain | Used for filtering |
| auto_debit | Plain | Used by scheduler |
| tenure_months | Plain | Used by scheduler |
| paid_instalments | Plain | Used by scheduler |
| created_at | Plain | Not sensitive |
| **name** | **Encrypted** | Service name |
| **amount** | **Encrypted** | Financial amount |
| **category** | **Encrypted** | Category detail |
| **payment_method** | **Encrypted** | Bank/card info |
| **notes** | **Encrypted** | Free text |
| **credit_limit** | **Encrypted** | Financial |
| **outstanding_balance** | **Encrypted** | Financial |
| **statement_amount** | **Encrypted** | Financial |

Encrypted fields stored as a single `enc_data TEXT` column containing
AES-256-GCM encrypted JSON. A `enc_nonce TEXT` column stores the nonce.

**investments table**

| Field | Plain / Encrypted |
|-------|------------------|
| id, user_id, active, last_updated, created_at | Plain |
| name, current_value, invested_amount, institution, notes | **Encrypted** |
| category | **Encrypted** |

**paid_log table**

| Field | Plain / Encrypted |
|-------|------------------|
| id, user_id, service_id, type, cycle_month, paid_at | Plain |
| service_name, amount, category | **Encrypted** |

### Encryption helper (Python)

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os, json, base64

def encrypt(data_key: bytes, payload: dict) -> tuple[str, str]:
    nonce = os.urandom(12)
    ct = AESGCM(data_key).encrypt(nonce, json.dumps(payload).encode(), None)
    return base64.b64encode(ct).decode(), base64.b64encode(nonce).decode()

def decrypt(data_key: bytes, enc_data: str, nonce: str) -> dict:
    ct = base64.b64decode(enc_data)
    n  = base64.b64decode(nonce)
    pt = AESGCM(data_key).decrypt(n, ct, None)
    return json.loads(pt)
```

---

## Database Schema

### New table: `users`

```sql
CREATE TABLE IF NOT EXISTS users (
    id                   TEXT PRIMARY KEY,
    username             TEXT UNIQUE NOT NULL,
    pin_hash             TEXT NOT NULL,           -- Argon2id hash (verification only)
    encrypted_data_key   TEXT NOT NULL,           -- base64 AES-GCM ciphertext
    key_nonce            TEXT NOT NULL,           -- base64 nonce for above
    telegram_chat_id     TEXT,                    -- NULL until linked via /link
    link_code            TEXT,                    -- 6-digit code, NULL when not active
    link_code_expires    TEXT,                    -- ISO datetime, 15-min TTL
    created_at           TEXT NOT NULL
);
```

### Modified tables (Phases 2 & 3)

```sql
-- Phase 2: add user_id
ALTER TABLE services      ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE investments   ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE paid_log      ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE payment_history ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';

CREATE INDEX IF NOT EXISTS idx_services_user    ON services(user_id);
CREATE INDEX IF NOT EXISTS idx_investments_user ON investments(user_id);
CREATE INDEX IF NOT EXISTS idx_paid_log_user    ON paid_log(user_id);

-- Phase 3: add encryption columns
ALTER TABLE services    ADD COLUMN enc_data  TEXT;
ALTER TABLE services    ADD COLUMN enc_nonce TEXT;
ALTER TABLE investments ADD COLUMN enc_data  TEXT;
ALTER TABLE investments ADD COLUMN enc_nonce TEXT;
ALTER TABLE paid_log    ADD COLUMN enc_data  TEXT;
ALTER TABLE paid_log    ADD COLUMN enc_nonce TEXT;
```

---

## API Changes

### New endpoints

```
POST /api/auth/register        — { username, pin } → creates user, returns JWT
POST /api/auth/login           — { username, pin } → returns JWT
GET  /api/auth/me              — returns current user info (no sensitive data)
POST /api/auth/link-code       — generates 6-digit Telegram link code (15-min TTL)
```

### Auth middleware

```python
async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    user_id = payload["user_id"]
    data_key = base64.b64decode(payload["data_key_b64"])
    return {"user_id": user_id, "data_key": data_key, "username": payload["username"]}
```

Every existing endpoint gets `current_user = Depends(get_current_user)`.
All storage calls pass `user_id=current_user["user_id"]` and
`data_key=current_user["data_key"]`.

### CORS (for GitHub Pages)

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://sreeharimv.github.io"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Rate limiting (login endpoint)

5 failed PIN attempts per IP per 15 minutes → HTTP 429.
Implemented with a simple in-memory dict (good enough at this scale).

---

## Telegram Bot Changes

### New state

```python
_user_sessions: dict = {}   # chat_id → { user_id, data_key, expires_at }
```

### New commands

**`/start` or any command with no active session:**
```
Bot: Welcome to DigiSeva. Enter your PIN to unlock your session.

User: 123456

Bot: ✅ Session active for 2 hours.
     Try /summary, /paid, /investments
```

**`/link <code>`** — links Telegram account to web account:
```
User: /link 482951

Bot: ✅ Telegram linked to account "sreehari".
     You can now use all commands.
```

### Modified commands

Every handler gets a session guard:

```python
async def cmd_summary(update, context):
    session = _get_session(update.effective_chat.id)
    if not session:
        await update.message.reply_text("Enter your PIN to unlock.")
        return
    services = load_services(user_id=session["user_id"],
                             data_key=session["data_key"])
    ...
```

### Session helper

```python
def _get_session(chat_id: int) -> dict | None:
    s = _user_sessions.get(chat_id)
    if not s:
        return None
    if datetime.now() > s["expires_at"]:
        del _user_sessions[chat_id]
        return None
    return s
```

### PIN handling

When a message is a 4 or 6 digit number and no session is active:

```python
# In message handler (before other handlers)
if re.fullmatch(r'\d{4}|\d{6}', text):
    user = get_user_by_chat_id(chat_id)
    if not user:
        reply("Not linked. Visit DigiSeva → Settings → Link Telegram.")
        return
    data_key = verify_pin_and_get_key(user, text)
    if data_key:
        _user_sessions[chat_id] = {
            "user_id": user["id"],
            "data_key": data_key,
            "expires_at": datetime.now() + timedelta(hours=2)
        }
        reply("✅ Unlocked. Try /summary")
    else:
        reply("❌ Wrong PIN.")
    return
```

### Scheduler changes

Morning notifications go to every linked user:

```python
for user in get_linked_users():   # all users with telegram_chat_id set
    session_key = derive_data_key_for_scheduler(user)  # requires a scheduler key
    services = load_services(user_id=user["id"], data_key=session_key)
    text = _build_summary(services)
    await bot.send_message(user["telegram_chat_id"], text)
```

**Scheduler key problem:** The scheduler runs without a PIN. To decrypt data for
morning notifications, the data_key must be available without user input.

**Solution:** Store a separate `scheduler_data_key` per user, encrypted with a
server-side `SCHEDULER_SECRET` from the `.env` file. This key is used only by
the scheduler — it cannot decrypt data at rest without the server secret.

```
SCHEDULER_SECRET (in .env) → encrypt each user's data_key → scheduler_encrypted_key (in users table)
```

This is weaker than the PIN-derived key (server secret is on disk) but acceptable
for scheduled notifications. The PIN-derived key remains the gold standard for
interactive access.

---

## Frontend Changes

### New screens

**Login screen** (shown when no JWT in localStorage):
```
┌─────────────────────────┐
│  🌿 DigiSeva            │
│                         │
│  Username: [_________]  │
│                         │
│  PIN:  [1][2][3]        │
│        [4][5][6]        │
│        [7][8][9]        │
│           [0]           │
│                         │
│     [ Unlock ]          │
│                         │
│  New here? Register     │
└─────────────────────────┘
```

**Settings panel** — new "Telegram" section:
```
Link Telegram
Your code: 4 8 2 9 5 1   (expires in 12:34)
Send /link 482951 to @DigiSevaBot
[Generate new code]
```

### API call changes

Every `fetch()` call updated:
```javascript
// Old
fetch('/api/summary')

// New
fetch(`${API_BASE}/api/summary`, {
  headers: { 'Authorization': `Bearer ${getToken()}` }
})
```

Helper function:
```javascript
function apiFetch(path, options = {}) {
  return fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${localStorage.getItem('token')}`,
      ...(options.headers || {})
    }
  });
}
```

On 401 response → clear token → show login screen.

---

## New Dependencies

```
# backend/requirements.txt additions
argon2-cffi>=23.1.0     # PIN hashing
cryptography>=42.0.0    # AES-256-GCM
PyJWT>=2.8.0            # JWT tokens
```

---

## Infrastructure (Reusing Grabha Pattern)

### New systemd service on Anjaneya

`/etc/systemd/system/digiseva-tunnel.service`
```ini
[Unit]
Description=DigiSeva Cloudflare Quick Tunnel
After=network-online.target
Wants=network-online.target

[Service]
User=sreeh007
ExecStart=/usr/local/bin/cloudflared tunnel --url http://localhost:8200
ExecStartPost=/home/sreeh007/update-tunnel-url-digiseva.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### New scripts on Anjaneya

`~/update-tunnel-url-digiseva.sh` — reads URL from journald, commits to digiseva repo
`~/check-tunnel-digiseva.sh` — health check, restarts service if dead

### New cron entries

```
*/5 * * * * /home/sreeh007/check-tunnel-digiseva.sh
```

### GitHub repo changes

```
digiseva/
├── tunnel-url.txt          ← NEW: auto-updated by scripts
├── docs/
│   ├── index.html          ← NEW: GitHub Pages serves from here
│   ├── logo.png
│   ├── favicon.ico
│   └── ...
├── .github/
│   └── workflows/
│       └── pages.yml       ← NEW: syncs backend/static/ → docs/
└── backend/
    └── static/
        └── index.html      ← unchanged: FastAPI still serves this
```

### GitHub Action (`.github/workflows/pages.yml`)

```yaml
name: Sync frontend to GitHub Pages
on:
  push:
    branches: [main]
    paths: ['backend/static/**']

permissions:
  contents: write

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Sync static files to docs/
        run: |
          mkdir -p docs
          cp backend/static/index.html docs/
          cp backend/static/logo.png docs/ 2>/dev/null || true
          cp backend/static/favicon.ico docs/ 2>/dev/null || true
          cp backend/static/*.png docs/ 2>/dev/null || true
      - name: Commit if changed
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add docs/
          git diff --staged --quiet || git commit -m "chore: sync frontend to GitHub Pages"
          git push
```

---

## Implementation Phases

Each phase is independently deployable. The running instance stays up throughout.

### Phase 0 — Infrastructure (no Python changes)
**Goal:** GitHub Pages live, tunnel running, self-healing.

1. Create `docs/` folder, copy current `index.html` + assets
2. Enable GitHub Pages on repo (serve from `docs/`)
3. Add `.github/workflows/pages.yml`
4. Add `tunnel-url.txt` placeholder to repo
5. Create `digiseva-tunnel.service` on Anjaneya
6. Write `update-tunnel-url-digiseva.sh` and `check-tunnel-digiseva.sh`
7. Add cron entry
8. Start service, verify `sreeharimv.github.io/digiseva/` loads

✅ Deliverable: App accessible from anywhere, no login yet.

---

### Phase 1 — Auth foundation (additive, no data changes)
**Goal:** Login screen, JWT, users table. No encryption, no user scoping yet.

1. Add `argon2-cffi`, `PyJWT`, `cryptography` to requirements.txt
2. Add `users` table to database schema
3. Add `/api/auth/register`, `/api/auth/login`, `/api/auth/me` endpoints
4. Add `get_current_user` JWT dependency (but don't apply to existing endpoints yet)
5. Add CORS middleware
6. Add login/register screen to `index.html`
7. Add `apiFetch()` helper with Authorization header
8. Create "default" admin account (migration script, one-time)

✅ Deliverable: Login works. Existing endpoints still unauthenticated (temporarily).

---

### Phase 2 — User isolation (data migration, breaking change)
**Goal:** All data scoped to user_id. Existing data migrated to 'default' user.

1. Migration script: add `user_id = 'default'` to all existing rows
2. Update all storage functions: add `user_id` parameter
3. Apply `get_current_user` dependency to all existing endpoints
4. Update frontend: pass JWT on all API calls, handle 401
5. Test thoroughly before deploying

✅ Deliverable: Single-user still, but auth-gated. Ready for more users.

---

### Phase 3 — Encryption (layered on Phase 2)
**Goal:** Admin cannot read other users' data from SQLite.

1. Add `enc_data` + `enc_nonce` columns to services, investments, paid_log
2. Add `encrypt()` / `decrypt()` helpers to a new `crypto.py`
3. Update storage layer: encrypt on write, decrypt on read
4. Update auth: include `data_key` in JWT payload
5. Migration script: encrypt existing 'default' user's data
6. Add `SCHEDULER_SECRET` to `.env`, add `scheduler_encrypted_key` to users table
7. Update scheduler to decrypt data for notifications

✅ Deliverable: Data encrypted at rest. Admin sees only ciphertext.

---

### Phase 4 — Bot multiuser (PIN flow + account linking)
**Goal:** Any linked user can use all bot commands.

1. Add `/link <code>` command
2. Add PIN detection in message handler
3. Add `_user_sessions` dict with expiry
4. Update all bot command handlers with session guard
5. Add "Link Telegram" section to Settings panel in frontend
6. Add `POST /api/auth/link-code` endpoint
7. Update scheduler to send to all linked users

✅ Deliverable: Full multiuser bot.

---

### Phase 5 — Polish
**Goal:** Production-ready.

1. Rate limiting on `/api/auth/login` (5 attempts / 15 min per IP)
2. Session expiry UX in bot ("Your session expired, send PIN to continue")
3. Session expiry UX in web (auto-redirect to login on 401)
4. Logout button in frontend
5. PIN change flow (optional)
6. Invite-only registration (INVITE_CODE in `.env` to prevent open registration)

✅ Deliverable: Ready for family use.

---

## Migration Plan

Existing data on Anjaneya belongs to the admin user.

**At Phase 1 deployment:**
- Run `python migrate_users.py` which:
  - Creates the `users` table
  - Prompts for admin username + PIN
  - Creates admin account
  - Sets `user_id = 'default'` on all existing rows (Phase 2 migration handles this)

**At Phase 2 deployment:**
- Run `python migrate_user_ids.py` which:
  - Adds `user_id` column to all tables
  - Sets `user_id = 'default'` on all existing rows
  - The 'default' account was created in Phase 1

**At Phase 3 deployment:**
- Run `python migrate_encrypt.py` which:
  - Loads all rows for 'default' user
  - Encrypts sensitive fields using admin's data_key
  - Writes back `enc_data` + `enc_nonce`
  - Old plain columns remain (for rollback safety), removed after verification

**Rollback:** Each phase migration is non-destructive. Old columns are kept
until the next phase is confirmed stable.

---

## Open Decisions

| # | Question | Options | Default |
|---|----------|---------|---------|
| 1 | PIN length | 4 digits or 6 digits | **6 digits** |
| 2 | Session duration | 1h / 2h / 4h | **2 hours** |
| 3 | Registration | Open or invite-code only | **Invite-code** (add to .env) |
| 4 | Username format | Display name (e.g. "Sreehari") or email | **Display name** |
| 5 | Max users | Soft limit? | **No limit** (family scale) |
| 6 | Forgot PIN | Data lost (like Bitwarden) or admin reset? | **TBD** |

For Q6 — "Forgot PIN" — the safest answer (admin can't recover data) is
to lose the data. But for a family tool, an admin-assisted reset (admin
re-encrypts with a new PIN) could be acceptable if implemented carefully.
This requires a separate design discussion.

---

## Security Notes

- Passwords/PINs are **never logged** anywhere
- JWT secret (`JWT_SECRET`) must be in `.env`, never committed
- `SCHEDULER_SECRET` must be in `.env`, never committed
- The `tunnel-url.txt` in the public repo exposes the tunnel hostname —
  this is acceptable (security by auth, not obscurity), same as Grabha
- Cloudflare sees API traffic but not content (data is encrypted end-to-end
  from browser to SQLite)
- Rate limiting prevents online PIN brute force
- Offline brute force of a stolen SQLite file: 10,000 (4-digit) or 1,000,000
  (6-digit) combinations × Argon2id cost makes this impractical on consumer hardware

---

*This document should be updated as implementation decisions are finalised.*
