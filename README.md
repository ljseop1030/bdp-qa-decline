# Q&A 커뮤니티 쇠퇴 추적 (Stack Exchange 2024Q1 덤프, Hadoop·Spark·Hive·Impala)

Stack Exchange 2024Q1 데이터 덤프를 Hadoop·Spark·Hive·Impala로 적재·가공하여 Q&A 커뮤니티의 쇠퇴 양상을 추적하는 팀 프로젝트입니다.

## 저장소 구조

```
.
├── README.md
├── MANUAL.pdf              (예정)
├── SHA256SUMS.txt
├── data_acquisition/       R1 임준원 — 원천 데이터 수집
└── source/
    ├── r1_ingest/          R1 임준원 — 수집·변환
    ├── r2_storage/         R2 이지섭 — DDL·적재 스크립트·조회 API
    ├── r3_metrics/         R3 김준우 — 지표 산출
    ├── r4_q2/              R4 김경윤
    ├── r4_web/             R4 김경윤 — 웹
    └── quick_repro/        빠른 재현용
```

## 규칙

- 데이터(Parquet·7z·CSV 덤프)는 커밋하지 않습니다. `.gitignore`에 등록되어 있습니다.
- 각자 자기 폴더에만 올립니다.
- `.sh` 파일은 LF 줄바꿈을 유지합니다 (`.gitattributes`로 강제).
