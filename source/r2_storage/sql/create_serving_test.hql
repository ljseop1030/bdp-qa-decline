-- Serving table site_metrics_monthly (R3 7-column schema, fixed).
-- !! 2026-11-21: filled with TEST DATA (R3 Notion attachment
--    site_metrics_monthly_without_meta.parquet, 10 sites / 630 rows).
--    Replace files under /project/serving/site_metrics_monthly with the
--    official R3 VM output, then REFRESH qa_decline.site_metrics_monthly.
USE qa_decline;
CREATE EXTERNAL TABLE IF NOT EXISTS site_metrics_monthly (
  site STRING, month STRING, domain_group STRING,
  questions_count BIGINT, new_users_count BIGINT,
  answered_rate DOUBLE, downvote_ratio DOUBLE)
  STORED AS PARQUET LOCATION 'hdfs:///project/serving/site_metrics_monthly'
  TBLPROPERTIES ('r2.data'='TEST DATA - R3 Notion attachment (Phase 1 A5/B5), replace with R3 official run');
