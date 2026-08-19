from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from .research import HORIZONS, PERIODS, allocation_horizon_view, fund_horizon_view, manager_horizon_view


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    display_name: str
    definition: str
    unit: str
    formula: str
    applicable_dimensions: list[str]
    source_table: str
    interpretation: str
    calculation_owner: str = "Python"


METRIC_PERIODS = ["Q1", "Q2", "Q3", "Q4", "FY2026", "H1 FY2026", "H2 FY2026", "Q4 vs Q3", "Q1 -> Q4"]


METRIC_REGISTRY = [
    MetricDefinition("ending_aum", "Ending AUM", "Market value at the reporting period end.", "USD millions", "source ending market value from fund horizon row", ["fund", "period"], "fund_summary_view", "snapshot measure at period end"),
    MetricDefinition("aum_change_amount", "AUM Change", "Change in ending AUM over the selected reporting horizon.", "USD millions", "ending_aum - beginning_aum for derived horizons; source QoQ change for quarters", ["fund", "period"], "fund_summary_view", "positive = AUM increased"),
    MetricDefinition("aum_change_pct", "AUM Change %", "AUM change divided by beginning AUM for the selected horizon.", "percent", "aum_change_amount / beginning_aum * 100", ["fund", "period"], "fund_summary_view", "positive = AUM increased"),
    MetricDefinition("fund_return_pct", "Fund Return", "Fund total return for the selected horizon.", "percent", "FY2026 uses Q4 FYTD; quarters use QTD; H1/H2 geometrically link source QTD returns", ["fund", "period"], "fund_summary_view", "higher = stronger absolute return"),
    MetricDefinition("policy_benchmark_return_pct", "Policy Benchmark Return", "Policy benchmark return for the selected horizon.", "percent", "FY2026 uses Q4 FYTD; quarters use QTD; H1/H2 geometrically link source QTD benchmark returns", ["fund", "period"], "fund_summary_view", "benchmark comparator for fund return"),
    MetricDefinition("fund_excess_return_pp", "Fund Excess Return", "Fund return minus policy benchmark return.", "percentage points", "fund_return_pct - policy_benchmark_return_pct", ["fund", "period"], "fund_summary_view", "positive = outperformed policy benchmark"),
    MetricDefinition("net_cash_flow", "Net Cash Flow", "Contributions/gifts plus benefit payments/distributions plus admin and investment-management fees.", "USD millions", "source net cash flow; H1/H2 sum quarter activity", ["fund", "period"], "fund_summary_view", "positive = net inflow"),
    MetricDefinition("investment_gain_loss", "Investment Gain / Loss", "Investment performance component of the AUM roll-forward.", "USD millions", "source investment gain/loss; H1/H2 sum quarter activity", ["fund", "period"], "fund_summary_view", "positive = investment gain"),
    MetricDefinition("actual_allocation_pct", "Actual Allocation", "Asset-class ending market value as a percent of fund ending AUM.", "percent", "source/reporting allocation percent at period snapshot", ["fund", "period", "asset_class"], "asset_allocation_view", "actual asset-class weight"),
    MetricDefinition("policy_target_pct", "Policy Target", "Policy target allocation for the asset class.", "percent", "source policy target percent", ["fund", "period", "asset_class"], "asset_allocation_view", "strategic target weight"),
    MetricDefinition("allocation_drift_pp", "Allocation Drift", "Actual allocation percent minus policy target percent.", "percentage points", "actual_allocation_pct - policy_target_pct", ["fund", "period", "asset_class"], "asset_allocation_view", "positive = overweight; negative = underweight"),
    MetricDefinition("dollar_variance_to_policy", "Dollar Variance To Policy", "Dollar variance between actual market value and policy target value.", "USD millions", "ending_market_value - ending_fund_aum * policy_target_pct / 100", ["fund", "period", "asset_class"], "asset_allocation_view", "positive = dollars overweight"),
    MetricDefinition("manager_return_pct", "Manager Return", "Manager return for the selected horizon.", "percent", "FY2026 uses Q4 FYTD; quarters use QTD; H1/H2 geometrically link source QTD returns", ["fund", "period", "manager", "asset_class"], "manager_performance_view", "higher = stronger absolute manager return"),
    MetricDefinition("manager_benchmark_return_pct", "Manager Benchmark Return", "Asset-class benchmark return used for manager comparison.", "percent", "FY2026 uses Q4 FYTD; quarters use QTD; H1/H2 geometrically link source QTD benchmark returns", ["fund", "period", "manager", "asset_class"], "manager_performance_view", "benchmark comparator for manager return"),
    MetricDefinition("manager_excess_return_pp", "Manager Excess Return", "Manager return minus manager benchmark return.", "percentage points", "manager_return_pct - manager_benchmark_return_pct", ["fund", "period", "manager", "asset_class"], "manager_performance_view", "positive = manager outperformed benchmark"),
    MetricDefinition("manager_consistency", "Manager Consistency", "Count of quarters in the selected horizon where the manager outperformed benchmark.", "quarters", "count of source QTD manager excess return values greater than zero", ["fund", "period", "manager", "asset_class"], "manager_performance_view", "higher = more consistent relative performance"),
    MetricDefinition("best_relative_manager", "Best Relative Manager", "Manager with the highest benchmark-relative return in the selected horizon.", "percentage points", "max(manager_excess_return_pp)", ["fund", "period"], "manager_performance_view", "identifies strongest relative contributor"),
    MetricDefinition("worst_relative_manager", "Worst Relative Manager", "Manager with the lowest benchmark-relative return in the selected horizon.", "percentage points", "min(manager_excess_return_pp)", ["fund", "period"], "manager_performance_view", "identifies largest relative detractor"),
    MetricDefinition("reconciliation_variance", "Reconciliation Variance", "Difference between calculated and reported ending AUM in the roll-forward validation.", "USD millions", "beginning_aum + net_cash_flow + investment_gain_loss - ending_aum", ["fund", "period"], "validation_results", "zero or near zero = reconciled"),
    MetricDefinition("allocation_validation_status", "Allocation Validation Status", "Status of allocation total and market-value tie-out checks.", "status", "validation status from allocation_total checks", ["fund", "period"], "validation_results", "pass = allocation total and market value reconcile within tolerance"),
]


