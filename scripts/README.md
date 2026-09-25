# Scripts

Small Trino-client helpers used from `ingestion-pipeline/README.md`.

Setup (first time):

```bash
cd scripts
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Windows
# .venv/bin/pip install -r requirements.txt         # macOS/Linux
```

- `exec_sql_file.py <path-to-sql-file>` — runs every `;`-separated statement
  in a `.sql` file against Trino on the Ryzen box (`YOUR_TRINO_HOST:8080`),
  printing results for statements that return rows. Used for `sql/01`, `02`,
  and `03`.
- `checklist_check.py` — runs the row-count / DLQ / `json_extract_scalar`
  checks from `ingestion-pipeline/README.md`'s "Definition of done" section
  and prints PASS/FAIL for each.

## Trino CLI (ad-hoc queries, e.g. browsing table contents)

Chosen over a GUI client (DBeaver etc.) for this project so every query stays
a copy-pasteable, scriptable command — consistent with `exec_sql_file.py` and
with how the eventual experiment harness will talk to Trino (programmatically,
not through a GUI). Not committed here (14MB+ binary) — download it matching
whatever version `infra/docker-compose.yml`'s `trino` image is pinned to
(currently `455`):

```bash
mkdir -p scripts/tools
curl -L -o scripts/tools/trino-cli-455-executable.jar \
  https://repo1.maven.org/maven2/io/trino/trino-cli/455/trino-cli-455-executable.jar
```

Run it:

```bash
java -jar scripts/tools/trino-cli-455-executable.jar --pager="" \
  --server http://YOUR_TRINO_HOST:8080 --catalog hive --schema events
```

`--pager=""` avoids a "failed to open pager: ... less ..." error on Windows
(cmd.exe has no `less`) — harmless (the query still runs and prints results
even without it), but noisy enough to be worth silencing up front.

Or one-shot with `--execute "SELECT ..."` instead of dropping into the
interactive shell.
