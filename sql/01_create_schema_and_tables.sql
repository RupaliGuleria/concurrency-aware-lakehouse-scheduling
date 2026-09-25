-- Run once against Trino on the Ryzen box (trino CLI, DBeaver, or the web UI's
-- query editor at http://YOUR_TRINO_HOST:8080), after the ingestion service has
-- written at least one Parquet file to the `lakehouse-events` bucket — Trino's
-- Hive connector needs the S3 prefix to exist before CREATE TABLE succeeds.
--
-- Layout this matches (see docs/PIPELINE_ARCHITECTURE.md):
--   s3a://lakehouse-events/data/year=Y/month=M/day=D/hour=H/source=S/*.parquet       (PASSED + QUARANTINED... see note)
--   s3a://lakehouse-events/quarantine/year=Y/month=M/day=D/hour=H/source=S/*.parquet
--
-- Every event (regardless of dataset — banking, tpch_orders_v1, tpch_lineitem_v1)
-- lands in the same flat envelope schema; the dataset-specific fields live inside
-- `payload_json` as a JSON string, keyed by `schema_id`. Pull them out with
-- json_extract_scalar(payload_json, '$.o_orderkey') etc. — see
-- sql/03_example_queries.sql.

CREATE SCHEMA IF NOT EXISTS hive.events;

CREATE TABLE IF NOT EXISTS hive.events.raw_events (
    event_id                 varchar,
    trade_group_id           varchar,
    trade_id                 varchar,
    idempotency_key          varchar,
    event_type                varchar,
    "timestamp"               varchar,
    pipeline_version          varchar,
    schema_id                 varchar,
    payload_json               varchar,
    validation_status          varchar,
    schema_version             varchar,
    schema_valid                boolean,
    drift_detected               boolean,
    drift_type                    varchar,
    breaking_change                boolean,
    compatibility                    varchar,
    schema_violations_json            varchar,
    quality_score                      double,
    quality_passed                      boolean,
    quality_threshold                    double,
    quality_rule_results_json             varchar,
    ingestion_time                         varchar,
    source_topic                            varchar,
    -- partition columns must be listed last, matching partitioned_by below.
    -- Trino derives these from the directory path, not from the Parquet file's
    -- own `source` column (which is also embedded per-row — harmless duplicate).
    year   integer,
    month  integer,
    day    integer,
    hour   integer,
    source varchar
)
WITH (
    external_location = 's3a://lakehouse-events/data/',
    format = 'PARQUET',
    partitioned_by = ARRAY['year', 'month', 'day', 'hour', 'source']
);

CREATE TABLE IF NOT EXISTS hive.events.quarantined_events (
    event_id                 varchar,
    trade_group_id           varchar,
    trade_id                 varchar,
    idempotency_key          varchar,
    event_type                varchar,
    "timestamp"               varchar,
    pipeline_version          varchar,
    schema_id                 varchar,
    payload_json               varchar,
    validation_status          varchar,
    schema_version             varchar,
    schema_valid                boolean,
    drift_detected               boolean,
    drift_type                    varchar,
    breaking_change                boolean,
    compatibility                    varchar,
    schema_violations_json            varchar,
    quality_score                      double,
    quality_passed                      boolean,
    quality_threshold                    double,
    quality_rule_results_json             varchar,
    ingestion_time                         varchar,
    source_topic                            varchar,
    year   integer,
    month  integer,
    day    integer,
    hour   integer,
    source varchar
)
WITH (
    external_location = 's3a://lakehouse-events/quarantine/',
    format = 'PARQUET',
    partitioned_by = ARRAY['year', 'month', 'day', 'hour', 'source']
);
