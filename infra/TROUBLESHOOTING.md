# Infra Troubleshooting — known issues & fixes applied

Issues found and fixed while verifying the Trino / MinIO / Hive-Metastore /
Postgres stack on the Ryzen box (2026-08-17). All three fixes are already
baked into `docker-compose.yml` and `hive-metastore/`. This doc exists so
that if the stack is ever rebuilt from scratch (`docker compose down -v`,
fresh clone, new box), these don't have to be re-diagnosed from scratch.

## 1. Wrong Tailscale IP in docs

`RESOURCES.md` documented the wrong Tailscale IP for the box — a
transcription typo one digit off from the actual address (per `tailscale
status`, hostname `your-host`), which is `YOUR_TRINO_HOST`. Fixed in
`RESOURCES.md`.

**Symptom:** `ping`/`Test-NetConnection` to the documented IP times out even
though the box is up and Tailscale-connected.

**Check:** run `tailscale status` on any machine on the tailnet and confirm
the IP against what's in `RESOURCES.md`.

## 2. `hive-metastore` stuck waiting on the wrong DB port

The container ran for 22+ hours showing `Up`, but never actually started —
`docker logs hive-metastore` just repeated:

```
Waiting for database on postgres to launch on 3306 ...
```

The `bitsondatadev/hive-metastore` entrypoint script's DB-readiness wait
loop uses `METASTORE_DB_PORT` (default `3306`, MySQL) and `METASTORE_TYPE`
(default `mysql`) to decide what port to poll. The compose file only set
`METASTORE_DB_HOSTNAME: postgres` — it never told the entrypoint's own
wait/init logic that the backing DB is Postgres on 5432. This is separate
from `metastore-site.xml`'s `javax.jdo.option.ConnectionURL`, which *was*
already correctly pointed at `jdbc:postgresql://postgres:5432/...` — that
JDBC URL is only used once the metastore actually starts, which it never
reached.

**Fix** — added to the `hive-metastore` service's `environment:` in
`docker-compose.yml`:
```yaml
METASTORE_DB_PORT: 5432
METASTORE_TYPE: postgres
```

**Check:** `docker logs hive-metastore --tail 50` should not show a
repeating "Waiting for database... on 3306" line.

## 3. Kerberos realm crash on startup

After fixing #2, `hive-metastore` crashed instead of looping:

```
Caused by: KrbException: Cannot locate default realm
Exception in thread "main" java.lang.IllegalArgumentException: Can't get Kerberos realm
```

This stack has no Kerberos configured anywhere (no keytab/principal in
`metastore-site.xml`, `hadoop.security.authentication` stays at the Hadoop
default of `simple`). But Hadoop's `UserGroupInformation` init
unconditionally probes for a default Kerberos realm on startup regardless
of whether Kerberos is actually used, and throws instead of skipping the
check when no `krb5.conf` exists at all in the container.

**Fix** — added a dummy `hive-metastore/krb5.conf`:
```
[libdefaults]
    default_realm = EXAMPLE.COM
```
mounted read-only in `docker-compose.yml`:
```yaml
- ./hive-metastore/krb5.conf:/etc/krb5.conf:ro
```
This only satisfies the JVM's realm lookup — it does not enable or require
real Kerberos; auth stays "simple".

**Check:** `docker logs hive-metastore --tail 50` should show it reach
actual metastore startup with no `KrbException`.

## 4. No `warehouse` bucket in MinIO

`hive.metastore.warehouse.dir` (`metastore-site.xml`) and Trino's Hive
catalog both point at `s3a://warehouse/` / bucket `warehouse`, but nothing
creates that bucket automatically — MinIO does not auto-create buckets on
first write. Without it, `CREATE SCHEMA`/`CREATE TABLE` fails looking for a
bucket that doesn't exist.

**Fix applied manually** (not yet automated in compose): created a bucket
named exactly `warehouse` via the MinIO console (`:9001`).

**Still open:** if the `minio_data` volume is ever wiped (`docker compose
down -v`), the bucket has to be recreated by hand again the same way. Worth
automating later with an `mc mb` init container/service in
`docker-compose.yml` if this stack gets rebuilt often.

## Verifying the fix end-to-end

Once all four are in place, confirm the full chain (Trino → Hive Metastore
→ Postgres, and Trino/Metastore → MinIO) works:

```powershell
docker exec -it trino trino --execute "CREATE SCHEMA hive.test WITH (location = 's3a://warehouse/test/')"
docker exec -it trino trino --execute "SHOW SCHEMAS FROM hive"
```

Should show `test` in the schema list. Clean up afterward:
```powershell
docker exec -it trino trino --execute "DROP SCHEMA hive.test"
```

**Known leftover:** as of 2026-08-19, `hive.test` still exists (`SHOW SCHEMAS FROM hive`
lists it) — the cleanup `DROP SCHEMA` above apparently wasn't run after this
verification, or the schema was recreated later. It's harmless (empty,
`location = s3a://warehouse/test`) but not accounted for by anything else in
this repo — safe to `DROP SCHEMA hive.test` next time someone's in there, or
leave it, just don't be surprised it's not one of "your" 3 real schemas
(`default`, `information_schema`, `events`) when you run `SHOW SCHEMAS`.

## 5. `SHOW TABLES FROM hive.<schema>` fails with `Invalid method name: 'get_table_meta'`

Found 2026-08-19 while exploring the `events` schema via the Trino CLI:

```
trino.exceptions.TrinoExternalError: TrinoExternalError(type=EXTERNAL, name=HIVE_METASTORE_ERROR,
message="Error listing tables for catalog hive: Invalid method name: 'get_table_meta'", ...)
```

Trino 455's Hive connector calls a Thrift method (`get_table_meta`) that the
`bitsondatadev/hive-metastore` image's metastore version doesn't implement —
a version mismatch between the two images pinned in `docker-compose.yml`.

**Workaround:** query tables directly by name instead of listing first —
`DESCRIBE hive.events.raw_events` and `SHOW CREATE TABLE hive.events.raw_events`
both work fine; it's specifically the bulk-listing call that fails. Not yet
fixed (would mean pinning a newer/matching `hive-metastore` image and
re-verifying the other 4 fixes above still apply) — low priority since it
doesn't block querying, only table discovery via `SHOW TABLES`.
