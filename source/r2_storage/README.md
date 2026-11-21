# r2_storage — R2 저장·서빙 (Stack Exchange Q&A 쇠퇴 추적)

수업 실습 VM(CDH 5.4.3, Hive 1.1, Impala 2.2, 시스템 Python 2.6)에서 추가 설치 없이 동작합니다.

| 경로 | 내용 |
|---|---|
| `sql/create_tables.hql` | 원천 테이블 `qa_decline.{posts,users,votes}` (site→year 파티션, EXTERNAL, LOCATION `/project/parquet/<table>`) |
| `sql/create_serving_test.hql` | 서빙 테이블 `qa_decline.site_metrics_monthly` (LOCATION `/project/serving/site_metrics_monthly`) |
| `sql/load_serving_test_from_csv.hql` | (테스트 데이터 전용) TSV → 텍스트 테이블 → Hive가 Parquet v1로 다시 쓰기. R3 노션 첨부본이 Parquet v2라 Impala 2.2가 거부해서 사용 |
| `load/register_partitions.py` | R1 status 기반 파티션 등록 (MSCK REPAIR 대체), SKIP/REPLACE, Impala REFRESH, 행 수 검증 |
| `load/check_site.sh` | 사이트 단위 무결성 스냅샷 (행 수, 고유 id, 중복 id, 합계, 파티션 수) |
| `api/r2_api.py` | 조회 API (포트 8100): `/api/meta`, `/api/sites`, `/api/metrics` |
| `api/start_api.sh`, `api/stop_api.sh` | API 기동/중지 (nohup, 로그 `/data/work/r2/logs/api.log`) |
| `tests/api_smoke.sh` | API 엔드포인트·오류 코드·CORS 점검 |

## 순서
```bash
# 0) 서비스 기동 (ZooKeeper 먼저)
for s in zookeeper-server hive-metastore hive-server2 impala-state-store impala-catalog impala-server; do
  sudo service $s status >/dev/null 2>&1 || sudo service $s start; done
# 1) 테이블
hdfs dfs -mkdir -p /project/parquet/_status /project/serving/site_metrics_monthly
hive -f sql/create_tables.hql
hive -f sql/create_serving_test.hql
impala-shell -q "INVALIDATE METADATA qa_decline.posts; INVALIDATE METADATA qa_decline.users; INVALIDATE METADATA qa_decline.votes; INVALIDATE METADATA qa_decline.site_metrics_monthly"
# 2) R1 파서가 status를 게시한 site/table 등록 (반복 실행해도 중복 없음)
python load/register_partitions.py            # 전체
python load/register_partitions.py --site beer.stackexchange.com --dry-run
# 3) API
api/start_api.sh && tests/api_smoke.sh http://localhost:8100
```

## 등록 규칙 (11/21 결정)
- status JSON이 있고 `dump_date=2024-04-02`, `parser_version=v0.3`, `years` = HDFS의 실제 `year=` 디렉터리일 때만 등록.
- ledger(`/data/work/r2/ledger.json`)와 비교: 같은 `finished_at` → SKIP, 바뀌면 → REPLACE(없어진 연도 DROP, 새 연도 ADD, REFRESH). append 없음.
- 등록 후 Impala `COUNT(*)` = status `rows`가 아니면 해당 site 파티션을 내리고 FAIL.
- status가 없는 사이트는 건드리지 않음 (MSCK는 처리 중·중단된 디렉터리까지 등록할 수 있어서 사용하지 않음).

## API 규칙
롱 포맷, 월 `YYYY-MM`, 결측 `null`과 0 구분, `index_2019`는 2019년 12개월이 모두 있고 평균 > 0일 때만 계산.
오류 `{"error":{"code","message"}}`: 400 `INVALID_METRIC`/`INVALID_RANGE`/`TOO_MANY_SITES`, 404 `SITE_NOT_FOUND`, 503 `QUERY_FAILED` (없는 경로는 404 `NOT_FOUND`).
