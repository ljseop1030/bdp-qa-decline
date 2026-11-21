#!/bin/bash
# Start the R2 query API on port 8100 (Python 2.6 stdlib, impala-shell).
DIR=$(cd "$(dirname "$0")" && pwd)
LOG=${R2_API_LOG:-/data/work/r2/logs/api.log}
PIDF=${R2_API_PID:-/data/work/r2/api.pid}
mkdir -p "$(dirname "$LOG")"
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  echo "already running (pid $(cat "$PIDF"))"; exit 0
fi
nohup /usr/bin/python "$DIR/r2_api.py" --port "${R2_API_PORT:-8100}" "$@" >>"$LOG" 2>&1 &
echo $! > "$PIDF"
sleep 1
if kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  echo "started pid $(cat "$PIDF"), log $LOG"
else
  echo "failed to start, see $LOG"; tail -5 "$LOG"; exit 1
fi
