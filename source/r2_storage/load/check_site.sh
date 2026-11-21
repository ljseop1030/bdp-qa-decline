#!/bin/bash
# Integrity snapshot of one site in qa_decline.{posts,users,votes}.
# Usage: check_site.sh <site> <label> [--hive]
# Appends TSV rows to /data/work/r2/logs/check_<site>.tsv:
#   label engine table count distinct_id dup_ids sum_id sum_creation min_creation max_creation partitions
SITE=$1; LABEL=$2; ENGINE=impala; [ "$3" = "--hive" ] && ENGINE=hive
[ -z "$SITE" ] || [ -z "$LABEL" ] && { echo "usage: $0 <site> <label> [--hive]"; exit 1; }
echo "$SITE" | grep -Eq '^[a-z0-9][a-z0-9.-]*$' || { echo "bad site"; exit 1; }
OUT=/data/work/r2/logs/check_${SITE}.tsv; mkdir -p "$(dirname "$OUT")"
for T in posts users votes; do
  Q="SELECT COUNT(*), COUNT(DISTINCT id), SUM(id), SUM(creation_date), MIN(creation_date), MAX(creation_date) FROM qa_decline.$T WHERE site='$SITE'"
  D="SELECT COUNT(*) FROM (SELECT id FROM qa_decline.$T WHERE site='$SITE' GROUP BY id HAVING COUNT(*) > 1) d"
  P="SHOW PARTITIONS qa_decline.$T"
  if [ $ENGINE = impala ]; then
    R=$(impala-shell -B --output_delimiter='\t' -q "$Q" 2>/dev/null)
    DUP=$(impala-shell -B -q "$D" 2>/dev/null)
    NP=$(impala-shell -B --output_delimiter='\t' -q "$P" 2>/dev/null | awk -F'\t' -v s="$SITE" '$1==s' | wc -l)
  else
    R=$(hive -S -e "$Q" 2>/dev/null)
    DUP=$(hive -S -e "$D" 2>/dev/null)
    NP=$(hive -S -e "$P" 2>/dev/null | grep -c "^site=$SITE/")
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$LABEL" "$ENGINE" "$T" "$(echo "$R" | tr -s ' \t' '\t' | cut -f1-2)" "$DUP" "$(echo "$R" | tr -s ' \t' '\t' | cut -f3-6)	$NP" | tee -a "$OUT"
done
