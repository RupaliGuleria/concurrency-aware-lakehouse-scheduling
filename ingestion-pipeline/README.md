# Ingestion → storage → query: deployment steps

Read `docs/PIPELINE_ARCHITECTURE.md` first for the full picture. This is the
step-by-step to actually bring the pipeline up.

**Current phase scope: TPC-H only** (`tpch-orders` + `tpch-lineitem`
producer profiles). Banking is deliberately deferred — skip it below. The
goal of this phase is the checklist at the bottom of this file, fully green,
*before* any scheduler work starts.

## 0. Prereqs

- The Ryzen box's infra stack (`infra/`) is up — see `infra/RESOURCES.md` and
  `infra/TROUBLESHOOTING.md`. Verify: MinIO console at
  `http://YOUR_TRINO_HOST:9001`, Trino UI at `http://YOUR_TRINO_HOST:8080`.
- `adaptive-data-lake-producer` cloned locally, **on the `pipeline_tpch`
  branch** (that's the branch with TPC-H producer support — `main` alone only
  handles banking data):
  ```bash
  git clone https://github.com/RupaliGuleria/adaptive-data-lake-producer.git
  cd adaptive-data-lake-producer
  git checkout pipeline_tpch
  ```
- Java 21 + Maven, Python 3.11+, Docker Desktop — all on this (local) machine,
  per that repo's own README prerequisites.

## 1. Start Kafka locally — skip the local MinIO

```bash
cd adaptive-data-lake-producer/producers
docker compose up -d zookeeper kafka kafka-ui
```

Deliberately **not** `docker compose up -d` (which would also start that
compose file's own local `minio` service) — storage is the Ryzen box's MinIO,
not a local one.

## 2. Point the ingestion service at the Ryzen box

Copy the values from this repo's `ingestion-pipeline/.env.ingestion.example`
into your shell before starting the service:

```powershell
$env:KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
$env:MINIO_ENDPOINT           = "http://YOUR_TRINO_HOST:9000"
$env:MINIO_ACCESS_KEY         = "minioadmin"
$env:MINIO_SECRET_KEY         = "CHANGE_ME_MINIO_SECRET"
$env:MINIO_BUCKET             = "lakehouse-events"
```

```bash
cd adaptive-data-lake-producer/ingestion
mvn spring-boot:run
```

On startup, watch the logs for `MinIO bucket created | bucket=lakehouse-events`
(or `...exists...` on later runs) — that confirms it's reaching the Ryzen
box's MinIO, not a local one.

## 3. Set up the Python producer's venv (first time only)

```bash
cd adaptive-data-lake-producer/producers/cloud
python3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # Windows
# .venv/bin/pip install -r requirements.txt        # macOS/Linux
```

TPC-H CSVs (`producers/cloud/data/tpch/orders.csv`, `lineitem.csv`) are
already committed on the `pipeline_tpch` branch at scale factor 0.01 (15,000
orders / 60,175 lineitem rows) — no download needed to get started.
Regenerate at a larger scale later, once this phase is done and the paper
plan's staged dataset sizing calls for it, using the DuckDB `tpch` extension
steps in that repo's `tpch_ingestion.md`.

(Banking data needs the Kaggle CSV per that repo's README "Dataset" section
— not needed for this phase, skip it.)

## 4. Run the TPC-H producers

From this repo:

```powershell
.\ingestion-pipeline\run-producers.ps1 -ProducerRepo "C:\path\to\adaptive-data-lake-producer" -Profile tpch-orders
.\ingestion-pipeline\run-producers.ps1 -ProducerRepo "C:\path\to\adaptive-data-lake-producer" -Profile tpch-lineitem
```

(The script also has a `banking` profile — leave it for later, per the
scope note at the top of this file.)

Each run streams its dataset through Kafka → the ingestion service → MinIO on
the Ryzen box. Watch the ingestion service's logs for
`Parquet written → MinIO | bucket=lakehouse-events key=...`.

## 5. Register the table + partitions in Trino (first time, then after each run)

Against Trino on the Ryzen box (CLI, DBeaver, or the web UI query editor at
`http://YOUR_TRINO_HOST:8080`):

1. Run `sql/01_create_schema_and_tables.sql` once.
2. Run `sql/02_sync_partitions.sql` after this run and every subsequent
   ingestion run, to make new partitions visible.

## 6. Query it

Run the queries in `sql/03_example_queries.sql`, or point a Trino client at
`YOUR_TRINO_HOST:8080`, catalog `hive`, schema `events`.

## Definition of done for this phase

Don't start scheduler work (see `research-plan/`) until all of these hold.
**Verified 2026-08-19** — run via `scripts/checklist_check.py`
(uses `scripts/exec_sql_file.py`, a small Trino-python-client runner for
any `.sql` file in this repo):

- [x] `tpch-orders` run completes with **0 events in DLQ** — 0 in
      `hive.events.quarantined_events` for `schema_id = 'tpch_orders_v1'`,
      and 0 `DLQ_ROUTED`/`NO_CONTROL_DOC` lines in the ingestion service log
      across the whole run.
- [x] `tpch-lineitem` run completes with **0 events in DLQ** — same, for
      `tpch_lineitem_v1`.
- [x] `SELECT count(*) FROM hive.events.raw_events WHERE schema_id =
      'tpch_orders_v1'` returns **15000**.
- [x] Same check for `tpch_lineitem_v1` → **60175**. (Combined:
      `SELECT count(*) FROM hive.events.raw_events` = **75175**, exactly
      15000 + 60175 — nothing silently dropped between Kafka and Parquet.)
- [x] `sql/03_example_queries.sql`'s `json_extract_scalar` query against
      `payload_json` returns correct, non-null TPC-H fields — e.g.
      `o_orderkey=20704, o_totalprice=164373.12` read back correctly typed
      through Trino.
- [x] Re-running `sql/02_sync_partitions.sql` a second time (no new data in
      between) is idempotent — no error, and `raw_events` count stayed at
      75175 (no duplication). Confirms the partition-sync step is safe to
      re-run after every ingestion batch. (Not yet exercised: does it pick
      up *new* partitions from a genuinely later ingestion run without `01`
      being re-run — expected to work since `sync_partition_metadata` is a
      standard Trino/Hive mechanism, but only actually proven the next time
      more TPC-H data is ingested.)

Ingestion-service log summary for this run: 76 batches × ~1000 events,
100% passed, 0% quarantined, 0% rejected, 0 schema drift, avg quality score
1.00 throughout. 12 Parquet files written to
`s3a://lakehouse-events/data/year=2026/month=08/day=19/hour=05/source=cloud/`.
