# Beacon Business-Domain Tools

These tools are deterministic Python functions. They do not expose filesystem access, arbitrary Python execution, SQL execution, or an LLM. They operate on the generated Beacon model and its canonical metric values.

## Tool Schemas

1. `get_fund_summary(fund, period)`
2. `get_asset_allocation(fund, period, asset_class)`
3. `get_allocation_history(fund, asset_class)`
4. `get_manager_performance(manager=None, fund=None, period=None, asset_class=None)`
5. `rank_managers(period, metric, direction, fund=None, asset_class=None, limit=None)`
6. `get_manager_history(manager, fund=None)`
7. `get_cash_flows(fund, period)`
8. `compare_funds(metric, period, asset_class=None)`
9. `compare_periods(entity, metric, period_a, period_b, fund=None)`
10. `get_research_signals(fund=None, period=None, asset_class=None, manager=None)`
11. `validate_reconciliation(fund, period)`
12. `get_source_record(record_id)`

## Metric Sources

Numeric outputs use canonical metric values where a registered metric exists. Each metric result includes:

- `record_id`
- `metric_id`
- `value`
- `unit`
- `calculation_method`
- `support_status`
- `provenance`

Asset market value and cash-flow line items are returned from canonical normalized records because they are source measures rather than registered calculation metrics.

## Example Calls

```python
tools.get_asset_allocation("BLE", "Q3", "Private Equity")
tools.rank_managers("Q4", "excess return", "asc", limit=1)
tools.compare_funds("allocation_drift_pp", "FY2026", asset_class="Private Equity")
tools.compare_periods("Cash", "allocation_drift_pp", "Q3", "Q4", fund="BPT")
tools.get_research_signals(fund="BPT", period="FY2026")
```

## Example Structured Responses

`get_asset_allocation("BLE", "Q3", "Private Equity")` returns metrics including:

```json
{
  "market_value": {"value": 975.22, "unit": "USD millions"},
  "actual_allocation": {"value": 20.8, "unit": "percent"},
  "policy_target": {"value": 20.0, "unit": "percent"},
  "drift_pp": {"value": 0.8, "unit": "percentage points"}
}
```

`rank_managers("Q4", "excess return", "asc", limit=1)` returns the lowest Q4 relative performer:

```json
{
  "manager": "Northbridge Global Equity Fund",
  "fund": "BLE",
  "asset_class": "Public Equity",
  "metric": {"metric_id": "manager_excess_return_pp", "value": -0.341, "unit": "percentage points"}
}
```

## Provenance Example

Metric-backed responses include workbook lineage:

```json
{
  "source_record_ids": ["Asset_Allocation|Q4|2026-06-30|BPT|Cash|Beacon Cash Benchmark (ICE BofA 3-Month US T-Bill)"],
  "source_files": ["20260630_FYTD.xlsx"],
  "source_sheets": ["Asset_Allocation"],
  "source_rows": [64],
  "source_cells": ["A64:S64"]
}
```
