-- R2 raw tables (DDL v0, 2026-10-02; unchanged on 2026-11-21).
-- Run with:  hive -f create_tables.hql   then once in impala-shell:
--            INVALIDATE METADATA qa_decline.posts; (and users, votes)
-- Pre-req:   hdfs dfs -mkdir -p /project/parquet/_status
-- Partitions are NOT discovered with MSCK REPAIR TABLE any more; they are
-- added per site/table only after the R1 status check
-- (load/register_partitions.py).
CREATE DATABASE IF NOT EXISTS qa_decline;   -- default has course sample tables
USE qa_decline;

CREATE EXTERNAL TABLE IF NOT EXISTS posts (
  id BIGINT, post_type_id INT, creation_date BIGINT, parent_id BIGINT, month INT)
  PARTITIONED BY (site STRING, year INT)
  STORED AS PARQUET LOCATION 'hdfs:///project/parquet/posts';

CREATE EXTERNAL TABLE IF NOT EXISTS users (
  id BIGINT, creation_date BIGINT, month INT)
  PARTITIONED BY (site STRING, year INT)
  STORED AS PARQUET LOCATION 'hdfs:///project/parquet/users';

CREATE EXTERNAL TABLE IF NOT EXISTS votes (
  id BIGINT, vote_type_id INT, creation_date BIGINT, month INT)
  PARTITIONED BY (site STRING, year INT)
  STORED AS PARQUET LOCATION 'hdfs:///project/parquet/votes';
