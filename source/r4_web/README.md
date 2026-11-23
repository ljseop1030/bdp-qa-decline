# r4_web — 대시보드 (R4 김경윤)

> 실제 API 연결: R2 API(`source/r2_storage/api/start_api.sh`, 포트 8100)를 띄운 뒤 `http://localhost:8000/?api=http://localhost:8100`.
> `pilot_data.js`(파일럿 데이터로 만든 생성 파일)는 커밋하지 않는다. 목 모드로 보려면 `../r4_q2/build_ui_data.py`로 다시 만든다.
> `/api/trend`가 아직 없으면 Q2 추세 화면은 안내 문구만 보여 준다.

# Mock UI: Q&A community decline tracker (R4, Phase 2)

Dashboard with three views (11/20: added the domain-group view, Q2 v2 data):

- **Compare sites**: pick sites and a metric, see monthly lines (2019 = 100 or raw).
- **Domain groups (A vs B)**: monthly median line + Q1 to Q3 band per group, computed in the browser from `/api/metrics?group=A|B` (a month needs at least 3 sites with data). Shows the Q2 A-vs-B DID as a note when `/api/group_summary?metric=` exists (optional).
- **Q2 trend vs actual**: one site, its pre-LLM trend extended past Nov 2022, the placebo range, and the gap.

It follows R2's query API spec (`/api/meta`, `/api/sites`, `/api/metrics`) plus `/api/trend`
(site_trend_model schema v1), so the same page works against the mock now and the real API later.

**Data: R3's local pilot (10 real sites, 2019-01 to 2024-02) and the Q2 trial tables**, bundled in
`pilot_data.js`. This is not yet read from Hive/Impala; it switches to the live API with `?api=`.

## Rebuild the data file

`pilot_data.js` is generated, not hand-written. From the `q2/` folder (Python 3 + pandas + numpy):

```bash
python3 q2_trend_model_v2.py --input monthly_metrics_audit.json --out v2/out_pilot
python3 build_ui_data.py --input monthly_metrics_audit.json --q2 out_pilot --ui ../r4_web   # only is_primary models are exported
```

`--input` also accepts R3's `site_metrics_monthly_*.parquet`. With pyarrow installed, the Q2 tables are also
written as Parquet 1.0, data page v1, Snappy, no dictionary (Impala 2.2.0-friendly) for R2 to load.

## Files

| File | Role |
|---|---|
| `index.html` | Page layout |
| `style.css` | Styles (Flexbox only) |
| `app.js` | API layer, controls, chart |
| `mock_api.js` | Mock of R2's API spec (same shapes and error codes) + `/api/trend` |
| `pilot_data.js` | Generated data: R3 pilot metrics + Q2 trial tables |
| `Chart.min.js` | Chart.js 2.9.4, bundled locally (no CDN, for reproducibility) |

## Run on the course VM

From the Mac (UTM), copy the folder into the VM:

```bash
scp -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa \
    -r mock-ui training@192.168.64.2:/data/work/
```

In the VM:

```bash
cd /data/work/mock-ui
python -m SimpleHTTPServer 8000
```

Open `http://localhost:8000` in the VM's Firefox (type `http://`, not `https://`).
From the Mac browser: `http://192.168.64.2:8000`.

## URL switches

| URL | Behaviour |
|---|---|
| `/` | Mock API on pilot data (default) |
| `/?data=synthetic` | Old synthetic series (no trend view) |
| `/?api=http://localhost:5000` | Calls the real API at that base URL |
| `/?fail=1` | Mock returns `QUERY_FAILED` to check the error view |

When R2's API is served from a different port than this page, the API must send
`Access-Control-Allow-Origin` (or serve this folder from the API server itself).

## What the screen handles

- `null` vs `0`: missing months break the line (`spanGaps: false`) and are listed under the chart.
- `index_2019 = null`: sites with no 2019 data get no index line; a note says so.
- `answered_rate`: stops at its last valid month from `/api/meta` (`metric_last_month`, R3 rule: 7-day answer window);
  the page does not decide the cutoff itself. Falls back to `coverage_last_month - excluded_months_tail` if the field is missing.
- Summary table: every site is shown at **one reference month** (earliest of "To", the metric's last valid month, the data end),
  so sites are never compared across different months. A site with no value that month shows `N/A` and its own last valid month.
- Period: "To" defaults to `coverage_last_month` from `/api/meta` (2024-02), so the unobserved 2024-03 is not drawn.
- Trend view: grey band = where the actual line would sit if the method erred as it did at fake breaks in 2021;
  the "outside placebo range" flag uses the same range, so chart and table always agree.
- Trend view is labelled "Pilot, 10 sites. Exploratory": R3's sensitivity check (10/5) showed results change with fit start,
  method (DID vs trend) and fake break dates, so it is not shown as evidence of an LLM effect.
- Error codes: `INVALID_METRIC`, `INVALID_RANGE`, `TOO_MANY_SITES`, `SITE_NOT_FOUND`, `QUERY_FAILED`, plus no response.
- Marker on Dec 2022 (ChatGPT released 30 Nov 2022; the line is the first full month after it, same as the Q2 baseline month, and the label says so) and a dotted line at index 100.

## Browser constraints (Firefox 31.5 on the VM)

Code is ES5 only, checked with `acorn --ecma5`:
no `fetch` (uses `XMLHttpRequest`), no template literals, no `let`/`const`,
no arrow functions, no CSS Grid. UI text is English because the VM may lack Korean fonts.

## `/api/trend` (proposed to R2)

`GET /api/trend?site=<host>&metric=<metric>` returns the `site_trend_model` rows for that site and metric
(`month, segment, actual, counterfactual, band_low, band_high, gap`) and one `site_trend_summary` row as `meta`
(`model, fit_start, fit_end, post_start, post_end, pre_trend_pct_per_year, post_gap_pct, placebo_min,
placebo_max, beyond_placebo, break_month, break_strength`). Same error codes as `/api/metrics`.

## `/api/meta` fields this page reads

`dump_date`, `coverage_last_month`, `excluded_months_tail`, `answer_window_days`, `trend_available`, and
`metric_last_month` (proposed: `{metric: "YYYY-MM"}`, last valid month per metric). R2 agreed on 10/3 to add
per-metric valid ranges to `/api/meta`; the field name is still to be confirmed with R2.

## Q2 v2 notes (11/20)

- `/api/trend` meta carries `unit`: `index` (values are 2019=100, gaps in %) or `rate` (values are ratios 0..1, the page shows %; gaps in percentage points). Also `post_gap_12m`, `placebo_values`, `n_fit_months`, `note`.
- Swapping in the real API: only `api()` in `app.js` talks to the data source (`?api=<base>`); `/api/group_summary` is a proposal to R2 and the page works without it.
- Not yet checked on Firefox 31.5 in the VM (ES5 syntax checked with `acorn --ecma5`; Chrome-class browser used for the visual check).
