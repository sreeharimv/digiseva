#!/bin/bash
# Called by ExecStartPost in digiseva-tunnel.service.
# Returns immediately (so it doesn't block/timeout the service start),
# then does the actual URL detection + GitHub push in a background subshell.

REPO=~/docker/digiseva
URL_FILE="$REPO/tunnel-url.txt"
LOG=~/digiseva-tunnel.log

(
  echo "[digiseva] waiting for tunnel URL..." >> "$LOG"

  # Poll user journal until we get a trycloudflare.com URL (up to 120s)
  TUNNEL_URL=""
  for i in $(seq 1 60); do
    TUNNEL_URL=$(journalctl --user -u digiseva-tunnel -n 100 --no-pager \
      | grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' \
      | grep -v 'api\.trycloudflare\.com' \
      | head -1)
    if [ -n "$TUNNEL_URL" ]; then
      break
    fi
    sleep 2
  done

  if [ -z "$TUNNEL_URL" ]; then
    echo "[digiseva] ERROR: could not detect tunnel URL after 120s" >> "$LOG"
    exit 1
  fi

  echo "[digiseva] tunnel URL: $TUNNEL_URL" >> "$LOG"

  # Push to GitHub so frontend can discover it
  cd "$REPO"
  git pull --rebase origin master -q

  CURRENT=$(cat tunnel-url.txt 2>/dev/null | tr -d '[:space:]')
  if [ "$CURRENT" = "$TUNNEL_URL" ]; then
    echo "[digiseva] URL unchanged, skipping push" >> "$LOG"
    exit 0
  fi

  echo "$TUNNEL_URL" > tunnel-url.txt
  git add tunnel-url.txt
  git commit -m "chore: update digiseva tunnel URL to $TUNNEL_URL"
  git push origin master
  echo "[digiseva] pushed new URL to GitHub" >> "$LOG"
) &

disown
exit 0  # Return immediately — don't block service startup
