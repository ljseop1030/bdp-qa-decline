#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
R3_PYTHON="${R3_PYTHON:-/usr/local/bin/python2.7}"
export SPARK_CONF_DIR="${R3_SPARK_CONF_DIR:-${SPARK_CONF_DIR:-$ROOT/spark-conf}}"
export PYSPARK_PYTHON="$R3_PYTHON"
export PYSPARK_DRIVER_PYTHON="$R3_PYTHON"
export TZ=UTC
exec "$R3_PYTHON" "$ROOT/run.py" "$@"
