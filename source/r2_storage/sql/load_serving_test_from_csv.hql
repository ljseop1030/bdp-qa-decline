-- TEST DATA path (2026-11-21): R3 Notion parquet is format v2 (Impala 2.2 rejects it).
-- Convert to TSV on the host, load as text, rewrite as Parquet v1 with Hive.
USE qa_decline;
CREATE EXTERNAL TABLE IF NOT EXISTS site_metrics_monthly_csv_test (
  site STRING, month STRING, domain_group STRING,
  questions_count BIGINT, new_users_count BIGINT,
  answered_rate DOUBLE, downvote_ratio DOUBLE)
  ROW FORMAT DELIMITED FIELDS TERMINATED BY '\t'
  STORED AS TEXTFILE LOCATION 'hdfs:///project/serving/_test_csv/site_metrics_monthly';
SET parquet.compression=SNAPPY;
SET hive.exec.compress.output=true;
INSERT OVERWRITE TABLE site_metrics_monthly
  SELECT site, month, domain_group, questions_count, new_users_count, answered_rate, downvote_ratio
  FROM site_metrics_monthly_csv_test;
