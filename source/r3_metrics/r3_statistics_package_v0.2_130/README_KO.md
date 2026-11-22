# R3 130사이트 통계 패키지 v0.2.0

사용자가 제공한 적격 본 사이트 146개 분류표에서 A118/B12인 130개만 선택합니다. 영어 Stack Overflow, meta, C군은 제외합니다. 기존 10사이트 v0.1과 별도 패키지이며 기존 VM 성공 기록을 변경하지 않습니다.

## 입력과 완료 기준

- 수업 VM: CentOS 6.6 / Spark 1.3 / Python 2.7 / Java 7.
- R1 v0.3, dump_date=2024-04-02, HDFS /project/parquet. 대상 사이트의 posts/users/votes 및 완료 status가 필요합니다.
- 이번 작업은 R2 Hive 원본 테이블을 입력으로 사용합니다. 모든 실행에 --input-mode hive --hive-database qa_decline를 지정합니다.
- 목록 sites_130.txt, 군 selected_sites.json, 근거 classification_evidence.json 및 classification_source.md.
- 네 지표: 월별 질문 수, 신규 계정 수, 7일 내 답변률, 다운보트 비율. 기존 SQL과 지표 정의를 유지합니다.
- 2019-01~2024-03, 사이트당 63행. 건수·투표는 2024-02, 답변률은 2024-01까지 유효하며 나머지는 NULL입니다.
- 전체 완료는 정확히 130사이트 × 63개월 = 8,190행, A118/B12, 입력/출력 검증 및 통합 Parquet 저장 성공 시에만 full_scope=true입니다.
- --site 부분 실행도 complete=true가 될 수 있으나 이는 선택한 부분 작업 완료입니다. full_scope=false이므로 130사이트 전체 완료로 보고하지 않습니다. full_pilot은 항상 false인 호환 필드입니다.
- 기존 10사이트만 성공한 630행은 전체 완료가 아닙니다. 전체 A/B 통계는 전체 범위 실행에만 생성합니다.
- scope_reference_audit.json은 기존 로컬 전수 월 지표 중 130사이트를 추출하고 이번 군 라벨로 연결한 대조 기준입니다. 이 기준의 존재는 VM 130사이트 실행 성공을 의미하지 않습니다.

## 설치와 실행

```bash
cd /home/training
tar -xzf /실제/공유경로/r3_statistics_package_v0.2_130.tar.gz
cd r3_statistics_package_v0.2_130
sha256sum -c SHA256SUMS.txt

# 먼저 1사이트 입력 검사. 매 실행에는 새로운 ID 사용.
bash run_r3.sh --input-mode hive --hive-database qa_decline --preflight --site cs.stackexchange.com --run-id scope130-preflight-001
bash run_r3.sh --input-mode hive --hive-database qa_decline --site cs.stackexchange.com --run-id scope130-trial-001

# R1의 130사이트 × 3테이블이 모두 준비된 뒤 전체 계산
bash run_r3.sh --input-mode hive --hive-database qa_decline --run-id scope130-full-001

# test_vm.sh는 Parquet 시험도 포함하는 별도 검증 도구입니다.
# 이번 Hive 전용 절차는 위 run_r3.sh 명령과 로컬 기준 대조를 사용합니다.
```

기본 결과: 패키지 results/<run-id>, batch_logs/<run-id>, HDFS /project/statistics/<run-id>. --work-root, --log-root, --output-base로 변경할 수 있습니다. 기존 실행 경로는 덮어쓰지 않습니다. 실행 중 같은 입력을 R1/R2에서 변경하지 마세요.

R4 전달물: site_metrics_monthly.csv(7열), HDFS site_metrics_monthly, config와 selected_sites의 분류 버전, validated.json. 감사 결과는 monthly_metrics_audit.json(14열)입니다. 통합 검증은 8,190행 × 14열 = 114,660셀을 대조합니다.

## 분류 및 검증 범위

분류표는 8사이트 이견 47문항의 사용자 조정과 나머지 기존 판정을 반영합니다. 130사이트 전체에 대한 독립 검토 완료를 주장하지 않습니다. 변경된 군의 비교 통계는 새 패키지에서 다시 계산합니다. 이전 결과를 본 이후의 패키지화·분류 조정 이력을 유지합니다.

출처와 호스트/VM 검증 범위는 VALIDATION_KO.md, 통계 방법은 METHODS_KO.md를 확인하세요.

## 기존 Hive/Derby 해결 설정 재사용

이 VM에서 이미 검증한 클래스패스 필터를 spark-conf/spark-env.sh에 포함했습니다. run_r3.sh는 외부 SPARK_CONF_DIR 지정이 없으면 이 패키지 설정을 사용합니다. 시스템 /etc/spark/conf는 수정하지 않습니다. 기존 별도 설정을 쓰려면 R3_SPARK_CONF_DIR로 지정합니다.

Hive 입력은 --input-mode hive를 명시합니다. --hive-conf-dir 기본값은 /etc/hive/conf이며 그 경로를 Spark driver classpath에 추가해 R2 metastore의 hive-site.xml을 읽습니다. 설정이 다른 환경에서는 실제 경로로 변경합니다.

2026-11-22 후속 검증: qa_decline과 원본 테이블·365개 파티션을 등록한 뒤 Hive 입력 10사이트 계산 및 로컬 630행/8,820셀 대조에서 불일치 0을 확인했습니다. 130사이트 전체 실행은 입력 준비 후 수행해야 합니다.
