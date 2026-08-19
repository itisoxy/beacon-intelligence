# Beacon Data Layer

The authoritative Reporting data pipeline is Python-based.

Run:

```powershell
.\.venv\Scripts\python.exe -m beacon_data
```

This reads the immutable source workbooks in `Data/`, reads each `ReadMe` sheet first, normalizes the structured workbook sheets, writes `public/data/beacon-data.json`, writes the browser-loadable `public/data/beacon-data.js`, and creates the DuckDB analytical store at `public/data/beacon.duckdb`.

The research layer now emits horizon-specific signals for `FY2026`, `H2 FY2026`, and individual quarters `Q1` through `Q4`. H2 calculations use Q3 and Q4 source-supported quarter rows: additive activity is summed, quarter-end positions use the Q4 snapshot, and returns are geometrically linked from QTD returns rather than subtracting FYTD percentages.

## Canonical Domain Layer

`public/data/beacon-data.json` includes a `canonical` section for application services and future agent workflows:

- `funds`
- `reporting_periods`
- `fund_performance`
- `asset_allocations`
- `managers`
- `manager_performance`
- `cash_flows`
- `benchmarks`

Canonical records expose agent-friendly names such as `fund_id`, `quarter`, `ending_aum`, `fund_return_pct`, `policy_benchmark_return_pct`, `excess_return_pp`, `asset_class`, `actual_allocation_pct`, `policy_target_pct`, `allocation_drift_pp`, `manager_name`, `manager_return_pct`, `manager_benchmark_return_pct`, `manager_excess_return_pp`, `net_cash_flow`, and `investment_gain_loss`.

The source-shaped `records` and app-facing `analytics` sections remain available for Portfolio and Insights compatibility. Canonical records retain lineage through `record_id`, `source_record_id`, `source_file`, `source_sheet`, `source_row`, `source_cells`, `fiscal_year`, `quarter`, and `reporting_period`.

Validation tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The PowerShell scripts in `tools/` are retained as earlier inspection artifacts. They are no longer the authoritative build path.
