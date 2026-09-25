"""Runs the ingestion-pipeline/README.md 'Definition of done' checks against
Trino and prints a pass/fail summary."""
from __future__ import annotations

import os

import trino

HOST = os.environ.get("TRINO_HOST", "localhost")
PORT = 8080
USER = "lakehouse-scheduler-research"
CATALOG = "hive"

conn = trino.dbapi.connect(host=HOST, port=PORT, user=USER, catalog=CATALOG)
cur = conn.cursor()


def one(sql: str):
    cur.execute(sql)
    return cur.fetchone()[0]


checks = []

dlq_orders = one("SELECT count(*) FROM hive.events.quarantined_events WHERE schema_id = 'tpch_orders_v1'")
checks.append(("tpch-orders: 0 quarantined", dlq_orders == 0, dlq_orders))

dlq_lineitem = one("SELECT count(*) FROM hive.events.quarantined_events WHERE schema_id = 'tpch_lineitem_v1'")
checks.append(("tpch-lineitem: 0 quarantined", dlq_lineitem == 0, dlq_lineitem))

orders_count = one("SELECT count(*) FROM hive.events.raw_events WHERE schema_id = 'tpch_orders_v1'")
checks.append(("tpch-orders raw_events count == 15000", orders_count == 15000, orders_count))

lineitem_count = one("SELECT count(*) FROM hive.events.raw_events WHERE schema_id = 'tpch_lineitem_v1'")
checks.append(("tpch-lineitem raw_events count == 60175", lineitem_count == 60175, lineitem_count))

cur.execute(
    "SELECT json_extract_scalar(payload_json, '$.o_orderkey'), "
    "json_extract_scalar(payload_json, '$.o_totalprice') "
    "FROM hive.events.raw_events WHERE schema_id = 'tpch_orders_v1' LIMIT 5"
)
sample_orders = cur.fetchall()
sample_ok = len(sample_orders) == 5 and all(r[0] is not None and r[1] is not None for r in sample_orders)
checks.append(("json_extract_scalar returns non-null o_orderkey/o_totalprice", sample_ok, sample_orders))

print(f"{'PASS' if sample_ok else 'FAIL'} | sample rows: {sample_orders}\n")
for label, ok, detail in checks:
    print(f"{'PASS' if ok else 'FAIL'} | {label} (got: {detail})")
