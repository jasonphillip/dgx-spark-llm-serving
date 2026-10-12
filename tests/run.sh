#!/usr/bin/env bash
set -u

ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
mkdir -p "$TMP/bin"
cat > "$TMP/bin/curl" <<'EOF'
#!/usr/bin/env bash
cat "$STUB_BODY"
exit "$STUB_CURL_RC"
EOF
cat > "$TMP/bin/docker" <<'EOF'
#!/usr/bin/env bash
echo restart >> "$STUB_RESTARTS"
exit "$STUB_DOCKER_RC"
EOF
chmod +x "$TMP/bin/curl" "$TMP/bin/docker"
export PATH="$TMP/bin:$PATH" STUB_BODY="$TMP/body" STUB_RESTARTS="$TMP/restarts"
export STUB_CURL_RC=0 STUB_DOCKER_RC=0
failures=0

result() {
  if [[ $1 == 1 ]]; then
    echo "PASS $2"
  else
    echo "FAIL $2"
    failures=$((failures + 1))
  fi
}

setup_case() {
  export HOME="$TMP/home-$1"
  mkdir -p "$HOME"
  : > "$STUB_RESTARTS"
  STUB_CURL_RC=0
  STUB_DOCKER_RC=0
  unset STATE LOG
}

run_guard() {
  if bash "$ROOT/scripts/engine-guard.sh" > "$TMP/out" 2> "$TMP/err"; then
    rc=0
  else
    rc=$?
  fi
}

check() {
  local label=$1 expected_rc=$2 expected_state=$3 expected_restarts=$4
  local actual_state actual_restarts ok=1
  actual_state=$(cat "$HOME/.cache/engine-guard.fails" 2>/dev/null)
  actual_restarts=$(wc -l < "$STUB_RESTARTS")
  [[ $rc == "$expected_rc" && $actual_state == "$expected_state" && $actual_restarts == "$expected_restarts" ]] || ok=0
  result "$ok" "$label"
}

setup_case chat
printf '%s' '{"choices":[{"message":{"content":"anything"}}]}' > "$STUB_BODY"
mkdir -p "$HOME/.cache"
echo 1 > "$HOME/.cache/engine-guard.fails"
run_guard
check 'valid chat resets pending failure' 0 0 0

setup_case completion
printf '%s' '{"choices":[{"text":"anything"}]}' > "$STUB_BODY"
run_guard
check 'valid completion' 0 0 0

for name in error empty null malformed http timeout; do
  setup_case "$name"
  case "$name" in
    error) printf '%s' '{"error":{"message":"bad"}}' > "$STUB_BODY" ;;
    empty) printf '%s' '{"choices":[]}' > "$STUB_BODY" ;;
    null) printf '%s' '{"choices":null}' > "$STUB_BODY" ;;
    malformed) printf '%s' 'broken "choices" text' > "$STUB_BODY" ;;
    http) printf '%s' '{"choices":[{"message":{}}]}' > "$STUB_BODY"; STUB_CURL_RC=22 ;;
    timeout) printf '%s' '{"choices":[{"message":{}}]}' > "$STUB_BODY"; STUB_CURL_RC=28 ;;
  esac
  run_guard
  check "$name counts failure" 0 1 0
done

setup_case fresh
printf '%s' '{"error":"down"}' > "$STUB_BODY"
run_guard
first_ok=0
[[ $rc == 0 && $(cat "$HOME/.cache/engine-guard.fails") == 1 && $(wc -l < "$STUB_RESTARTS") == 0 ]] && first_ok=1
result "$first_ok" 'fresh HOME records first failure'
run_guard
restart_ok=0
[[ $rc == 0 && $(cat "$HOME/.cache/engine-guard.fails") == 0 && $(wc -l < "$STUB_RESTARTS") == 1 ]] &&
  grep -q 'RESTARTED llm' "$HOME/.cache/engine-guard.log" && restart_ok=1
result "$restart_ok" 'second failure restarts and resets'

setup_case unwritable
printf '%s' '{"error":"down"}' > "$STUB_BODY"
mkdir -p "$HOME/locked"
chmod 500 "$HOME/locked"
export STATE="$HOME/locked/fails"
run_guard
unwritable_ok=0
[[ $rc == 2 && ! -e $STATE && ! -s $STUB_RESTARTS ]] &&
  grep -q "engine-guard: cannot write $STATE" "$TMP/err" && unwritable_ok=1
result "$unwritable_ok" 'unwritable STATE directory exits 2'
chmod 700 "$HOME/locked"

setup_case overrides
printf '%s' '{"error":"down"}' > "$STUB_BODY"
export STATE="$HOME/state/sub/fails" LOG="$HOME/log/sub/guard.log"
run_guard
override_ok=0
[[ $rc == 0 && $(cat "$STATE") == 1 && -s $LOG ]] && override_ok=1
result "$override_ok" 'overridden STATE and LOG parents are created'

setup_case log_unwritable
printf '%s' '{"error":"down"}' > "$STUB_BODY"
mkdir -p "$HOME/locked"
chmod 500 "$HOME/locked"
export LOG="$HOME/locked/guard.log"
run_guard
log_ok=0
[[ $rc == 2 && ! -e $LOG && ! -s $HOME/.cache/engine-guard.fails && ! -s $STUB_RESTARTS ]] &&
  grep -q "engine-guard: cannot write $LOG" "$TMP/err" && log_ok=1
result "$log_ok" 'unwritable LOG directory exits 2 before probe'
chmod 700 "$HOME/locked"

setup_case restart_fail
printf '%s' '{"error":"down"}' > "$STUB_BODY"
mkdir -p "$HOME/.cache"
echo 1 > "$HOME/.cache/engine-guard.fails"
STUB_DOCKER_RC=9
run_guard
check 'failed restart keeps counter and exits 1' 1 2 1

exit "$((failures > 0))"