def metric_registry_records() -> list[dict[str, Any]]:
    return [asdict(metric) for metric in METRIC_REGISTRY]


def _value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


def _manager_id(name: Any) -> str | None:
    if pd.isna(name):
        return None
    return str(name).upper().replace(" ", "_").replace("-", "_")


def _period_type(period: str) -> str:
    if period in PERIODS:
        return "quarter"
    if period == "FY2026":
        return "fiscal_year"
    if period in {"H1 FY2026", "H2 FY2026"}:
        return "derived_horizon"
    return "comparison"


def _metric_source_quarters(period: str) -> list[str]:
    if period == "FY2026":
        return ["Q4"]
    return HORIZONS[period]["quarters"]


def _registry(metric_id: str) -> MetricDefinition:
    return next(metric for metric in METRIC_REGISTRY if metric.metric_id == metric_id)


def _clean_slug(value: Any) -> str:
    return str(value).upper().replace(" ", "_").replace("/", "_").replace("->", "TO").replace("(", "").replace(")", "")


def _source_refs(rows: pd.DataFrame) -> dict[str, Any]:
    if rows.empty:
        return {"source_record_ids": [], "source_files": [], "source_sheets": [], "source_rows": [], "source_cells": []}
    ids = [str(v) for v in rows.get("source_record_id", pd.Series(dtype=str)).dropna().unique().tolist()]
    provenance = [p for p in rows.get("_provenance", pd.Series(dtype=object)).dropna().tolist() if isinstance(p, dict)]
    return {
        "source_record_ids": ids,
        "source_files": sorted({p.get("source_file") for p in provenance if p.get("source_file")}),
        "source_sheets": sorted({p.get("source_sheet") for p in provenance if p.get("source_sheet")}),
        "source_rows": sorted({int(p.get("source_row")) for p in provenance if p.get("source_row")}),
        "source_cells": sorted({p.get("source_cells") for p in provenance if p.get("source_cells")}),
    }


