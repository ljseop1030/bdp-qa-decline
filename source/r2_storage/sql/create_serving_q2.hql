-- Serving tables for R4 Q2 results (q2_trend_model_v2.py output).
-- Location: /project/serving/<table>  (fixed path, decided 11/21)
--
-- site_trend_model   : site x metric x month x model_id  (v1 columns + unit)
-- site_trend_summary : site x metric x model_id          (v1 columns + v2 columns)
-- q2_group_summary   : metric (A vs B DID), CSV as given by R4
--
-- Column order follows q2_trend_model_v2.py (mcols / scols) as of commit 5098796.
-- Rule: new columns only at the end, no rename / reorder.
--
-- TODO(after R4 official run, 11/27~): compare the real Parquet schema with
--   this file (parquet-tools schema or a pyarrow print on the host).
--   * note: if every row has note = None, pyarrow writes it as INT32 (Null type)
--     and STRING here will not match. Ask R4 to cast note to str before saving,
--     or drop the column from the Parquet.
--   * is_primary / beyond_placebo must be BOOLEAN, n_* must be INT64 (BIGINT).
USE qa_decline;

CREATE EXTERNAL TABLE IF NOT EXISTS site_trend_model (
  site STRING, metric STRING, month STRING, domain_group STRING, model_id STRING,
  segment STRING,
  actual DOUBLE, counterfactual DOUBLE, band_low DOUBLE, band_high DOUBLE, gap DOUBLE,
  unit STRING)
  STORED AS PARQUET LOCATION 'hdfs:///project/serving/site_trend_model';

CREATE EXTERNAL TABLE IF NOT EXISTS site_trend_summary (
  site STRING, metric STRING, domain_group STRING, model_id STRING,
  fit_start STRING, fit_end STRING, post_start STRING, post_end STRING,
  pre_trend_pct_per_year DOUBLE, post_gap_pct DOUBLE,
  placebo_min DOUBLE, placebo_max DOUBLE, beyond_placebo BOOLEAN,
  break_month STRING, break_strength DOUBLE,
  -- v2 (appended)
  unit STRING, post_gap_12m DOUBLE, placebo_values STRING,
  n_fit_months BIGINT, n_zero_months_excluded BIGINT, n_out_of_range BIGINT,
  smearing_factor DOUBLE, is_primary BOOLEAN, note STRING)
  STORED AS PARQUET LOCATION 'hdfs:///project/serving/site_trend_summary';

-- R4 writes this one as CSV only (one header line, comma separated).
-- Impala 2.2 ignores skip.header.line.count, so the API filters by metric
-- and the header line never matches. Hyphens in did_placebo_YYYY-MM -> '_'.
CREATE EXTERNAL TABLE IF NOT EXISTS q2_group_summary (
  metric STRING, unit STRING, n_sites_A BIGINT, n_sites_B BIGINT,
  did_12m DOUBLE, did_12m_perm_p DOUBLE, did_12m_cluster_se_logpt DOUBLE,
  did_full DOUBLE, did_full_perm_p DOUBLE, perm_type STRING, n_perm BIGINT,
  did_placebo_2020_07 DOUBLE, did_placebo_2021_01 DOUBLE,
  did_placebo_2021_07 DOUBLE, did_placebo_2021_11 DOUBLE,
  did_placebo_min DOUBLE, did_placebo_max DOUBLE, did_beyond_placebo BOOLEAN,
  trend_gap12_median_A DOUBLE, trend_gap12_median_B DOUBLE, trend_gap12_A_minus_B DOUBLE,
  share_sites_beyond_placebo_A DOUBLE, share_sites_beyond_placebo_B DOUBLE,
  primary_model STRING, same_sign_trend_vs_did BOOLEAN,
  break_signal BOOLEAN, break_signal_strict BOOLEAN,
  A_minus_B_break_month STRING, A_minus_B_break_strength DOUBLE)
  ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
  STORED AS TEXTFILE LOCATION 'hdfs:///project/serving/q2_group_summary'
  TBLPROPERTIES ('skip.header.line.count'='1');
