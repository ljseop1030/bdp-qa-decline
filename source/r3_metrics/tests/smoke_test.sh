#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
R3_PYTHON="${R3_PYTHON:-/usr/local/bin/python2.7}"
export SPARK_CONF_DIR="${R3_SPARK_CONF_DIR:-${SPARK_CONF_DIR:-$ROOT/../load/spark-conf}}"
export PYSPARK_PYTHON="$R3_PYTHON"
export PYSPARK_DRIVER_PYTHON="$R3_PYTHON"
export TZ=UTC
"$R3_PYTHON" -m unittest discover -s "$ROOT" -p 'test_*.py' -v
exec spark-submit --master 'local[1]' --driver-memory 768m \
  --conf spark.ui.enabled=false --conf spark.driver.extraJavaOptions=-Duser.timezone=UTC \
  "$ROOT/spark_smoke.py" "$@"
