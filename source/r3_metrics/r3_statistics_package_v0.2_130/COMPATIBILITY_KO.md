# 호환성

대상은 Python 2.7.8 / Spark 1.3.0 / CDH 5.4.3 / Java 7입니다. 기존 v0.1의 SQLContext, 명시 스키마, 연도별 고유 임시 테이블, 구형 CAST 대응을 유지합니다. 130사이트 확장은 표준 라이브러리만 추가 사용합니다.

Python 3 호스트 테스트는 수업 VM의 Python 2.7 및 실제 Spark/Hive 실행을 대체하지 않습니다. 검증 현황은 VALIDATION_KO.md를 따릅니다.
