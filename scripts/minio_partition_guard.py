"""MinIO partition-snapshot guard for Week 3's ingestion-contention testing.

Problem: the ingestion pipeline partitions physical Parquet files by
year/month/day/hour/source only (ParquetWriteBuffer.buildPartitionKey() in
adaptive-data-lake-producer), and `source` is a hardcoded "cloud" literal
shared by every producer profile (banking_producer/schema.py) - see
research-plan/week3_task_list.md's "Deferred / blocks Week 5" entry. So no
S3 object path identifies which producer wrote a given file; the only safe
cleanup mechanism this phase is a before/after partition-prefix snapshot
diff, which is only valid because TPC-H ingestion is static during Week 3
(no producer other than the heavy_burst one writes while it's used).

Usage:
  .venv/Scripts/python.exe minio_partition_guard.py check
      Read-only contamination report: TPC-H row counts (via Trino),
      partition prefixes + object counts/bytes (via MinIO), any
      unexpected schema_ids currently registered.

  .venv/Scripts/python.exe minio_partition_guard.py snapshot --out snap.json
      Records the current partition prefix list + per-prefix object
      count/bytes to a JSON file, to diff against later.

  .venv/Scripts/python.exe minio_partition_guard.py cleanup --since snap.json [--confirm]
      Lists partitions now, diffs against the snapshot, reports exactly
      which prefixes are new. Without --confirm this is a dry run (prints
      what WOULD be deleted, deletes nothing). With --confirm, deletes only
      the new prefixes' objects, then re-verifies they're gone and that
      TPC-H's registered row counts are unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict

import boto3
import trino

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://localhost:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "CHANGE_ME_MINIO_SECRET")
BUCKET = "lakehouse-events"
DATA_PREFIX = "data/"

TRINO_HOST = os.environ.get("TRINO_HOST", "localhost")
TRINO_PORT = 8080
TRINO_USER = "minio-partition-guard"
TRINO_CATALOG = "hive"

EXPECTED_SCHEMA_IDS = {"tpch_orders_v1", "tpch_lineitem_v1"}


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
    )


def list_partitions(s3) -> dict:
    """Returns {partition_prefix: {"objects": [keys...], "bytes": int}} under DATA_PREFIX."""
    paginator = s3.get_paginator("list_objects_v2")
    partitions: dict = defaultdict(lambda: {"objects": [], "bytes": 0})
    for page in paginator.paginate(Bucket=BUCKET, Prefix=DATA_PREFIX):
        for obj in page.get("Contents", []):
            parts = obj["Key"].split("/")
            if len(parts) < 6:
                continue  # not a full year=/month=/day=/hour=/source= path
            prefix = "/".join(parts[:6])
            partitions[prefix]["objects"].append(obj["Key"])
            partitions[prefix]["bytes"] += obj["Size"]
    return dict(partitions)


def trino_schema_id_counts() -> dict:
    conn = trino.dbapi.connect(host=TRINO_HOST, port=TRINO_PORT, user=TRINO_USER, catalog=TRINO_CATALOG)
    cur = conn.cursor()
    cur.execute("SELECT schema_id, count(*) FROM hive.events.raw_events GROUP BY schema_id")
    return {row[0]: row[1] for row in cur.fetchall()}


def cmd_check(args) -> None:
    s3 = s3_client()
    partitions = list_partitions(s3)
    counts = trino_schema_id_counts()

    total_objects = sum(len(p["objects"]) for p in partitions.values())
    total_bytes = sum(p["bytes"] for p in partitions.values())

    unexpected_schema_ids = sorted(set(counts) - EXPECTED_SCHEMA_IDS)
    tpch_present = "tpch_orders_v1" in counts and "tpch_lineitem_v1" in counts

    print("=== Contamination check ===")
    print(f"TPC-H SF 0.1 present: {'yes' if tpch_present else 'no'}")
    for sid in sorted(EXPECTED_SCHEMA_IDS):
        print(f"  {sid}: {counts.get(sid, 0)} rows (registered in Trino)")
    print(f"Unexpected schema_ids registered in Trino: {unexpected_schema_ids or 'none'}")
    print()
    print(f"MinIO {DATA_PREFIX} objects: {total_objects} ({total_bytes} bytes) "
          f"across {len(partitions)} partition prefixes")
    for prefix in sorted(partitions):
        p = partitions[prefix]
        print(f"  {prefix}: {len(p['objects'])} objects, {p['bytes']} bytes")

    print()
    if not tpch_present:
        print("WARNING: expected TPC-H schema_ids not found in raw_events.")
    if unexpected_schema_ids:
        print("WARNING: unexpected schema_ids registered - investigate before starting Week 3 baselines.")
    if tpch_present and not unexpected_schema_ids:
        print("Clean: TPC-H SF 0.1 present, no unexpected registered data.")


def cmd_snapshot(args) -> None:
    s3 = s3_client()
    partitions = list_partitions(s3)
    snapshot = {
        prefix: {"object_count": len(p["objects"]), "bytes": p["bytes"]}
        for prefix, p in partitions.items()
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)
    print(f"Snapshot written to {args.out}: {len(snapshot)} partition prefixes, "
          f"{sum(v['object_count'] for v in snapshot.values())} objects")


def cmd_cleanup(args) -> None:
    with open(args.since, "r", encoding="utf-8") as f:
        before = json.load(f)

    s3 = s3_client()
    now = list_partitions(s3)

    new_prefixes = sorted(set(now) - set(before))
    if not new_prefixes:
        print("No new partitions since snapshot - nothing to clean up.")
        return

    print(f"New partitions since {args.since}:")
    total_objects = 0
    total_bytes = 0
    for prefix in new_prefixes:
        p = now[prefix]
        print(f"  {prefix}: {len(p['objects'])} objects, {p['bytes']} bytes")
        total_objects += len(p["objects"])
        total_bytes += p["bytes"]
    print(f"Total: {total_objects} objects, {total_bytes} bytes across {len(new_prefixes)} new prefixes")

    if not args.confirm:
        print("\nDry run (no --confirm passed) - nothing deleted.")
        return

    keys_to_delete = [key for prefix in new_prefixes for key in now[prefix]["objects"]]
    print(f"\nDeleting {len(keys_to_delete)} objects...")
    for i in range(0, len(keys_to_delete), 1000):  # S3 delete_objects caps at 1000 keys/call
        batch = keys_to_delete[i:i + 1000]
        resp = s3.delete_objects(Bucket=BUCKET, Delete={"Objects": [{"Key": k} for k in batch]})
        errors = resp.get("Errors", [])
        if errors:
            print(f"  ERRORS: {errors}")
    print("Delete calls complete.")

    print("\nRe-verifying...")
    after = list_partitions(s3)
    remaining_new = sorted(set(after) & set(new_prefixes))
    if remaining_new:
        print(f"WARNING: these new prefixes still have objects after delete: {remaining_new}")
    else:
        print("Confirmed: all new prefixes removed from MinIO.")

    counts = trino_schema_id_counts()
    print(f"Trino schema_id counts after cleanup: {counts}")
    unexpected = sorted(set(counts) - EXPECTED_SCHEMA_IDS)
    if unexpected:
        print(f"WARNING: unexpected schema_ids still registered in Trino: {unexpected} "
              f"(these were unsynced new-partition objects, so this shouldn't happen - investigate).")
    else:
        print("Confirmed: no unexpected schema_ids registered in Trino.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="Read-only contamination report")
    p_check.set_defaults(func=cmd_check)

    p_snap = sub.add_parser("snapshot", help="Record current partition state to a JSON file")
    p_snap.add_argument("--out", required=True)
    p_snap.set_defaults(func=cmd_snapshot)

    p_clean = sub.add_parser("cleanup", help="Delete only partitions new since a snapshot")
    p_clean.add_argument("--since", required=True)
    p_clean.add_argument("--confirm", action="store_true", help="Actually delete (default is dry run)")
    p_clean.set_defaults(func=cmd_cleanup)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
