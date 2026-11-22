# r3_metrics — R3 지표 산출

R2의 `qa_decline.posts`, `users`, `votes`를 Spark로 읽어 월별 지표와 A/B 비교 통계를 계산합니다.
수업 VM의 CentOS 6.6 / CDH 5.4.3 / Spark 1.3 / Java 7 / Python 2.7을 사용하며 추가 pip 설치는 필요하지 않습니다.

| 경로 | 내용 |
|---|---|
| `load/run_r3.sh`, `load/preflight_check.sh` | 실행·입력 사전 점검 |
| `load/run.py`, `load/spark_job.py` | Hive 입력 검증, Spark 집계, Parquet 저장·재읽기 |
| `load/report.py`, `load/common.py` | 통계·그래프, 입력·출력 계약 |
| `load/config.json`, `load/selected_sites.json`, `load/sites_130.txt` | 기간·관측창·대상 130사이트(A118/B12) |
| `load/classification_evidence.json`, `docs/classification_source.md` | 사이트 분류 근거 |
| `load/spark-conf/spark-env.sh` | 수업 VM Spark/Hive 클래스패스 설정 |
| `sql/metrics.sql` | 질문 수·신규 계정 수·7일 내 답변률·다운보트 비율 |
| `sql/register_serving.hql` | R2 서빙 테이블 연결 |
| `tests/` | 단위 테스트, Spark smoke, VM 대조 도구와 집계 기준 |
| `docs/` | 지표 정의·호환성·기존 VM 검증 기록 |

## 순서

아래 명령은 `source/r3_metrics`에서 실행합니다. R2의 서비스 기동·DDL 생성·완료 파티션 등록을 먼저 끝냅니다.
R1/R2가 같은 입력을 변경하는 동안 R3를 실행하지 않습니다.

```bash
cd source/r3_metrics
sha256sum -c SHA256SUMS.txt
export R3_PYTHON=/usr/local/bin/python2.7

# 1) R2 입력 확인
hive -S -e 'DESCRIBE qa_decline.posts; SHOW PARTITIONS qa_decline.posts;'
hdfs dfs -cat /project/parquet/_status/cs.stackexchange.com.posts.json

# 2) 한 사이트 사전 점검과 실제 실행. 기본 입력 모드는 hive.
bash load/preflight_check.sh --site cs.stackexchange.com --run-id trial-001
bash load/run_r3.sh --site cs.stackexchange.com --run-id trial-001

# 3) 130사이트의 입력이 모두 준비되면 전체 실행
bash load/run_r3.sh --run-id full-001

# 4) 완료 확인
cat results/full-001/validated.json
hdfs dfs -cat /project/statistics/full-001/_R3_SUCCESS.json
```

실제 실행마다 새 `--run-id`를 사용합니다. 한 사이트 성공은 `SUBSET ONLY`, 63행이며
전체 성공은 `full_scope=true`, 130사이트·8,190행입니다. 부분 실행은 전체 A/B 통계를 만들지 않습니다.
사전 점검은 파일·status·환경 확인이고, Hive 연결·스키마·행 수 검증은 실제 Spark 실행에서 수행합니다.

| 옵션 | 기본값 |
|---|---|
| `--input-mode` | `hive` (`parquet` 지정 시 R1 파일 직접 읽기) |
| `--hive-database` | `qa_decline` |
| `--hive-conf-dir` | `/etc/hive/conf` (`HIVE_CONF_DIR` 지정 시 해당 경로) |
| `--input-root` | HDFS `/project/parquet` |
| `--output-base` | HDFS `/project/statistics` |
| `--work-root` | `r3_metrics/results` |
| `--log-root` | `r3_metrics/batch_logs` |
| `--driver-memory` | `768m` |

서빙 등록 SQL의 `r3_serving_uri`에는 이름노드 주소가 포함된 완전한 HDFS URI를 사용합니다.
같은 R2 metastore의 `hive-site.xml`을 사용해야 합니다. 별도 Spark 설정은 `R3_SPARK_CONF_DIR`로 지정합니다.
HDFS 경로 옵션은 `hdfs:///...` URI가 아닌 `/project/...` 형식으로 입력합니다.

## R2 입력 형식

| 테이블 | 데이터 열(순서·타입) | 파티션 |
|---|---|---|
| `posts` | `id BIGINT, post_type_id INT, creation_date BIGINT, parent_id BIGINT, month INT` | `site STRING, year INT` |
| `users` | `id BIGINT, creation_date BIGINT, month INT` | `site STRING, year INT` |
| `votes` | `id BIGINT, vote_type_id INT, creation_date BIGINT, month INT` | `site STRING, year INT` |

