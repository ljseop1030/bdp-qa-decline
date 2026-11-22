# R3 지표 산출 — 팀 저장소 반영본

## 저장소에 넣는 방법

이 ZIP을 팀 저장소 루트에 압축 해제하면 `source/r3_metrics/`만 추가됩니다.
기존 루트 README·.gitignore·.gitattributes 및 R1/R2/R4 파일을 교체하지 않습니다.
`r3_metrics_repo_ready` 같은 포장 폴더가 저장소 안에 한 겹 더 들어가지 않게 합니다.
ZIP 자체를 커밋하지 말고 압축 해제된 소스를 커밋합니다.

팀 저장소 루트에서 추가 파일과 제외 규칙을 확인합니다.

```bash
git status --short --untracked-files=all -- source/r3_metrics
git check-ignore source/r3_metrics/r3_statistics_package_v0.2_130/results/check.csv
git add -- source/r3_metrics
git diff --cached --stat -- source/r3_metrics
```

## 포함된 패키지

`r3_statistics_package_v0.2_130/`은 최신 Hive 10사이트 검증 이력을 포함한 v0.2.0 소스입니다.
분류·설정·Spark 설정·SQL·시험 코드·문서·집계 대조용 JSON을 함께 유지했습니다.
`scope_reference_audit.json`은 시험 도구가 사용하는 월별 집계 기준이며 원천 덤프가 아닙니다.
원본 CSV/XML/Parquet/7z, 실행 결과·로그·캐시, 압축 배포본은 포함하지 않습니다.

- 분석 범위: 본 사이트 130개(A118/B12), meta 제외, 2019-01~2024-03.
- 입력: R1 v0.3 / dump_date=2024-04-02, HDFS `/project/parquet` 및 완료 status.
- 환경: 수업 VM Python 2.7 / Spark 1.3 / Java 7.
- 지표 정의·NULL 정책·관측 범위는 패키지 README_KO.md와 METHODS_KO.md를 따릅니다.

## 무결성과 실행

팀 저장소 루트에서 아래 명령을 실행합니다. 셸 스크립트는 LF이며 `bash`로 실행합니다.

```bash
cd source/r3_metrics
sha256sum -c SHA256SUMS.txt
cd r3_statistics_package_v0.2_130
sha256sum -c SHA256SUMS.txt
bash run_r3.sh --input-mode hive --hive-database qa_decline --preflight --site cs.stackexchange.com --run-id r3-team-preflight-001
bash run_r3.sh --input-mode hive --hive-database qa_decline --site cs.stackexchange.com --run-id r3-team-trial-001
```

실행 ID는 매 실제 실행마다 새로 지정합니다. Hive 모드는 R2 원본 테이블·완료 파티션과
동일 metastore 설정이 준비되어야 합니다. Parquet 직접 읽기는 `--input-mode parquet`입니다.
130사이트 전체 입력이 준비되면 `--site`를 생략해 전체 실행합니다.
현대 Python 호스트에서 단위 테스트만 실행하려면 패키지 폴더에서:

```bash
python -B -m unittest discover -s tests -p 'test_*.py' -v
```

## R2/R4 연결

결과는 HDFS `/project/statistics/<run-id>/site_metrics_monthly` 및 로컬
`results/<run-id>/`에 생성됩니다. R2 기존 서빙 LOCATION
`/project/serving/site_metrics_monthly`와 다르므로 R2 담당자가 성공 실행의 경로로
외부 테이블 LOCATION을 연결하고 Hive/Impala 조회를 검증해야 합니다.
`site_metrics_monthly_schema.sql`은 참고 DDL이며 R3가 자동 실행하지 않습니다.
기존 R2 DDL은 CREATE IF NOT EXISTS 방식이므로 이미 있는 테이블의 LOCATION을
다시 생성 명령만으로 바꿀 수 없습니다. R2 담당자가 변경 절차를 적용합니다.

7열 순서: `site, month, domain_group, questions_count, new_users_count, answered_rate, downvote_ratio`.
R4에는 성공한 실행의 7열 결과, `validated.json`, config·분류 버전을 별도로 전달합니다.
생성 CSV·Parquet과 실행 로그를 Git에 추가하지 않습니다.

## 검증 범위

기존 패키지 문서의 후속 VM 검증 기록은 Hive 입력 10사이트/630행,
로컬 대조 8,820셀 불일치 0입니다. 130사이트 전체 완료를 뜻하지 않습니다.
전체 완료 기준은 `full_scope=true`, 130사이트, 8,190행입니다.
포장 시 확인한 호스트 테스트 및 파일 검사 결과는 PACKAGING_CHECK_KO.md를 참고하세요.
