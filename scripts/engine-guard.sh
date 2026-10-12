#!/usr/bin/env bash
# Detect a wedged vLLM (API alive, generation dead) and restart the container.
# Wedge signature: /v1/models answers while a 5-token completion hangs forever.
# Run from a 10-minute systemd user timer. Two consecutive failures -> restart.
#
# Env: ENGINE_URL (default http://localhost:8010), ENGINE_MODEL, CONTAINER (docker name)
set -u
ENGINE_URL="${ENGINE_URL:-http://localhost:8010}"
ENGINE_MODEL="${ENGINE_MODEL:-qwen3-30b-a3b}"
CONTAINER="${CONTAINER:-llm}"
STATE="${STATE:-$HOME/.cache/engine-guard.fails}"
LOG="${LOG:-$HOME/.cache/engine-guard.log}"

for path in "$STATE" "$LOG"; do
  dir=$(dirname -- "$path")
  if ! mkdir -p -- "$dir" 2>/dev/null || ! : >> "$path" 2>/dev/null; then
    echo "engine-guard: cannot write $path" >&2
    exit 2
  fi
done

probe() {
  body=$(curl -sf -m 90 "$ENGINE_URL/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$ENGINE_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Say OK\"}],\"max_tokens\":5}" \
    2>/dev/null) || return 1
  printf '%s' "$body" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    choices = data.get("choices") if isinstance(data, dict) else None
    ok = isinstance(choices, list) and bool(choices) and isinstance(choices[0], dict) and ("message" in choices[0] or "text" in choices[0])
except (ValueError, TypeError):
    ok = False
sys.exit(0 if ok else 1)'
}

if probe; then
  echo 0 > "$STATE"
  exit 0
fi

FAILS=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$FAILS" > "$STATE"
echo "$(date '+%F %H:%M') probe fail #$FAILS" >> "$LOG"
if [ "$FAILS" -ge 2 ]; then
  echo "$(date '+%F %H:%M') RESTARTING $CONTAINER (wedged)" >> "$LOG"
  if docker restart "$CONTAINER" >/dev/null 2>&1; then
    echo 0 > "$STATE"
    echo "$(date '+%F %H:%M') RESTARTED $CONTAINER" >> "$LOG"
  else
    rc=$?
    echo "$(date '+%F %H:%M') RESTART FAILED rc=$rc" >> "$LOG"
    exit 1
  fi
fi
