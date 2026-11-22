-- R2 reference only. Replace LOCATION with the successful run's HDFS directory.
-- Not automatically executed by R3. Hive/Impala interoperability needs VM QA.
CREATE EXTERNAL TABLE IF NOT EXISTS site_metrics_monthly (
  site STRING,
  month STRING,
  domain_group STRING,
  questions_count BIGINT,
  new_users_count BIGINT,
  answered_rate DOUBLE,
  downvote_ratio DOUBLE
)
STORED AS PARQUET
LOCATION '/project/statistics/REPLACE_RUN/site_metrics_monthly';
