# Concurrency-Aware SLA Scheduling and Admission Control for Analytical Queries in a Data Lake

Code, data, and paper source for the research described in the paper of the
same name (Rupali Guleria). Preprint: *arXiv link goes here once posted.*

## Abstract

Lakehouse query engines increasingly serve mixed analytical workloads under
per-query deadlines alongside continuous ingestion, commonly assumed the
primary risk to deadline compliance. Four independent tests show otherwise:
ingestion has little measurable effect on query latency here; query
concurrency is the dominant factor — a single-node Trino deployment slows a
point-lookup query 19x at sixteen concurrent queries versus one. We reframe
scheduling accordingly. Conditioning runtime prediction on current
concurrency materially changes scheduling decisions in simulation, and live
evaluation confirms the broader contention-driven collapse in deadline
adherence and the aggregate advantage of dynamic-programming (DP) based
scheduling, though not a consistent live edge for adaptive over static
prediction specifically. An objective-optimal DP scheduler closes much of the
remaining gap. Beyond a certain concurrency level no ordering satisfies every
deadline; the more defensible response is admission control, deferring excess
load rather than letting it fail: admitted queries met deadlines more often
than the blended average in every live condition tested, and under live
moderate-concurrency contention the mechanism deferred 82.5% of arrivals once
its feasibility test judged further admission unsafe. We report ingestion's
null effect as a rigorously tested finding, and state this work's scope
plainly: predictions are point estimates, concurrency is modeled at three
coarse levels, and one live admission-control result falls short of its
predicted target — discussed, not hidden.

## What's here

| Folder | What |
|---|---|
| `paper/ieee/` | IEEE-format LaTeX source of the paper (`main.tex`, `refs.bib`, `figures/`) — the same content submitted to arXiv. |
| `scripts/` | All analysis code: offline bootstrap simulation, live-validation harnesses, the multi-query schedulers (Adaptive Least Slack, static-margin baselines, DP), admission-control reconciliation, held-out prediction-error analysis, and figure generation. |
| `results/` | Every experiment's raw output and write-up, in chronological order: `checkpoint1_*` and `week3*` are the falsification stage (four independent tests ruling out ingestion as the dominant factor — see Section 5 of the paper); `week4/` is the concurrency-aware scheduling and admission-control evaluation the paper reports (offline, held-out, and live). |
| `infra/` | Docker Compose stack (Trino / MinIO / Hive Metastore / Postgres) used to run the live query engine. Credentials in this folder are placeholders — see below. |
| `ingestion-pipeline/` | Deployment glue connecting [adaptive-data-lake-producer](https://github.com/RupaliGuleria/adaptive-data-lake-producer) (the ingestion layer — Kafka + Java ingestion service + Python producers, a separate repo, not vendored here) to `infra/`'s MinIO/Trino. |
| `sql/` | Trino DDL for the external tables over ingested Parquet data, plus partition-sync and example queries. |
| `docs/` | Architecture notes on how the pieces fit together. |

## Reproducing the analysis

The scheduler simulation and figure-generation scripts in `scripts/` run
against the CSVs already committed in `results/` — no live infrastructure
needed to reproduce the paper's numbers and figures:

```
pip install -r scripts/requirements.txt
python scripts/simulate_multi_query_scheduling.py   # offline bootstrap + held-out evaluation
python scripts/compute_prediction_error_and_concurrency_fig.py
python scripts/generate_paper_figures.py
```

Reproducing the *live* results requires standing up the `infra/` stack
yourself (Trino/MinIO/Hive Metastore/Postgres) and the ingestion pipeline
from [adaptive-data-lake-producer](https://github.com/RupaliGuleria/adaptive-data-lake-producer):

1. `cp infra/.env.example infra/.env` and fill in your own credentials.
2. Update the hardcoded credential placeholders in
   `infra/hive-metastore/metastore-site.xml` and
   `infra/trino/catalog/hive.properties` to match (`docker-compose` does not
   substitute variables into these two files — see the comment at the top of
   `metastore-site.xml`).
3. `docker compose -f infra/docker-compose.yml up -d`
4. Set `TRINO_HOST` / `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`
   environment variables to point the scripts in `scripts/` and
   `ingestion-pipeline/` at your own deployment (all default to `localhost`
   or a clearly-marked placeholder — see `infra/TROUBLESHOOTING.md` for
   known deployment issues and fixes).

## A note on this repo's history

This is a curated snapshot of a larger private working repository (informal
planning notes, session logs, and day-to-day task tracking live there, not
here). Some infrastructure files below still describe internal deployment
details generically (e.g. "your host", "a private network") — these were
redacted from what was originally a single physical dev machine reachable
only over a private VPN, not a production or multi-tenant system.

## License

MIT — see `LICENSE`.
