#!/bin/bash
# Checks every 5 minutes if the DigiSeva tunnel is alive.
# If not, restarts digiseva-tunnel.service so ExecStartPost picks up the new URL.
# Run via cron: */5 * * * * /home/sreeh007/check-tunnel-digiseva.sh

URL_FILE=~/docker/digiseva/tunnel-url.txt
LOG=~/digiseva-tunnel.log

CURRENT_URL=$(cat "$URL_FILE" 2>/dev/null | tr -d '[:space:]')

if [ -z "$CURRENT_URL" ]; then
  echo "$(date): no URL in tunnel-url.txt, restarting service" >> "$LOG"
  sudo systemctl restart digiseva-tunnel >> "$LOG" 2>&1
  exit 0
fi

if curl -sf --max-time 10 "$CURRENT_URL/api/health" > /dev/null 2>&1; then
  # Tunnel is alive — do nothing
  exit 0
else
  echo "$(date): tunnel stale ($CURRENT_URL), restarting service" >> "$LOG"
  sudo systemctl restart digiseva-tunnel >> "$LOG" 2>&1
fi
