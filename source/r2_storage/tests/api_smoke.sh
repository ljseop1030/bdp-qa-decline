#!/bin/bash
# Smoke test for the R2 API. Usage: api_smoke.sh [base_url] [site]
BASE=${1:-http://localhost:8100}; SITE=${2:-cs.stackexchange.com}
PASS=0; FAIL=0
check() {  # expected_status path [grep_pattern]
  local exp=$1 path=$2 pat=$3 body code t
  body=$(curl -s -o /tmp/r2_smoke.$$ -w '%{http_code} %{time_total}' "$BASE$path")
  code=${body% *}; t=${body#* }
  if [ "$code" = "$exp" ] && { [ -z "$pat" ] || grep -q "$pat" /tmp/r2_smoke.$$; }; then
    PASS=$((PASS+1)); r=PASS; else FAIL=$((FAIL+1)); r=FAIL; fi
  printf '%-4s %s %5ss %s\n     %s\n' "$r" "$code" "$t" "$path" "$(head -c 300 /tmp/r2_smoke.$$)"
}
check 200 "/api/meta" '"dump_date": "2024-04-02"'
check 200 "/api/sites" '"n_sites"'
check 200 "/api/sites?group=A" '"domain_group": "A"'
check 200 "/api/metrics?metric=questions_count&sites=$SITE&from=2019-01&to=2019-03" '"index_2019"'
check 200 "/api/metrics?metric=answered_rate&group=B&from=2024-01&to=2024-03" '"value": null'
check 200 "/api/metrics?metric=downvote_ratio&sites=$SITE" '"n_rows": 63'
check 400 "/api/metrics?metric=nope" INVALID_METRIC
check 400 "/api/metrics?metric=questions_count&from=2023-05&to=2022-01" INVALID_RANGE
check 400 "/api/metrics?metric=questions_count&from=2023-13" INVALID_RANGE
check 400 "/api/metrics?metric=questions_count&sites=$(seq -s, -f 's%g.x' 1 21)" TOO_MANY_SITES
check 404 "/api/metrics?metric=questions_count&sites=no.such.site" SITE_NOT_FOUND
check 404 "/api/nope" NOT_FOUND
# CORS header
curl -s -D - -o /dev/null "$BASE/api/meta" | grep -i '^Access-Control-Allow-Origin: \*' >/dev/null \
  && { PASS=$((PASS+1)); echo "PASS CORS header"; } || { FAIL=$((FAIL+1)); echo "FAIL CORS header"; }
rm -f /tmp/r2_smoke.$$
echo "== $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ]
