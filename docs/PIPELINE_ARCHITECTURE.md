# Pipeline architecture: ingestion → storage → query

> **Current phase scope: TPC-H only** — verified end to end 2026-08-19, see
> `ingestion-pipeline/README.md`'s "Definition of done" for the actual
> results. The banking-transaction producer profile mentioned below exists
> in `adaptive-data-lake-producer` and will matter later (as an
> ingestion-load generator, once scheduler work starts), but was
> deliberately deferred until the TPC-H path was proven first.

## What runs where

```text
THIS MACHINE (local, wherever adaptive-data-lake-producer is cloned)
├── Kafka + Zookeeper + Kafka UI   (producers/docker-compose.yml — minio service NOT started)
├── Python producers                (producers/cloud — banking_producer module, run for the
                                      tpch-orders / tpch-lineitem profiles right now)
└── Java ingestion service          (ingestion/ — Kafka consumer, dedup, schema/quality checks,
                                      Parquet writer)
        |
        | S3 PutObject over Tailscale (MINIO_ENDPOINT=http://YOUR_TRINO_HOST:9000)
        v
RYZEN BOX (YOUR_TRINO_HOST, this repo's infra/)
├── MinIO           bucket: lakehouse-events   (auto-created by the ingestion service)
├── Postgres         backs Hive Metastore
├── Hive Metastore    catalogs the Parquet partitions for Trino
└── Trino              queries lakehouse-events.raw_events / quarantined_events via the
                         `hive` catalog (infra/trino/catalog/hive.properties)
```

Only the Java ingestion service's S3 traffic crosses Tailscale — Kafka, the
producers, and the ingestion service's own CPU/memory all stay local, so
running this on a modest laptop doesn't affect measurement fidelity later
(the thing being measured is Trino/MinIO contention on the Ryzen box, not
this machine's speed). This mirrors the "ingestion writer can be a separate
machine from the system under test" reasoning in
`research-plan/scheduler_scope_and_infra_notes.md`.

## Why one Trino table covers three datasets

`adaptive-data-lake-producer` (branch `pipeline_tpch`) writes every event —
banking transaction, TPC-H order, TPC-H lineitem — through the same generic
Avro envelope (`banking_transaction_record.avsc`, despite the name). The
dataset-specific fields (`o_orderkey`, `l_quantity`, `transaction_id`, ...)
are JSON-encoded into one `payload_json` string column; `schema_id` says
which dataset a row belongs to. So `hive.events.raw_events` (see
`sql/01_create_schema_and_tables.sql`) is a single external table over all
three, partitioned by `year/month/day/hour/source` — filter by `schema_id`
and pull typed fields out of `payload_json` with `json_extract_scalar`
(see `sql/03_example_queries.sql`).

PASSED and QUARANTINED events land in different S3 prefixes
(`data/` vs `quarantine/`) under the same bucket and partition scheme —
hence two tables, not one.

**Gotcha confirmed 2026-08-19:** the write buffer that decides when to flush
a Parquet file (`ParquetWriteBuffer.buildPartitionKey()` in
`adaptive-data-lake-producer`) keys only on `prefix + year/month/day/hour/
source` — **not** `schema_id`. So a single physical Parquet file can contain
rows from *multiple datasets* if a flush boundary lands mid-transition
between producer runs. Observed directly: running `tpch-orders` then
immediately `tpch-lineitem` produced one file (`part-2-*.parquet`) holding
7000 `tpch_orders_v1` rows + 3000 `tpch_lineitem_v1` rows together. Don't
assume "one file = one dataset" — always filter by `schema_id` in the query,
never by which file you're looking at. To see this for yourself:
`SELECT "$path", "$file_size", schema_id, count(*) FROM hive.events.raw_events
GROUP BY "$path", "$file_size", schema_id` (Hive connector hidden columns).

## How a query actually reaches MinIO (schemas vs. buckets aren't 1:1)

Worth being explicit about, since it's not obvious from the DDL alone: a
Trino/Hive **schema** (`SHOW SCHEMAS FROM hive`) is just a metadata
namespace tracked by the Hive Metastore — it is *not* the same thing as a
MinIO **bucket**, and there's no 1:1 mapping between them. Confirmed via
`SHOW CREATE SCHEMA`/`SHOW CREATE TABLE` on 2026-08-19:

| Schema | `location` | Notes |
|---|---|---|
| `information_schema` | *(none)* | Synthetic, provided by Trino itself — not backed by any storage. |
| `default` | `s3://warehouse/` | Hive Metastore's built-in schema, auto-created on first boot (`hive.metastore.warehouse.dir` in `metastore-site.xml`) — this is why the `warehouse` bucket exists at all. |
| `events` | `s3://warehouse/events.db` | **Unused.** `CREATE SCHEMA hive.events` (`sql/01`) didn't set an explicit location, so it got Hive's default `<schema>.db` naming under `warehouse` — but nothing is actually stored there. |
| `test` | `s3a://warehouse/test` | Leftover from `infra/TROUBLESHOOTING.md`'s verification steps — see that file. |

The actual event data lives in the **`lakehouse-events`** bucket, because
each *table* (not the schema) overrides its location individually via
`external_location` in `sql/01_create_schema_and_tables.sql`:
`raw_events` → `s3a://lakehouse-events/data`, `quarantined_events` →
`s3a://lakehouse-events/quarantine`. So the real lookup chain for a query
is: query `hive.events.raw_events` → Trino asks the Hive Metastore for that
*table's* location + registered partitions → gets back
`s3a://lakehouse-events/data/year=.../...` → Trino reads those Parquet files
straight from MinIO via `infra/trino/catalog/hive.properties`'s S3 config.
The schema's own location is irrelevant once a table overrides it.

## Why partitions need an explicit sync step

The ingestion service writes directly to S3 (MinIO) — it never talks to the
Hive Metastore. Trino's Hive connector only knows about a partition once
something registers it in the metastore, so new `year=/month=/day=/hour=`
directories are invisible to Trino until `sql/02_sync_partitions.sql`
(`CALL hive.system.sync_partition_metadata(...)`) runs. Re-run it after each
ingestion batch, or on a timer, before querying newly-ingested data.

## Mapping onto the research paper (future phase, not now)

Once this phase's checklist is green, this pipeline plays two roles from
`research-plan/rupali_ingestion_aware_scheduler_paper_plan.md`:

- TPC-H (`tpch-orders` / `tpch-lineitem` producer profiles) becomes the
  **queryable dataset** the scheduler experiments run TPC-H queries
  against — run once (or at whatever scale factor the paper plan's staged
  sizing table calls for) to populate `hive.events.raw_events`.
- The **ingestion simulator** the Week 2 plan calls for ("write ingestion
  scripts that upload/write Parquet data into MinIO") — the deferred
  banking producer profile, run continuously or in bursts *as ongoing
  traffic competing with TPC-H queries*, becomes that ingestion-load
  generator.
- A source of **telemetry** either way — `ingestion_time`, `quality_score`,
  `validation_status`, and per-partition row counts (see the last query in
  `sql/03_example_queries.sql`) are exactly the kind of per-run signal the
  paper plan's telemetry schema needs (query id, start/end time, ingestion
  rate, etc.), even though this pipeline's own telemetry is ingestion-side,
  not query-side.
