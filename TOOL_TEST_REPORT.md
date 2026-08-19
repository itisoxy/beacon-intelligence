# Beacon Tool Test Report

Summary: 11/11 checks passed.

| Category | Question | Status | Error Code |
| --- | --- | --- | --- |
| DIRECT LOOKUP | What was BLE's Private Equity allocation versus policy in Q3? | pass |  |
| MANAGER RANKING | Which manager had the weakest benchmark-relative performance in Q4? | pass |  |
| PERIOD COMPARISON | How did BPT Cash allocation change between Q3 and Q4? | pass |  |
| FUND COMPARISON | Compare BPT and BLE Private Equity allocation in Q4. | pass |  |
| RESEARCH SIGNAL | What are the largest BPT research signals? | pass |  |
| RECONCILIATION | Does BPT Q4 reconcile? | pass |  |
| INVALID PERIOD | Q8 | pass | invalid_period |
| UNKNOWN FUND | unknown fund | pass | unknown_entity |
| UNKNOWN MANAGER | unknown manager | pass | unknown_entity |
| MISSING DATA | period outside FY2026 | pass | no_data |
| UNSUPPORTED METRIC | unsupported metric | pass | unsupported_metric |

Representative provenance:

```json
{
  "source_record_ids": [
    "Asset_Allocation|Q3|2026-03-31|BLE|Private Equity|Beacon Private Equity Custom Benchmark (Cambridge Associates US PE)"
  ],
  "source_file": null,
  "source_files": [
    "20260331_FYTD.xlsx"
  ],
  "source_sheet": null,
  "source_sheets": [
    "Asset_Allocation"
  ],
  "source_row": null,
  "source_rows": [
    50
  ],
  "source_cells": [
    "A50:S50"
  ]
}
```
