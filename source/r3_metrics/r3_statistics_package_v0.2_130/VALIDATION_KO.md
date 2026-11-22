# v0.2.0 검증 범위

기존 11/19 수업 VM 성공 기록은 v0.1의 10사이트/630행에 한정됩니다. 새 패키지의 130사이트 전체 VM 계산이나 실제 Hive 연결 완료를 뜻하지 않습니다.

호스트 회귀 검사는 130사이트 분류, scope 해시, 완료/부분 판정, 누락/중복/잘못된 군/NULL·비율, 입력 변경 감지, Spark 영수증 누락, Hive 옵션 전달, 순위합 동적계획법을 검사합니다. 외부 작업을 대역으로 실행한 완료 테스트를 실제 VM 계산 성공으로 보고하지 않습니다.

대조 기준은 기존 로컬 전수 결과의 130사이트/8,190행을 최신 라벨로 연결한 값입니다. VM에서는 test_vm.sh 성공 후 experiment.json, comparison.json, full/validated.json과 실제 로그를 보관합니다. 전체 성공 기준은 full_scope=true, rows=8190, sites 130개, A118/B12, 로컬 대조 match=true와 mismatched_cells=0입니다.

## 2026-11-22 실제 수업 VM 추가 확인

- 배포본 SHA256 무결성 검사 통과.
- Python 2.7.8에서 기존 42개 단위 테스트 통과(실제 Spark 계산과 별개).
- 기본 Spark 설정으로 HiveContext 초기화 시 Derby 클래스 충돌 재현. 기존 외부 SPARK_CONF_DIR 누락에 따른 실행 설정 문제였으며, 이전 해결 완료 기록을 취소하지 않습니다.
- /tmp/r3-hive-probe.VXZzHf/conf의 기존 필터 설정과 R2 Hive 설정 경로를 적용하자 Hive 초기화를 지나 DESCRIBE FORMATTED qa_decline.posts까지 도달했습니다.
- 해당 VM에는 default DB와 accountdevice/device/webpage 실습 테이블만 있으며 qa_decline.posts가 없어 Error 10001로 실패했습니다. 이번 Hive 지표 생성·로컬 대조는 완료되지 않았습니다.
- R1 공식 HDFS 완료 status는 기존 10사이트/30개입니다. 130사이트 Hive 전체 계산은 R1/R2 입력 준비 뒤 수행해야 합니다.
- 기존 검증된 필터를 spark-conf/spark-env.sh에 포함하고 Hive driver에 --hive-conf-dir를 전달하도록 배포본을 보완했습니다.

최종 확인: 새 패키지의 spark-conf 프로필을 사용한 실제 Spark HiveContext에서 SHOW DATABASES 및 SHOW TABLES IN default가 성공했습니다. 결과는 databases=[default], tables=[accountdevice, device, webpage]입니다. 최종 VM Python 2.7 회귀 테스트 43개도 통과했습니다. 최초 42개 테스트 실행과 구분합니다.

## 후속 검증 완료: Hive 10사이트

2026-11-22 qa_decline 외부 테이블 3개와 완료 status 기반 365개 파티션을 등록했습니다. 30개 site/table의 Impala 행 수가 R1 status와 모두 일치했습니다. Spark 1.3의 캐시된 Hive 관계 자체 조인 오류를 분산 RDD+명시 스키마 재연결로 해결했습니다.

실행 hive10-20261122-02: input_mode=hive, 10사이트/630행, 로컬 대조 8,820셀 불일치 0, 산출물 해시 일치, 통합 Parquet 저장·재읽기 성공. full_scope=false이며 130사이트 전체 실행의 성공을 뜻하지 않습니다. 성공 재실행 약 665.954초(별도 로컬 대조 제외). 이전의 입력 테이블 미등록 상태는 최초 점검 당시의 기록입니다.