LOCATION은 `/project/parquet/<table>/site=<site>/year=<YYYY>`입니다.
`creation_date`는 UTC Unix 초, `month`는 정수 1~12이며 `site`는 호스트명입니다.
R2 조회 API나 7열 서빙 결과를 입력으로 읽는 방식이 아닙니다.

`/project/parquet/_status/<site>.<table>.json`에 `site`, `table`, `rows`,
오름차순·중복 없는 정수 `years`, `finished_at`, `parser_version=v0.3`, `dump_date=2024-04-02`가 필요합니다.
R3는 LOCATION, 등록 연도, 실제 행 수, 필수값, 사이트 내 ID 중복 및 실행 전후 status 변경을 검사합니다.
Hive 모드는 연도 폴더의 Parquet 파일명을 제한하지 않습니다. 여러 part 파일도 Hive 테이블로 읽습니다.
`--input-mode parquet` 직접 읽기는 R1의 연도별 `part-YYYY.parquet` 파일명을 사용합니다.

## 결과 저장과 R2/R4 연결

7열 순서는 `site STRING, month STRING, domain_group STRING, questions_count BIGINT,
new_users_count BIGINT, answered_rate DOUBLE, downvote_ratio DOUBLE`입니다.
키는 `(site, month)`, 월은 `YYYY-MM`, 비율은 0~1이며 NULL과 0을 구분합니다.
2019-01~2024-03을 보존하고 관측 가능한 범위 밖의 값은 NULL입니다.

| 위치 | 내용 |
|---|---|
| HDFS `/project/statistics/<run-id>/site_metrics_monthly` | Snappy Parquet, 여러 part 파일 가능 |
| `results/<run-id>/site_metrics_monthly.csv` | 같은 7열, NULL은 빈 칸 |
| `results/<run-id>/monthly_metrics_audit.json` | 분자·분모·관측 플래그 |
| `results/<run-id>/validated.json` | 완료 여부·입력/출력 해시 |
| `results/<run-id>/REPORT_KO.md`, `results/<run-id>/charts/` | 통계 보고서·SVG 그래프 |
| `batch_logs/<run-id>/` | 사이트별 Spark 및 통합 로그 |

성공 결과를 확인한 뒤 R2 서빙 테이블의 LOCATION을 해당 실행으로 연결합니다.
다음 명령은 기존 서빙 테이블의 조회 대상을 바꾸므로 R2 담당자와 실행 경로를 맞춰 사용합니다.

```bash
R3_FS=$(hdfs getconf -confKey fs.defaultFS)
hive --hiveconf r3_serving_uri="${R3_FS%/}/project/statistics/full-001/site_metrics_monthly" -f sql/register_serving.hql
impala-shell -q 'INVALIDATE METADATA qa_decline.site_metrics_monthly; REFRESH qa_decline.site_metrics_monthly;'
impala-shell -q 'SELECT COUNT(*) FROM qa_decline.site_metrics_monthly; SELECT site, COUNT(*) FROM qa_decline.site_metrics_monthly GROUP BY site;'
```

전체 결과는 총 8,190행·130사이트·사이트당 63행인지 확인합니다.
이어서 R2 폴더의 `api/start_api.sh`와 `tests/api_smoke.sh`로 API를 확인합니다.
이미 실행 중인 API는 60초 캐시가 만료된 뒤 확인하거나 R2의 중지·시작 스크립트로 재기동합니다.
R4에는 7열 결과와 `validated.json`, `load/config.json`, `load/selected_sites.json`을 전달합니다.
결과·덤프·로그는 Git에 추가하지 않습니다.

## 테스트와 오류 확인

```bash
# 현대 Python 호스트 단위 테스트
python -B -m unittest discover -s tests -p 'test_*.py' -v

# 수업 VM 합성 데이터와 Parquet 저장·재읽기
bash tests/smoke_test.sh --output /project/statistics/smoke-001

# 별도 종합 시험: Parquet 경로 시험도 포함하며 전체 130사이트 입력 필요
bash tests/test_vm.sh --run-id verify-001 --check-hive --hive-database qa_decline
```

실패 시 `results/<run-id>/failure.json`, `batch_logs/<run-id>/failure.json`과 사이트 로그를 확인합니다.
입력 문제를 해결한 뒤 새 실행 ID로 재실행합니다. 기존 결과를 덮어쓰거나 자동 삭제하지 않습니다.
현재 구조에서 실제 VM의 Python 2.7 테스트 46개, Spark 1.3 합성 시험, Hive 입력 10사이트·630행 계산과
기준값 대조, Hive·Impala 서빙 조회를 통과했습니다. 상세 결과는 `tests/RESULTS_2026-11-22.md`,
기존 이력은 `docs/VALIDATION_KO.md`에 있습니다. 130사이트 전체 VM 완료 기록은 없습니다.
