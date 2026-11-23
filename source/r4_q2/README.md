# r4_q2 — Q2 추세 모델 (R4 김경윤)

LLM 이후 변화가 기존 추세의 연장인지 단절인지, 크기는 얼마인지 잰다 (Q2 방법 v2).
**VM 밖 Python 3 + numpy/pandas (+ pyarrow: Parquet 출력)** 에서 실행한다.

| 파일 | 역할 |
|---|---|
| `q2_trend_model_v2.py` | 사이트별 추세 적합·반사실·placebo, A vs B DID + 사이트 단위 순열검정 → `site_trend_model`, `site_trend_summary`, `q2_group_summary` |
| `make_synthetic.py` | 정답을 아는 합성 데이터 (방법 검증용) |
| `build_ui_data.py` | 결과를 목 UI용 `pilot_data.js`로 변환 (is_primary 모델만) |

## 실행
```bash
pip install numpy pandas pyarrow
# 입력: R3 site_metrics_monthly (parquet / csv / json)
# 사이트 목록: R3 selected_sites.json 형식 {"sites": {"host": "A"|"B"}} 그대로 사용 (하드코딩 없음)
python3 q2_trend_model_v2.py \
  --input site_metrics_monthly.parquet \
  --sites ../r3_metrics/load/selected_sites.json \
  --n-perm 9999 --seed 527 \
  --out out
```
- 순열검정: 가능한 조합 수 ≤ `--n-perm`이면 전수 열거(exact), 넘으면 몬테카를로(random). 12 vs 12는 C(24,12)≈270만이라 몬테카를로.
- 출력 Parquet: version 1.0, data page v1, Snappy, 사전 인코딩 끔 (Impala 2.2.0 호환).
- 결과(`out/`)는 커밋하지 않는다 (.gitignore).

## 사양 (팀 합의, 바꾸지 말 것)
적합 2019-01~2022-11, 기준 2022-12, 평가 ~2024-02 (answered_rate ~2024-01). 건수는 로그 적합 + Duan smearing, 비율은 원값(%p).
placebo 2020-07 / 2021-01 / 2021-07 / 2021-11 (12개월 창). DID = 사이트 FE + 연월 FE + A×post.
