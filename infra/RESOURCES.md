# Infra Resources — RAM & Storage Allocation

Reference for the Trino / MinIO / Hive-Metastore / Postgres stack
(deployed via `docker-compose.yml` on a single on-premises machine, reachable
over a private VPN — see `TROUBLESHOOTING.md`; host details redacted in this
public copy).

> Values below are the **configured/assigned** limits from the compose file and
> `.wslconfig`. Live usage varies with workload — check `docker stats` for
> real-time numbers.

## Per-service allocation (Docker data stack)

| Service        | RAM (heap/allocated)                              | Storage (persistent volume)              |
|----------------|---------------------------------------------------|------------------------------------------|
| **Trino**      | `-Xmx16G` heap (+ ~5GB headroom reserve)          | none on disk (in-memory compute)         |
| **MinIO**      | default (no explicit limit)                       | `minio_data` volume (S3 data)            |
| **Postgres**   | default (~128MB shared_buffers)                   | `postgres_data` volume (metastore schema)|
| **Hive Metastore** | default JVM heap (~2GB default)               | none separate (uses Postgres)            |

## WSL2 / Docker overall limits (`.wslconfig`)

- WSL2 total RAM cap: **24GB** (of 32GB physical)
- WSL2 processors: **12**
- WSL2 swap: **8GB**

## Notes

- Trino's `-Xmx16G` is why `.wslconfig` must give WSL2 at least 24GB — without
  it, Trino's heap doesn't fit and the coordinator can OOM/restart-loop.
- All Docker containers use `restart: unless-stopped`, so they come back on
  reboot as long as Docker Desktop auto-starts.
