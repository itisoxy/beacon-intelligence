# Beacon 4.3 Ask Beacon End-to-End Assessment

Summary: 8/8 demonstrated requirements passed.

| Scenario | Requirement | Result | Evidence |
| --- | --- | --- | --- |
| simple_lookup | Tool-selected simple lookup with real data and provenance | PASS | BLE Private Equity was 20.8% versus a 20.0% policy target in Q3, a +0.80pp drift. Source: 20260331_FYTD.xlsx, Asset_Allocation row 50. |
| manager_ranking | Deterministic manager ranking by excess return | PASS | Northbridge Global Equity Fund underperformed by the widest Q4 margin at -0.341pp versus benchmark. Source: 20260630_FYTD.xlsx, Manager_Detail row 121. |
| multi_step | Multiple tools and evidence-backed synthesis | PASS | Investigate BPT Cash policy drift, weakest benchmark-relative managers, and FY2026 cash-flow pressure. These are sourced findings, not causal claims. |
| contextual | Safe UI context resolution | PASS | Using the BPT / FY2026 / Private Equity context, BPT drift was +0.97pp versus BLE at +0.94pp. |
| ambiguous | Clarification instead of guessing | PASS | How should I define best performance?

- Highest absolute return
- Highest excess return vs benchmark
- Most consistent outperformer |
| unsupported_causality | Limitation plus useful redirect | PASS | The supplied Beacon dataset cannot establish why an investment strategy changed. It contains performance, benchmark, allocation, cash-flow and research-signal data, not manager strategy-change records.

I can instead:
- analyse the manager's performance
- compare the manager with its benchmark
- show the quarterly trend |
| invalid | Graceful invalid-period handling | PASS | I can't run that request because Q8 is not a valid Beacon period. Use Q1, Q2, Q3, Q4, H1 FY2026, H2 FY2026, or FY2026. |
| source_traceability | Answer to metric to record to workbook/sheet/row/cell trace | PASS | BLE Private Equity Q3 allocation drift was +0.80pp. |

## Detailed Evidence

