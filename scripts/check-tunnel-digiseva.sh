#!/bin/bash
# Runs every 5 minutes via cron.
# 1. If tunnel URL is missing or stale — detects URL from journal and commits to GitHub.
# 2. If tunnel process is dead — restarts the service.
# cron: */5 * * * * /home/sreeh007/check-tunnel-digiseva.sh

# Required for systemctl --user and journalctl --user to work from cron
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"

REPO=~/docker/digiseva
URL_FILE="$REPO/tunnel-url.txt"
LOG=~/digiseva-tunnel.log

_get_live_url() {
  journalctl --user -u digiseva-tunnel --no-pager -n 200 \
    | grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' \
    | grep -v 'api\.trycloudflare\.com' \
    | tail -1
}

_commit_url() {
  local NEW_URL="$1"
  cd "$REPO"
  git pull --rebase origin master -q 2>/dev/null
  echo "$NEW_URL" > tunnel-url.txt
  git add tunnel-url.txt
  git diff --staged --quiet && return 0
  git commit -m "chore: update digiseva tunnel URL to $NEW_URL"
  git push origin master
  echo "$(date): pushed new tunnel URL: $NEW_URL" >> "$LOG"
}

# ── Is the tunnel service running? ───────────────────────────────────────────
if ! systemctl --user is-active --quiet digiseva-tunnel; then
  echo "$(date): tunnel service not running, starting..." >> "$LOG"
  systemctl --user start digiseva-tunnel
  sleep 10  # give it time to connect and get a URL
fi

# ── Detect current URL from journal ─────────────────────────────────────────
LIVE_URL=$(_get_live_url)
STORED_URL=$(cat "$URL_FILE" 2>/dev/null | tr -d '[:space:]')

if [ -z "$LIVE_URL" ]; then
  echo "$(date): tunnel running but no URL in journal yet, will retry" >> "$LOG"
  exit 0
fi

# ── Commit if URL changed ────────────────────────────────────────────────────
if [ "$STORED_URL" != "$LIVE_URL" ]; then
  echo "$(date): URL changed [$STORED_URL] → [$LIVE_URL], committing..." >> "$LOG"
  _commit_url "$LIVE_URL"
  STORED_URL="$LIVE_URL"
fi

# ── Health check ─────────────────────────────────────────────────────────────
if curl -sf --max-time 10 "$STORED_URL/api/health" > /dev/null 2>&1; then
  exit 0  # All good
else
  echo "$(date): tunnel unhealthy ($STORED_URL), restarting..." >> "$LOG"
  systemctl --user restart digiseva-tunnel
fi
