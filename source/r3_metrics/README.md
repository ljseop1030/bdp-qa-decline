# r3_metrics — R3 지표 산출

R2의 `qa_decline.posts`, `users`, `votes`를 Spark로 읽어 월별 지표와 A/B 비교 통계를 계산합니다.
수업 VM의 CentOS 6.6 / CDH 5.4.3 / Spark 1.3 / Java 7 / Python 2.7을 사용하며 추가 pip 설치는 필요하지 않습니다.

| 경로 | 내용 |
|---|---|
| `load/run_r3.sh`, `load/preflight_check.sh` | 실행·입력 사전 점검 |
| `load/run.py`, `load/spark_job.py` | Hive 입력 검증, Spark 집계, Parquet 저장·재읽기 |
| `load/report.py`, `load/common.py` | 통계·그래프, 입력·출력 계약 |
| `load/config.json`, `load/selected_sites.json`, `load/sites_24.txt` | 기간·관측창·기본 A12/B12 대상 |
| `load/classification_evidence.json`, `docs/classification_source.md` | 사이트 분류 근거 |
| `load/spark-conf/spark-env.sh` | 수업 VM Spark/Hive 클래스패스 설정 |
| `sql/metrics.sql` | 질문 수·신규 계정 수·7일 내 답변률·다운보트 비율 |
| `sql/register_serving.hql` | R2 서빙 테이블 연결 |
| `tests/` | 단위 테스트, Spark smoke, VM 대조 도구와 집계 기준 |
| `docs/` | 지표 정의·호환성·기존 VM 검증 기록 |

## 선정안 선택 (v0.3)

기본은 **현재 선정안 `category` (A12/B12)** 입니다. 규모 매칭은 별도 대안이며 기본 목록을 교체하지 않습니다.

| 옵션 | 범위 | 선정 기준 | 전체 완료 행 수 |
|---|---|---|---:|
| `--scope category` (기본) | A12/B12 | A 판정 90% 이상, 기존 A군 카테고리 비율 6/3/2/1, 정렬 구간 중앙 | 1,512 |
| `--scope size-matched` | A12/B12 | A 판정 90% 이상, 2022년 1~10월 질문 수 1:1 매칭, 카테고리 배정 없음 | 1,512 |
| `--scope legacy130` | A118/B12 | 이전 130사이트 목록, 명시적 재현용 | 8,190 |

```bash
# source/r3_metrics에서 실행. 실행마다 별도 run-id를 사용합니다.
bash load/preflight_check.sh --scope category --run-id category-check-001
bash load/run_r3.sh --scope category --run-id category-001
bash load/run_r3.sh --scope size-matched --run-id matched-001

# 선택한 범위에 맞춰 실제 VM 계산과 로컬 기준값을 대조합니다.
bash tests/test_vm.sh --scope category --run-id verify-category-001
bash tests/test_vm.sh --scope size-matched --run-id verify-matched-001

# VM/HDFS 없이 설정·완료 판정 로직 검증 (Spark 실행 완료 증거가 아님)
python -m unittest discover -s tests -p 'test_*.py'
```

`load/scopes/<이름>/`에 `config.json`, `selected_sites.json`, `classification_evidence.json`, `sites.txt`가 있습니다. 기본 `load/config.json`, `load/selected_sites.json`, `load/classification_evidence.json`은 현재 선정안의 활성 파일입니다. R1/R2에는 실행할 프로필의 `sites.txt`를 전달합니다. 두 안을 모두 준비한다면 `load/scopes/sites_union.txt`의 **34개** 사이트를 수집·적재합니다. 분석 실행은 각 24사이트로 별도 수행합니다.

새 선정안은 `load/scopes/<새이름>/`을 추가해 `--scope <새이름>`으로 선택할 수 있습니다. config의 `selection_file`, `classification_file`, `scope_id`, `selection_sha256`, `expected_sites`, `expected_groups`, `expected_rows`를 함께 갱신합니다. 경로는 `load/` 기준 상대 경로이며 목록의 `protocol_version`은 `scope_id`와 일치해야 합니다. 현재 월 계약은 사이트당 63행이며, 완료 수치는 실제 목록과 설정을 대조해 검증합니다. 분류 근거는 선정 사이트와 정확히 일치해야 합니다. 기본 선정안을 바꿀 때는 활성 파일과 `scopes/category/` 사본을 함께 갱신합니다. 패키지 파일 변경 후 `python tools/update_manifest.py`로 `SHA256SUMS.txt`를 재생성합니다.

완료 영수증 `validated.json`의 `scope_id`, `sites`, `target_sites`, `expected_rows`, `selection_sha256`로 어느 선정안의 결과인지 확인합니다. `full_scope=true`는 선택한 선정안 전체 완료이며 네트워크 전체 완료가 아닙니다. 두 실행은 별도 경로를 사용하고 R4에 선정안 이름을 함께 전달합니다.