```json
{
  "simple_lookup": {
    "passed": true,
    "query": "What was BLE's Private Equity allocation versus policy target in Q3?",
    "answer": "BLE Private Equity was 20.8% versus a 20.0% policy target in Q3, a +0.80pp drift. Source: 20260331_FYTD.xlsx, Asset_Allocation row 50.",
    "tool_calls": [
      "get_asset_allocation"
    ],
    "actual": 20.8,
    "target": 20.0,
    "drift_pp": 0.8,
    "provenance": {
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
    },
    "events": [
      "context_resolved",
      "ambiguity_evaluated",
      "tool_selected",
      "tool_completed",
      "calculation_completed",
      "source_verified",
      "answer_completed"
    ]
  },
  "manager_ranking": {
    "passed": true,
    "query": "Which manager underperformed its benchmark by the widest margin in Q4, and by how much?",
    "answer": "Northbridge Global Equity Fund underperformed by the widest Q4 margin at -0.341pp versus benchmark. Source: 20260630_FYTD.xlsx, Manager_Detail row 121.",
    "tool_calls": [
      "rank_managers"
    ],
    "ranked_manager": "Northbridge Global Equity Fund",
    "metric_value": -0.3410000000000002,
    "provenance": {
      "source_record_ids": [
        "Manager_Detail|Q4|2026-06-30|BLE|Public Equity|Northbridge Global Equity Fund"
      ],
      "source_file": null,
      "source_files": [
        "20260630_FYTD.xlsx"
      ],
      "source_sheet": null,
      "source_sheets": [
        "Manager_Detail"
      ],
      "source_row": null,
      "source_rows": [
        121
      ],
      "source_cells": [
        "A121:L121"
      ]
    },
    "events": [
      "context_resolved",
      "ambiguity_evaluated",
      "tool_selected",
      "tool_completed",
      "calculation_completed",
      "source_verified",
      "answer_completed"
    ]
  },
  "multi_step": {
    "passed": true,
    "query": "What are the three most important things BPT should investigate this year?",
    "answer": "Investigate BPT Cash policy drift, weakest benchmark-relative managers, and FY2026 cash-flow pressure. These are sourced findings, not causal claims.",
    "tool_calls": [
      "get_fund_summary",
      "get_research_signals",
      "rank_managers",
      "get_cash_flows"
    ],
    "intermediate_results": {
      "fund_summary_metrics": [
        "aum",
        "return",
        "policy_benchmark",
        "excess_return",
        "net_cash_flow",
        "gain_loss"
      ],
      "research_signal_count": 6,
      "manager_rank_count": 3,
      "cash_flow_rows": 20
    },
    "events": [
      "context_resolved",
      "ambiguity_evaluated",
      "tool_selected",
      "tool_completed",
      "calculation_completed",
      "source_verified",
      "tool_selected",
      "tool_completed",
      "calculation_completed",
      "source_verified",
      "tool_selected",
      "tool_completed",
      "calculation_completed",
      "source_verified",
      "tool_selected",
      "tool_completed",
      "calculation_completed",
      "source_verified",
      "answer_completed"
    ]
  },
  "contextual": {
    "passed": true,
    "query": "Compare this with BLE.",
    "context": {
      "fund": "BPT",
      "period": "FY2026",
      "asset_class": "Private Equity",
      "source_page": "portfolio"
    },
    "resolved_interpretation": {
      "fund": "BPT",
      "period": "FY2026",
      "asset_class": "Private Equity",
      "manager": null,
      "source_page": "portfolio",
      "research_signal_id": null,
      "metric_id": null,
      "operator": null,
      "threshold": null,
      "compare_to_fund": "BLE"
    },
    "answer": "Using the BPT / FY2026 / Private Equity context, BPT drift was +0.97pp versus BLE at +0.94pp.",
    "tool_calls": [
      "compare_funds"
    ],
    "events": [
      "context_resolved",
      "ambiguity_evaluated",
      "tool_selected",
      "tool_completed",
      "calculation_completed",
      "source_verified",
      "answer_completed"
    ]
  },
  "ambiguous": {
    "passed": true,
    "query": "Which manager performed best?",
    "answer": "How should I define best performance?\n\n- Highest absolute return\n- Highest excess return vs benchmark\n- Most consistent outperformer",
    "model_calls": 0,
    "events": [
      "context_resolved",
      "ambiguity_evaluated",
      "clarification_requested"
    ]
  },
  "unsupported_causality": {
    "passed": true,
    "query": "Why did Manager X change investment strategy?",
    "answer": "The supplied Beacon dataset cannot establish why an investment strategy changed. It contains performance, benchmark, allocation, cash-flow and research-signal data, not manager strategy-change records.\n\nI can instead:\n- analyse the manager's performance\n- compare the manager with its benchmark\n- show the quarterly trend",
    "model_calls": 0,
    "events": [
      "context_resolved",
      "ambiguity_evaluated",
      "out_of_scope"
    ]
  },
  "invalid": {
    "passed": true,
    "query": "Show BPT Q8.",
    "answer": "I can't run that request because Q8 is not a valid Beacon period. Use Q1, Q2, Q3, Q4, H1 FY2026, H2 FY2026, or FY2026.",
    "tool_calls": [
      "get_fund_summary"
    ],
    "error": {
      "code": "invalid_period",
      "message": "Quarter period must be Q1, Q2, Q3 or Q4.",
      "field": "period",
      "value": "Q8"
    },
    "events": [
      "context_resolved",
      "ambiguity_evaluated",
      "tool_selected",
      "tool_completed",
      "validation_failed",
      "validation_failed"
    ]
  },
  "source_traceability": {
    "passed": true,
    "answer": "BLE Private Equity Q3 allocation drift was +0.80pp.",
    "canonical_metric": "METRIC_ALLOCATION_DRIFT_PP_Q3_BLE_PRIVATE_EQUITY",
    "normalized_record": "ASSET_ALLOC_FY2026_BLE_Q3_PRIVATE_EQUITY",
    "source_record_id": "Asset_Allocation|Q3|2026-03-31|BLE|Private Equity|Beacon Private Equity Custom Benchmark (Cambridge Associates US PE)",
    "workbook": "20260331_FYTD.xlsx",
    "sheet": "Asset_Allocation",
    "row": 50,
    "cells": "A50:S50",
    "source_lookup": {
      "table": "asset_allocations",
      "record_id": "ASSET_ALLOC_FY2026_BLE_Q3_PRIVATE_EQUITY",
      "source_record_id": "Asset_Allocation|Q3|2026-03-31|BLE|Private Equity|Beacon Private Equity Custom Benchmark (Cambridge Associates US PE)",
      "fund": "BLE",
      "period": "Q3",
      "asset_class": "Private Equity",
      "manager": null,
      "provenance": {
        "source_record_ids": [
          "Asset_Allocation|Q3|2026-03-31|BLE|Private Equity|Beacon Private Equity Custom Benchmark (Cambridge Associates US PE)"
        ],
        "source_file": "20260331_FYTD.xlsx",
        "source_files": [
          "20260331_FYTD.xlsx"
        ],
        "source_sheet": "Asset_Allocation",
        "source_sheets": [
          "Asset_Allocation"
        ],
        "source_row": 50,
        "source_rows": [
          50
        ],
        "source_cells": [
          "A50:S50"
        ]
      }
    }
  }
}
```
