#!/bin/bash
# Partition effect measurement (fixed queries) for the final presentation.
#
#   partition_bench.sh setup            build non-partitioned copies (qa_decline_bench.*_flat)
#   partition_bench.sh run <site> [N]   run fixed queries N times (default 3) + 1 warm-up
#   partition_bench.sh files            file count / size, and what site/year/month would give
#
# Compared: qa_decline.{posts,votes} (site -> year partitions)
#      vs   qa_decline_bench.{posts,votes}_flat (same rows, site/year as normal columns)
# Fixed queries (do not change between the 10-site and the 24-site run):
#   Q1 one site, one year  : SELECT COUNT(*) FROM votes WHERE site=S AND year=2022
#   Q2 one site, by month  : questions per month for S (API / R3 shape)
#   Q3 all sites, no filter: SELECT site, COUNT(*) FROM votes GROUP BY site
#   Q2 also on Hive (partitioned) for the Hive vs Impala comparison.
# avg_mb_if_month assumes one file per partition (R1 writes one file per site/year).
# Output: /data/work/r2/logs/partition_bench_<UTC>.tsv
#   query  table  engine  run  seconds  partitions_scanned  bytes_planned
# Environment to note with the result: VM 1 core / 3 GB, impalad single node.
set -u
DB=qa_decline; BDB=qa_decline_bench
OUTDIR=${R2_LOG_DIR:-/data/work/r2/logs}; mkdir -p "$OUTDIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

setup() {
  hdfs dfs -mkdir -p /project/bench
  hive -S -e "
CREATE DATABASE IF NOT EXISTS $BDB;
DROP TABLE IF EXISTS $BDB.posts_flat; DROP TABLE IF EXISTS $BDB.votes_flat;
SET parquet.compression=SNAPPY;
CREATE TABLE $BDB.posts_flat STORED AS PARQUET LOCATION '/project/bench/posts_flat'
  AS SELECT * FROM $DB.posts;
CREATE TABLE $BDB.votes_flat STORED AS PARQUET LOCATION '/project/bench/votes_flat'
  AS SELECT * FROM $DB.votes;" || exit 1
  impala-shell -q "INVALIDATE METADATA $BDB.posts_flat; INVALIDATE METADATA $BDB.votes_flat;
SELECT COUNT(*) FROM $DB.posts; SELECT COUNT(*) FROM $BDB.posts_flat;
SELECT COUNT(*) FROM $DB.votes; SELECT COUNT(*) FROM $BDB.votes_flat;"
  echo "check: the two counts of each pair must be equal"
}

# impala_time <sql>: prints seconds (from impala-shell "Fetched ... in X s")
impala_time() {
  impala-shell -B -q "$1" 2>&1 >/dev/null | sed -n 's/.*Fetched .* in \([0-9.]*\)s.*/\1/p' | tail -1
}
# impala_plan <sql>: "partitions=a/b<TAB>size=..."
impala_plan() {
  impala-shell -B -q "EXPLAIN $1" 2>/dev/null | grep -o 'partitions=[0-9/]* size=[0-9.]*[KMGT]*B' | head -1 \
    | sed 's/ /\t/'
}

run_bench() {
  local S=$1 N=${2:-3} OUT="$OUTDIR/partition_bench_$STAMP.tsv"
  echo "$S" | grep -Eq '^[a-z0-9][a-z0-9.-]*$' || { echo "bad site"; exit 1; }
  printf 'query\ttable\tengine\trun\tseconds\tpartitions\tsize\n' > "$OUT"
  for pair in "$DB:votes:posts" "$BDB:votes_flat:posts_flat"; do
    IFS=: read D V P <<< "$pair"
    Q1="SELECT COUNT(*) FROM $D.$V WHERE site='$S' AND year=2022"
    Q2="SELECT year, month, COUNT(*) FROM $D.$P WHERE site='$S' AND post_type_id=1 GROUP BY year, month"
    Q3="SELECT site, COUNT(*) FROM $D.$V GROUP BY site"
    for q in Q1 Q2 Q3; do
      sql=${!q}; tbl=$(echo "$sql" | grep -o "$D\.[a-z_]*" | head -1)
      plan=$(impala_plan "$sql"); [ -z "$plan" ] && plan="-	-"
      impala_time "$sql" >/dev/null                       # warm-up, not recorded
      for i in $(seq 1 "$N"); do
        t=$(impala_time "$sql")
        printf '%s\t%s\timpala\t%s\t%s\t%s\n' "$q" "$tbl" "$i" "${t:--}" "$plan" | tee -a "$OUT"
      done
    done
  done
  # Hive vs Impala: Q2 on the partitioned table
  Q2H="SELECT year, month, COUNT(*) FROM $DB.posts WHERE site='$S' AND post_type_id=1 GROUP BY year, month"
  for i in $(seq 1 "$N"); do
    t0=$(date +%s.%N); hive -S -e "$Q2H" >/dev/null 2>&1; t1=$(date +%s.%N)
    printf 'Q2\t%s\thive\t%s\t%.2f\t-\t-\n' "$DB.posts" "$i" "$(awk -v a="$t0" -v b="$t1" 'BEGIN{print b-a}')" | tee -a "$OUT"
  done
  echo "# site=$S runs=$N sites_loaded=$(impala-shell -B -q "SELECT COUNT(DISTINCT site) FROM $DB.votes" 2>/dev/null)" >> "$OUT"
  echo "saved $OUT"
}

files_stats() {
  local OUT="$OUTDIR/partition_files_$STAMP.tsv"
  printf 'table\tdirs\tfiles\tbytes\tsite_year_parts\tsite_year_month_parts\tavg_mb_per_file_now\tavg_mb_if_month\n' > "$OUT"
  for T in posts users votes; do
    read DIRS FILES BYTES _ <<< "$(hdfs dfs -count /project/parquet/$T)"
    SY=$(impala-shell -B -q "SELECT COUNT(*) FROM (SELECT DISTINCT site, year FROM $DB.$T) x" 2>/dev/null)
    SYM=$(impala-shell -B -q "SELECT COUNT(*) FROM (SELECT DISTINCT site, year, month FROM $DB.$T) x" 2>/dev/null)
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%.3f\t%.3f\n' "$T" "$DIRS" "$FILES" "$BYTES" "$SY" "$SYM" \
      "$(awk -v b="$BYTES" -v f="$FILES" 'BEGIN{print b/f/1048576}')" \
      "$(awk -v b="$BYTES" -v f="$SYM" 'BEGIN{print b/f/1048576}')" | tee -a "$OUT"
  done
  echo "saved $OUT"
}

case "${1:-}" in
  setup) setup ;;
  run)   run_bench "${2:?site}" "${3:-3}" ;;
  files) files_stats ;;
  *) sed -n '2,6p' "$0"; exit 2 ;;
esac
