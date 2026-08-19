# Beacon Semantic + Context Layer

This layer is deterministic. It does not train, fine-tune, embed, or invoke an LLM.

## Entity Dictionary

Funds:

- `BPT`, `Beacon Pension Trust`, `pension`, `pension trust` -> `BPT`
- `BLE`, `Beacon Legacy Endowment`, `endowment` -> `BLE`

Asset-class aliases are constrained to the actual normalized dataset:

- `Absolute Return`: absolute return, hedge fund, hedge funds, alternatives
- `Cash`: cash, liquidity, liquid assets
- `Core`: core, core fixed income, core bonds
- `Growth (High Yield)`: growth, high yield, hy, growth high yield
- `Private Credit`: private credit, pc, direct lending
- `Private Equity`: private equity, pe, buyout, private markets
- `Public Equity`: public equity, public equities, equity, equities, stocks, stock
- `Real Assets`: real assets, infrastructure, natural resources
- `Real Estate`: real estate, re, property

Managers are resolved by exact normalized manager names from the workbook-derived dataset.

## Metric Vocabulary

- `underperformed`, `below benchmark`, `trailed benchmark`, `lagged benchmark` -> excess return `< 0`
- `outperformed`, `beat benchmark`, `above benchmark`, `ahead of benchmark` -> excess return `> 0`
- `drift`, `off target`, `policy deviation`, `policy drift` -> `allocation_drift_pp`
- `overweight` -> `allocation_drift_pp > 0`
- `underweight` -> `allocation_drift_pp < 0`
- `aum`, `assets under management`, `market value`, `portfolio value` -> `ending_aum`
- `cash flow`, `net flow`, `net cash flow` -> `net_cash_flow`

## Context Object

Ask Beacon can pass:

```json
{
  "fund": "BPT",
  "period": "FY2026",
  "asset_class": "Private Equity",
  "manager": null,
  "source_page": "insights",
  "research_signal_id": "SIG_002"
}
```

## Context Precedence

1. Explicit user language overrides application context.
2. Application context fills missing fund, period, asset class, manager, and research signal only when the referent is otherwise clear.
3. If explicit language conflicts with context, the explicit interpretation is returned and the conflict is recorded.
4. `recently` uses the active period/context. Without period context, clarification is required.
5. `last six months` maps to `H2 FY2026` for this FY2026 workbook dataset.
6. In comparison phrasing such as `Compare this with BLE`, the explicit fund is treated as the comparison target when the active context already has a different fund.

## Clarification Terms

Do not automatically define:

- best
- worst
- strongest
- weakest
- performed well

These require a metric or ranking basis before the future tool loop should execute.

## Example Interpretations

- `Did the pension trust underperform in the last six months?` -> fund `BPT`, period `H2 FY2026`, metric `fund_excess_return_pp < 0`
- `Which PE managers beat benchmark for BLE in Q4?` -> fund `BLE`, asset class `Private Equity`, period `Q4`, metric `manager_excess_return_pp > 0`
- `Compare this with BLE.` with active BPT/Private Equity/FY2026 context -> base fund `BPT`, compare-to fund `BLE`, same period and asset class
- `Has this got worse recently?` without active context -> clarification required
