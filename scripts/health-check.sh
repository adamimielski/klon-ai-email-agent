#!/usr/bin/env bash
# Health check — sprawdza czy maildash-api i maildash-worker żyją.
# Uruchamiane przez systemd timer co 30 min.
# Jeśli któryś nie działa → log + (opcjonalnie) email.
set -uo pipefail

LOG=/opt/mail-dashboard/logs/health.log
TS=$(date -u +%FT%TZ)

api_active=$(systemctl is-active maildash-api 2>/dev/null || echo "fail")
worker_active=$(systemctl is-active maildash-worker 2>/dev/null || echo "fail")

# Test endpoint /health
http_ok=$(curl -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health 2>/dev/null || echo "000")

status="ok"
if [[ "$api_active" != "active" || "$worker_active" != "active" || "$http_ok" != "200" ]]; then
    status="DEGRADED"
fi

echo "$TS api=$api_active worker=$worker_active http=$http_ok status=$status" >> "$LOG"

if [[ "$status" == "DEGRADED" ]]; then
    # Spróbuj automatycznego restartu (raz)
    if [[ "$api_active" != "active" ]]; then
        systemctl restart maildash-api && echo "$TS auto-restart maildash-api" >> "$LOG"
    fi
    if [[ "$worker_active" != "active" ]]; then
        systemctl restart maildash-worker && echo "$TS auto-restart maildash-worker" >> "$LOG"
    fi
    exit 1
fi
exit 0