def _metric_row(
    metric_id: str,
    period: str,
    value: Any,
    *,
    fund_id: str | None = None,
    asset_class: str | None = None,
    manager_id: str | None = None,
    manager_name: str | None = None,
    source_rows: pd.DataFrame | None = None,
    value_text: str | None = None,
    value_path: list[float] | None = None,
    calculation_method: str | None = None,
    support_status: str = "supported",
) -> dict[str, Any]:
    metric = _registry(metric_id)
    parts = ["METRIC", metric_id, period, fund_id, asset_class, manager_id or manager_name]
    row_id = "_".join(_clean_slug(part) for part in parts if part)
    refs = _source_refs(source_rows if source_rows is not None else pd.DataFrame())
    return {
        "metric_value_id": row_id,
        "metric_id": metric_id,
        "display_name": metric.display_name,
        "fiscal_year": "FY2026",
        "period": period,
        "period_type": _period_type(period),
        "fund_id": fund_id,
        "asset_class": asset_class,
        "manager_id": manager_id,
        "manager_name": manager_name,
        "value": _value(value),
        "value_text": value_text,
        "value_path": value_path,
        "unit": metric.unit,
        "source_table": metric.source_table,
        "calculation_method": calculation_method or metric.formula,
        "support_status": support_status,
        **refs,
    }


def _fund_metric_rows(fund_view: pd.DataFrame, period: str) -> list[dict[str, Any]]:
    horizon = fund_horizon_view(fund_view, period)
    rows: list[dict[str, Any]] = []
    for _, row in horizon.iterrows():
        fund_id = row["FundCode"]
        source = fund_view[(fund_view["FundCode"] == fund_id) & (fund_view["Quarter"].isin(_metric_source_quarters(period)))]
        beginning = float(row["BeginningMarketValue"])
        ending = float(row["EndingMarketValue"])
        if period in PERIODS:
            amount = float(row["QoQAUMChange"])
            pct = float(row["QoQAUMChangePct"])
        else:
            amount = ending - beginning
            pct = amount / beginning * 100 if beginning else 0.0
        metric_values = {
            "ending_aum": ending,
            "aum_change_amount": amount,
            "aum_change_pct": pct,
            "fund_return_pct": float(row["HorizonReturnPct"]),
            "policy_benchmark_return_pct": float(row["HorizonBenchmarkPct"]),
            "fund_excess_return_pp": float(row["HorizonExcessPp"]),
            "net_cash_flow": float(row["NetCashFlow"]),
            "investment_gain_loss": float(row["InvestmentGainLoss"]),
        }
        for metric_id, metric_value in metric_values.items():
            rows.append(_metric_row(metric_id, period, metric_value, fund_id=fund_id, source_rows=source))
    return rows


def _allocation_metric_rows(allocation_view: pd.DataFrame, period: str) -> list[dict[str, Any]]:
    horizon = allocation_horizon_view(allocation_view, period)
    rows: list[dict[str, Any]] = []
    for _, row in horizon.iterrows():
        fund_id = row["FundCode"]
        asset = row["AssetClassLevel1"]
        source = allocation_view[
            (allocation_view["FundCode"] == fund_id)
            & (allocation_view["AssetClassLevel1"] == asset)
            & (allocation_view["Quarter"].isin(_metric_source_quarters(period)))
        ]
        for metric_id, field in {
            "actual_allocation_pct": "PctOfFundTotal",
            "policy_target_pct": "PolicyTargetPct",
            "allocation_drift_pp": "VarianceToTargetPct",
            "dollar_variance_to_policy": "DollarVariance",
        }.items():
            rows.append(_metric_row(metric_id, period, float(row[field]), fund_id=fund_id, asset_class=asset, source_rows=source))
    return rows


