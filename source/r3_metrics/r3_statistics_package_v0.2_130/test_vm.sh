#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
R3_PYTHON="${R3_PYTHON:-/usr/local/bin/python2.7}"
if ! command -v "$R3_PYTHON" >/dev/null; then
    echo "Python 2.7 not found. Set R3_PYTHON to the interpreter used by R1." >&2
    exit 1
fi
export R3_PYTHON
export PYTHONUNBUFFERED=1
exec "$R3_PYTHON" "$ROOT/vm_test.py" "$@"