VM 비교용 `tests/scope_reference_audit.json`은 이전 130사이트 로컬 기준값을 보존합니다. 비교 시 선택한 사이트만 추립니다. 새 후보가 이 기준 파일에 없으면 새 로컬 기준값을 만들어야 하며, 누락을 성공으로 처리하지 않습니다. VM 시험의 1사이트 점검 대상은 해당 범위의 첫 A 사이트이며, `--trial-site <호스트명>`으로 지정할 수도 있습니다.

선정 근거·범위 축소 배경·2022년 규모 비교는 [선정안 설명](docs/SELECTION_SCOPES_KO.md)을 참고하세요. 사이트 수 12:12가 활동 규모의 균형을 보장하지 않습니다. 과거 VM 검증 기록은 당시 범위의 이력이며 새 두 프로필의 VM 완료 증거로 재사용하지 않습니다.

## 순서

아래 명령은 `source/r3_metrics`에서 실행합니다. R2의 서비스 기동·DDL 생성·완료 파티션 등록을 먼저 끝냅니다.
R1/R2가 같은 입력을 변경하는 동안 R3를 실행하지 않습니다.

```bash
cd source/r3_metrics
sha256sum -c SHA256SUMS.txt
export R3_PYTHON=/usr/local/bin/python2.7

# 1) R2 입력 확인
hive -S -e 'DESCRIBE qa_decline.posts; SHOW PARTITIONS qa_decline.posts;'
hdfs dfs -cat /project/parquet/_status/civicrm.stackexchange.com.posts.json

# 2) 한 사이트 사전 점검과 실제 실행. 기본 입력 모드는 hive.
bash load/preflight_check.sh --site civicrm.stackexchange.com --run-id trial-001
bash load/run_r3.sh --site civicrm.stackexchange.com --run-id trial-001

# 3) 선택한 24사이트의 입력이 모두 준비되면 전체 실행
bash load/run_r3.sh --run-id full-001

# 4) 완료 확인
cat results/full-001/validated.json
hdfs dfs -cat /project/statistics/full-001/_R3_SUCCESS.json
```

실제 실행마다 새 `--run-id`를 사용합니다. 한 사이트 성공은 `SUBSET ONLY`, 63행이며
전체 성공은 `full_scope=true`, 24사이트·1,512행입니다. 부분 실행은 전체 A/B 통계를 만들지 않습니다.
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
같은 R2 metastore의 `hive-site.xml`을 사용해야 합니다. 두 선정안의 결과를 같은 서빙 테이블에 합치지 않습니다. R4에 제공할 실행 경로 하나를 선택하거나, 별도 테이블로 구분합니다. 별도 Spark 설정은 `R3_SPARK_CONF_DIR`로 지정합니다.
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

전체 결과는 총 1,512행·24사이트·사이트당 63행인지 확인합니다.
이어서 R2 폴더의 `api/start_api.sh`와 `tests/api_smoke.sh`로 API를 확인합니다.
이미 실행 중인 API는 60초 캐시가 만료된 뒤 확인하거나 R2의 중지·시작 스크립트로 재기동합니다.
R4에는 7열 결과와 `validated.json`, 실행에 사용한 프로필의 설정·사이트 목록을 전달합니다. 규모 매칭 실행에 기본 현재안의 목록을 잘못 붙이지 않습니다.
결과·덤프·로그는 Git에 추가하지 않습니다.

## 테스트와 오류 확인

```bash
# 현대 Python 호스트 단위 테스트
python -B -m unittest discover -s tests -p 'test_*.py' -v

# 수업 VM 합성 데이터와 Parquet 저장·재읽기
bash tests/smoke_test.sh --output /project/statistics/smoke-001

# 별도 종합 시험: 선택한 24사이트의 Parquet 입력 필요
bash tests/test_vm.sh --scope category --run-id verify-001 --check-hive --hive-database qa_decline
```

실패 시 `results/<run-id>/failure.json`, `batch_logs/<run-id>/failure.json`과 사이트 로그를 확인합니다.
입력 문제를 해결한 뒤 새 실행 ID로 재실행합니다. 기존 결과를 덮어쓰거나 자동 삭제하지 않습니다.
v0.2 당시 실제 VM의 Python 2.7 테스트 46개, Spark 1.3 합성 시험, Hive 입력 10사이트·630행 계산과
기준값 대조, Hive·Impala 서빙 조회를 통과했습니다. 상세 결과는 `tests/RESULTS_2026-11-22.md`,
기존 이력은 `docs/VALIDATION_KO.md`에 있습니다. v0.3 두 24사이트 프로필의 실제 VM 완료 기록은 아직 없습니다. 이번 호스트 검증은 `tests/RESULTS_2026-11-24.md`를 참고하세요.