def _manager_metric_rows(manager_view: pd.DataFrame, period: str) -> list[dict[str, Any]]:
    horizon = manager_horizon_view(manager_view, period)
    rows: list[dict[str, Any]] = []
    for _, row in horizon.iterrows():
        fund_id = row["FundCode"]
        manager_name = row["ManagerName"]
        asset = row["AssetClassLevel1"]
        manager_id = _manager_id(manager_name)
        source = manager_view[
            (manager_view["FundCode"] == fund_id)
            & (manager_view["ManagerName"] == manager_name)
            & (manager_view["AssetClassLevel1"] == asset)
            & (manager_view["Quarter"].isin(_metric_source_quarters(period)))
        ]
        metric_values = {
            "manager_return_pct": float(row["HorizonReturnPct"]),
            "manager_benchmark_return_pct": float(row["HorizonBenchmarkPct"]),
            "manager_excess_return_pp": float(row["HorizonExcessPp"]),
            "manager_consistency": int(row["HorizonQuartersAhead"]),
        }
        for metric_id, metric_value in metric_values.items():
            rows.append(
                _metric_row(
                    metric_id,
                    period,
                    metric_value,
                    fund_id=fund_id,
                    asset_class=asset,
                    manager_id=manager_id,
                    manager_name=manager_name,
                    source_rows=source,
                )
            )
    for fund_id, group in horizon.groupby("FundCode", sort=False):
        if group.empty:
            continue
        best = group.sort_values("HorizonExcessPp", ascending=False).iloc[0]
        worst = group.sort_values("HorizonExcessPp", ascending=True).iloc[0]
        for metric_id, selected in (("best_relative_manager", best), ("worst_relative_manager", worst)):
            manager_name = selected["ManagerName"]
            source = manager_view[
                (manager_view["FundCode"] == fund_id)
                & (manager_view["ManagerName"] == manager_name)
                & (manager_view["AssetClassLevel1"] == selected["AssetClassLevel1"])
                & (manager_view["Quarter"].isin(_metric_source_quarters(period)))
            ]
            rows.append(
                _metric_row(
                    metric_id,
                    period,
                    float(selected["HorizonExcessPp"]),
                    fund_id=fund_id,
                    asset_class=selected["AssetClassLevel1"],
                    manager_id=_manager_id(manager_name),
                    manager_name=manager_name,
                    source_rows=source,
                )
            )
    return rows


