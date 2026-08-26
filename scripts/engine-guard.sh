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

probe() {
  curl -sf -m 90 "$ENGINE_URL/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$ENGINE_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Say OK\"}],\"max_tokens\":5}" \
    >/dev/null 2>&1
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
  docker restart "$CONTAINER" >/dev/null 2>&1
  echo 0 > "$STATE"
fi
