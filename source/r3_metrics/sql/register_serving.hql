-- Check the successful R3 validated.json before changing the serving table.
-- hive --hiveconf r3_serving_path=/project/statistics/full-001/site_metrics_monthly -f sql/register_serving.hql
USE qa_decline;
CREATE EXTERNAL TABLE IF NOT EXISTS site_metrics_monthly (
  site STRING, month STRING, domain_group STRING,
  questions_count BIGINT, new_users_count BIGINT,
  answered_rate DOUBLE, downvote_ratio DOUBLE
)
STORED AS PARQUET LOCATION '${hiveconf:r3_serving_path}';
-- CREATE IF NOT EXISTS does not update an existing table's LOCATION.
ALTER TABLE site_metrics_monthly SET LOCATION '${hiveconf:r3_serving_path}';
ALTER TABLE site_metrics_monthly SET TBLPROPERTIES ('r2.data'='R3 output; see run validated.json for scope');