def _validation_metric_rows(validations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for validation in validations:
        if validation["type"] == "fund_roll_forward":
            rows.append(
                _metric_row(
                    "reconciliation_variance",
                    validation["period"],
                    validation.get("variance"),
                    fund_id=validation.get("fund"),
                    value_text=validation.get("status"),
                    calculation_method="validation: beginning_aum + net_cash_flow + investment_gain_loss - ending_aum",
                )
            )
        if validation["type"] == "allocation_total":
            rows.append(
                _metric_row(
                    "allocation_validation_status",
                    validation["period"],
                    1 if validation.get("status") == "pass" else 0,
                    fund_id=validation.get("fund"),
                    value_text=validation.get("status"),
                    calculation_method="validation: allocation total and market-value tie-out",
                )
            )
    return rows


def _comparison_metric_rows(analytics: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    fund = analytics["fund_summary_view"]
    alloc = analytics["asset_allocation_view"]
    manager = analytics["manager_performance_view"]
    rows: list[dict[str, Any]] = []
    for fund_id in sorted(fund["FundCode"].unique()):
        q3 = fund[(fund["FundCode"] == fund_id) & (fund["Quarter"] == "Q3")]
        q4 = fund[(fund["FundCode"] == fund_id) & (fund["Quarter"] == "Q4")]
        if q3.empty or q4.empty:
            continue
        q3r = q3.iloc[0]
        q4r = q4.iloc[0]
        source = pd.concat([q3, q4])
        rows.append(_metric_row("aum_change_amount", "Q4 vs Q3", float(q4r["EndingMarketValue"] - q3r["EndingMarketValue"]), fund_id=fund_id, source_rows=source, calculation_method="Q4 ending_aum - Q3 ending_aum"))
        rows.append(_metric_row("fund_excess_return_pp", "Q4 vs Q3", float(q4r["ExcessQTDReturnBps"] / 100 - q3r["ExcessQTDReturnBps"] / 100), fund_id=fund_id, source_rows=source, calculation_method="Q4 QTD excess return - Q3 QTD excess return"))
        path = [float(v) for v in fund[fund["FundCode"] == fund_id].sort_values("Quarter", key=lambda s: s.map({q: i for i, q in enumerate(PERIODS)}))["EndingMarketValue"].tolist()]
        rows.append(_metric_row("ending_aum", "Q1 -> Q4", path[-1] if path else None, fund_id=fund_id, source_rows=fund[fund["FundCode"] == fund_id], value_path=path, calculation_method="Q1 through Q4 ending_aum path"))
    for (fund_id, asset), group in alloc.groupby(["FundCode", "AssetClassLevel1"], sort=False):
        ordered = group.sort_values("Quarter", key=lambda s: s.map({q: i for i, q in enumerate(PERIODS)}))
        if {"Q3", "Q4"} <= set(group["Quarter"]):
            q3 = group[group["Quarter"] == "Q3"].iloc[0]
            q4 = group[group["Quarter"] == "Q4"].iloc[0]
            rows.append(_metric_row("allocation_drift_pp", "Q4 vs Q3", float(q4["VarianceToTargetPct"] - q3["VarianceToTargetPct"]), fund_id=fund_id, asset_class=asset, source_rows=group[group["Quarter"].isin(["Q3", "Q4"])], calculation_method="Q4 drift - Q3 drift"))
        if len(ordered) == 4:
            path = [float(v) for v in ordered["VarianceToTargetPct"].tolist()]
            rows.append(_metric_row("allocation_drift_pp", "Q1 -> Q4", path[-1] - path[0], fund_id=fund_id, asset_class=asset, source_rows=ordered, value_path=path, calculation_method="Q1 through Q4 allocation drift path"))
    for (fund_id, manager_name, asset), group in manager.groupby(["FundCode", "ManagerName", "AssetClassLevel1"], sort=False):
        ordered = group.sort_values("Quarter", key=lambda s: s.map({q: i for i, q in enumerate(PERIODS)}))
        if {"Q3", "Q4"} <= set(group["Quarter"]):
            q3 = group[group["Quarter"] == "Q3"].iloc[0]
            q4 = group[group["Quarter"] == "Q4"].iloc[0]
            rows.append(_metric_row("manager_excess_return_pp", "Q4 vs Q3", float(q4["ExcessQTDReturnPp"] - q3["ExcessQTDReturnPp"]), fund_id=fund_id, asset_class=asset, manager_id=_manager_id(manager_name), manager_name=manager_name, source_rows=group[group["Quarter"].isin(["Q3", "Q4"])], calculation_method="Q4 QTD manager excess - Q3 QTD manager excess"))
        if len(ordered) == 4:
            path = [float(v) for v in ordered["ExcessQTDReturnPp"].tolist()]
            rows.append(_metric_row("manager_excess_return_pp", "Q1 -> Q4", path[-1] - path[0], fund_id=fund_id, asset_class=asset, manager_id=_manager_id(manager_name), manager_name=manager_name, source_rows=ordered, value_path=path, calculation_method="Q1 through Q4 source QTD manager excess path"))
    return rows


def build_metric_values(canonical: dict[str, pd.DataFrame], analytics: dict[str, pd.DataFrame], validations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    del canonical
    rows: list[dict[str, Any]] = []
    for period in HORIZONS:
        rows.extend(_fund_metric_rows(analytics["fund_summary_view"], period))
        rows.extend(_allocation_metric_rows(analytics["asset_allocation_view"], period))
        rows.extend(_manager_metric_rows(analytics["manager_performance_view"], period))
    rows.extend(_validation_metric_rows(validations))
    rows.extend(_comparison_metric_rows(analytics))
    for row in rows:
        value = row.get("value")
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            row["value"] = None
    return rows
