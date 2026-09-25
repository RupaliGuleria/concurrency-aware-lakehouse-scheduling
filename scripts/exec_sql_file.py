"""Run every statement in a .sql file against Trino, printing results for
statements that return rows.

Usage: .venv/Scripts/python.exe exec_sql_file.py ../sql/01_create_schema_and_tables.sql

Set TRINO_HOST to point at your own Trino coordinator (default: localhost).
"""
from __future__ import annotations

import os
import re
import sys

import trino

HOST = os.environ.get("TRINO_HOST", "localhost")
PORT = 8080
USER = "lakehouse-scheduler-research"
CATALOG = "hive"


def split_statements(sql_text: str) -> list[str]:
    # Strip `--` line comments, then split on `;`.
    no_comments = re.sub(r"--[^\n]*", "", sql_text)
    parts = [p.strip() for p in no_comments.split(";")]
    return [p for p in parts if p]


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <path-to-sql-file>")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        sql_text = f.read()

    conn = trino.dbapi.connect(host=HOST, port=PORT, user=USER, catalog=CATALOG)
    cur = conn.cursor()

    for statement in split_statements(sql_text):
        preview = " ".join(statement.split())[:100]
        print(f"\n>>> {preview}...")
        cur.execute(statement)
        rows = cur.fetchall()
        if rows:
            columns = [d[0] for d in cur.description]
            print(" | ".join(columns))
            for row in rows:
                print(" | ".join(str(v) for v in row))
        else:
            print("(no rows returned)")


if __name__ == "__main__":
    main()
