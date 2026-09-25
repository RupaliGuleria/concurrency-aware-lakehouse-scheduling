-- Sanity-check queries once 01 and 02 have run.

-- Row counts per dataset, per validation status.
SELECT schema_id, validation_status, count(*) AS n
FROM hive.events.raw_events
GROUP BY schema_id, validation_status
ORDER BY schema_id, validation_status;

-- Pull TPC-H order fields out of payload_json.
SELECT
    json_extract_scalar(payload_json, '$.o_orderkey')   AS o_orderkey,
    json_extract_scalar(payload_json, '$.o_custkey')    AS o_custkey,
    json_extract_scalar(payload_json, '$.o_totalprice')  AS o_totalprice,
    quality_score,
    ingestion_time
FROM hive.events.raw_events
WHERE schema_id = 'tpch_orders_v1'
LIMIT 20;

-- Banking transactions, filtered by partition (prunes to one hour of files).
SELECT
    json_extract_scalar(payload_json, '$.transaction_id') AS transaction_id,
    json_extract_scalar(payload_json, '$.amount')          AS amount,
    quality_score
FROM hive.events.raw_events
WHERE schema_id = 'banking_transaction_v1'
  AND year = 2026 AND month = 8 AND day = 17
LIMIT 20;

-- Ingestion volume over time — this is the actual "ingestion load" signal the
-- scheduler's predictor will eventually consume (see research-plan/).
SELECT year, month, day, hour, source, count(*) AS events_ingested
FROM hive.events.raw_events
GROUP BY year, month, day, hour, source
ORDER BY year, month, day, hour;
