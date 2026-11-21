#!/bin/bash
PIDF=${R2_API_PID:-/data/work/r2/api.pid}
if [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null; then
  echo "stopped pid $(cat "$PIDF")"; rm -f "$PIDF"
else
  echo "not running"; rm -f "$PIDF"
fi
