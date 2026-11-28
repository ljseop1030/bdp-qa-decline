#!/bin/bash
# Publish R3 / R4 results into the fixed serving paths and refresh Impala.
#
#   publish_serving.sh metrics <r3-run-id>          R3 site_metrics_monthly
#   publish_serving.sh q2 <local-dir>               R4 site_trend_model / site_trend_summary
#                                                   (+ q2_group_summary.csv if present)
#   add --dry-run as the last argument to only print what would happen.
#
# Serving paths are fixed (/project/serving/<table>) so the API and the
# web page never change when a new run is published. The previous files are
# moved to /project/serving/_prev/<table>.<UTC time> (not deleted).
#
# metrics: needs /project/statistics/<run-id>/_R3_SUCCESS.json with
#          complete=true and full_scope=true, and rows = expected_rows.
#          R3's own register_serving.hql changes the table LOCATION instead;
#          R2 copies so the serving path stays fixed (11/21 decision).
set -u
DB=qa_decline
SERVING=/project/serving
STAT=/project/statistics
LOGDIR=${R2_LOG_DIR:-/data/work/r2/logs}
DRY=0
for a in "$@"; do [ "$a" = "--dry-run" ] && DRY=1; done
mkdir -p "$LOGDIR"
LOG="$LOGDIR/publish_serving.log"
NOW=$(date -u +%Y%m%dT%H%M%SZ)

say() { echo "$*" | tee -a "$LOG"; }
run() { say "+ $*"; [ $DRY -eq 1 ] && return 0; "$@" || { say "FAIL: $*"; exit 1; }; }
impala_count() {
  impala-shell -B -q "REFRESH $DB.$1; SELECT COUNT(*) FROM $DB.$1" 2>/dev/null | tail -1
}

# swap <table> <source-kind> <source...>: stage, move old aside, move new in
swap_dir() {
  local table=$1; shift
  local stage="$SERVING/_stage/$table.$NOW"
  run hdfs dfs -mkdir -p "$stage" "$SERVING/_prev"
  "$@" "$stage"                       # caller fills the stage dir
  if hdfs dfs -test -d "$SERVING/$table"; then
    run hdfs dfs -mv "$SERVING/$table" "$SERVING/_prev/$table.$NOW"
  fi
  run hdfs dfs -mv "$stage" "$SERVING/$table"
}

case "${1:-}" in
  metrics)
    RUN=${2:?usage: publish_serving.sh metrics <r3-run-id>}
    SRC="$STAT/$RUN/site_metrics_monthly"
    RECEIPT=$(hdfs dfs -cat "$STAT/$RUN/_R3_SUCCESS.json" 2>/dev/null) \
      || { say "FAIL: no $STAT/$RUN/_R3_SUCCESS.json"; exit 1; }
    read COMPLETE FULL ROWS EXP SCOPE <<EOF
$(echo "$RECEIPT" | python -c 'import json,sys; r=json.load(sys.stdin); print("%s %s %s %s %s" % (r.get("complete"), r.get("full_scope"), r.get("rows"), r.get("expected_rows"), r.get("scope_id")))')
EOF
    say "== $NOW metrics run=$RUN scope=$SCOPE complete=$COMPLETE full_scope=$FULL rows=$ROWS expected=$EXP"
    if [ "$COMPLETE" != "True" ] || [ "$FULL" != "True" ] || [ "$ROWS" != "$EXP" ]; then
      say "FAIL: R3 run is not a complete full-scope result (SUBSET ONLY is not published)"; exit 1
    fi
    copy_metrics() { run hdfs dfs -cp "$SRC/*.parquet" "$1/"; }
    swap_dir site_metrics_monthly copy_metrics
    if [ $DRY -eq 0 ]; then
      run hive -S -e "ALTER TABLE $DB.site_metrics_monthly SET TBLPROPERTIES ('r2.data'='R3 run $RUN scope $SCOPE published $NOW')"
      GOT=$(impala_count site_metrics_monthly)
      say "impala rows: $GOT (expected $EXP)"
      [ "$GOT" = "$EXP" ] || { say "FAIL: row count mismatch, previous files are in $SERVING/_prev/site_metrics_monthly.$NOW"; exit 1; }
    fi
    ;;
  q2)
    DIR=${2:?usage: publish_serving.sh q2 <local-dir-with-R4-output>}
    say "== $NOW q2 from $DIR"
    for t in site_trend_model site_trend_summary; do
      [ -f "$DIR/$t.parquet" ] || { say "FAIL: missing $DIR/$t.parquet"; exit 1; }
    done
    for t in site_trend_model site_trend_summary; do
      put_one() { run hdfs dfs -put "$DIR/$t.parquet" "$1/"; }
      swap_dir $t put_one
    done
    if [ -f "$DIR/q2_group_summary.csv" ]; then
      put_gs() { run hdfs dfs -put "$DIR/q2_group_summary.csv" "$1/"; }
      swap_dir q2_group_summary put_gs
    fi
    if [ $DRY -eq 0 ]; then
      for t in site_trend_model site_trend_summary q2_group_summary; do
        say "impala rows $t: $(impala_count $t)"
      done
      # CSV row count = lines - header; compare model / summary with R4's CSVs if present
      for t in site_trend_model site_trend_summary; do
        if [ -f "$DIR/$t.csv" ]; then
          say "csv rows $t: $(($(wc -l < "$DIR/$t.csv") - 1))"
        fi
      done
    fi
    ;;
  *)
    sed -n '2,8p' "$0"; exit 2 ;;
esac
say "OK"
