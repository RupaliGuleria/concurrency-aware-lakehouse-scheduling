# Runs one producer profile (banking / tpch-orders / tpch-lineitem) from a
# local clone of adaptive-data-lake-producer (pipeline_tpch branch — that's
# the branch with TPC-H support; see docs/PIPELINE_ARCHITECTURE.md).
#
# Usage (from anywhere):
#   .\run-producers.ps1 -ProducerRepo "C:\path\to\adaptive-data-lake-producer" -Profile banking
#   .\run-producers.ps1 -ProducerRepo "C:\path\to\adaptive-data-lake-producer" -Profile tpch-orders
#   .\run-producers.ps1 -ProducerRepo "C:\path\to\adaptive-data-lake-producer" -Profile tpch-lineitem
#
# Prereqs: the producer's own venv already created and dependencies installed
# (see adaptive-data-lake-producer/README.md "Quick start" step 3), and the
# ingestion service (Java) + local Kafka already running — this script only
# fires events at Kafka, it doesn't start anything else.

param(
    [Parameter(Mandatory = $true)][string]$ProducerRepo,
    [Parameter(Mandatory = $true)][ValidateSet("banking", "tpch-orders", "tpch-lineitem")][string]$Profile
)

$cloudDir = Join-Path $ProducerRepo "producers\cloud"
if (-not (Test-Path $cloudDir)) {
    throw "producers/cloud not found under $ProducerRepo — check -ProducerRepo path and that the pipeline_tpch branch is checked out."
}

switch ($Profile) {
    "banking" {
        $env:CSV_FILE_PATH  = "data/transactions.csv"
        $env:SCHEMA_ID      = "banking_transaction_v1"
        $env:ID_FIELD       = "transaction_id"
        $env:EVENT_TYPE     = "banking_transaction"
    }
    "tpch-orders" {
        $env:CSV_FILE_PATH  = "data/tpch/orders.csv"
        $env:SCHEMA_ID      = "tpch_orders_v1"
        $env:ID_FIELD       = "o_orderkey"
        $env:EVENT_TYPE     = "tpch_order"
    }
    "tpch-lineitem" {
        $env:CSV_FILE_PATH  = "data/tpch/lineitem.csv"
        $env:SCHEMA_ID      = "tpch_lineitem_v1"
        $env:ID_FIELD       = "line_item_id"
        $env:EVENT_TYPE     = "tpch_lineitem"
    }
}

Write-Host "Running profile '$Profile' | CSV=$($env:CSV_FILE_PATH) SCHEMA_ID=$($env:SCHEMA_ID)"

Push-Location $cloudDir
try {
    & ".\.venv\Scripts\python.exe" -m banking_producer.main
}
finally {
    Pop-Location
}
