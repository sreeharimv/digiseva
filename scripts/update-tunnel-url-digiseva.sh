#!/bin/bash
# Watches cloudflared journal for the DigiSeva tunnel URL and pushes it to GitHub.
# Called by ExecStartPost in digiseva-tunnel.service

REPO=~/docker/digiseva
URL_FILE="$REPO/tunnel-url.txt"
LOG=~/digiseva-tunnel.log

echo "[digiseva] waiting for tunnel URL..." | tee -a "$LOG"

# Poll journal until we get a trycloudflare.com URL (up to 120s)
TUNNEL_URL=""
for i in $(seq 1 60); do
  TUNNEL_URL=$(journalctl -u digiseva-tunnel -n 50 --no-pager -b \
    | grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' \
    | grep -v 'api\.trycloudflare\.com' \
    | head -1)
  if [ -n "$TUNNEL_URL" ]; then
    break
  fi
  sleep 2
done

if [ -z "$TUNNEL_URL" ]; then
  echo "[digiseva] ERROR: could not detect tunnel URL after 120s" | tee -a "$LOG"
  exit 1
fi

echo "[digiseva] tunnel URL: $TUNNEL_URL" | tee -a "$LOG"

# Push to GitHub so frontend can discover it
cd "$REPO"
git pull --rebase origin master -q

CURRENT=$(cat tunnel-url.txt 2>/dev/null | tr -d '[:space:]')
if [ "$CURRENT" = "$TUNNEL_URL" ]; then
  echo "[digiseva] URL unchanged, skipping push" | tee -a "$LOG"
  exit 0
fi

echo "$TUNNEL_URL" > tunnel-url.txt
git add tunnel-url.txt
git commit -m "chore: update digiseva tunnel URL to $TUNNEL_URL"
git push origin master
echo "[digiseva] pushed new URL to GitHub" | tee -a "$LOG"
