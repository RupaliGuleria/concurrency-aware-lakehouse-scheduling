-- The ingestion service writes new Hive-style partition directories directly
-- to S3 (e.g. a new source=/hour= combination) without ever calling the Hive
-- Metastore — so Trino doesn't know a new partition exists until told.
-- Re-run this after each ingestion run (or on a timer) to pick up new
-- partitions. mode => 'FULL' reconciles both additions and removals; cheap
-- enough to run after every batch at this data scale.

CALL hive.system.sync_partition_metadata(
    schema_name  => 'events',
    table_name   => 'raw_events',
    mode         => 'FULL'
);

CALL hive.system.sync_partition_metadata(
    schema_name  => 'events',
    table_name   => 'quarantined_events',
    mode         => 'FULL'
);
