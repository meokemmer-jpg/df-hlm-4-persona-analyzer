#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK_DIR="${DF_HLM_4_LOCK_DIR:-/tmp/df-hlm-4.lock}"
PID_FILE="$LOCK_DIR/pid"

if [[ -d "$LOCK_DIR" ]]; then
  if [[ "${DF_HLM_4_ENGINE_PGREP_CHECK:-true}" == "true" && -f "$PID_FILE" ]]; then
    old_pid="$(cat "$PID_FILE" || true)"
    if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
      echo "DF-HLM-4 K16 mutex held by pid=$old_pid" >&2
      exit 73
    fi
  fi
  rm -rf "$LOCK_DIR"
fi

mkdir "$LOCK_DIR"
echo "$$" > "$PID_FILE"
trap 'rm -rf "$LOCK_DIR"' EXIT INT TERM

cd "$ROOT_DIR"
exec python3 src/persona_analyzer.py "${1:-}"
