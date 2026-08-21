from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

PERIODS = ["Q1", "Q2", "Q3", "Q4"]
HORIZONS = {
    "FY2026": {"quarters": ["Q1", "Q2", "Q3", "Q4"], "label": "FY2026 full year", "snapshot": "Q4"},
    "H1 FY2026": {"quarters": ["Q1", "Q2"], "label": "H1 FY2026", "snapshot": "Q2"},
    "H2 FY2026": {"quarters": ["Q3", "Q4"], "label": "H2 FY2026", "snapshot": "Q4"},
    "Q1": {"quarters": ["Q1"], "label": "Q1 FY2026", "snapshot": "Q1"},
    "Q2": {"quarters": ["Q2"], "label": "Q2 FY2026", "snapshot": "Q2"},
    "Q3": {"quarters": ["Q3"], "label": "Q3 FY2026", "snapshot": "Q3"},
    "Q4": {"quarters": ["Q4"], "label": "Q4 FY2026", "snapshot": "Q4"},
}


@dataclass
class ResearchSignal:
    id: str
    type: str
    horizon: str
    research_question: str
    headline: str
    fund: str
    period: str
    asset_class: str | None
    manager: str | None
    primary_metric: str
    primary_value: float | str
    supporting_metrics: dict[str, Any]
    observation: str
    interpretation: str
    why_it_matters: str
    cio_question: str
    significance_score: float
    source_record_ids: list[str]
    source_files: list[str]
    source_sheets: list[str]
    source_rows: list[int]
    source_cells: list[str]
    limitations: str
    visual: dict[str, Any] = field(default_factory=dict)
    related_analysis: list[dict[str, Any]] = field(default_factory=list)
    possible_explanations: list[str] = field(default_factory=list)
    what_to_check_next: list[str] = field(default_factory=list)
    selected: bool = False


def _fmt_pp(value: float) -> str:
    return f"{value:+.2f}pp"


def _fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def _fmt_money(value: float) -> str:
    sign = "-" if value < 0 else ""
    value = abs(value)
    return f"{sign}${value / 1000:.2f}B" if value >= 1000 else f"{sign}${value:.1f}M"


def _link_returns(values: pd.Series) -> float:
    result = 1.0
    for value in values.fillna(0):
        result *= 1 + float(value) / 100
    return (result - 1) * 100


def _source_refs(rows: pd.DataFrame) -> dict[str, list[Any]]:
    if rows.empty:
        return {"ids": [], "files": [], "sheets": [], "rows": [], "cells": []}
    ids = [str(v) for v in rows.get("source_record_id", pd.Series(dtype=str)).dropna().unique().tolist()]
    provenance = [p for p in rows.get("_provenance", pd.Series(dtype=object)).dropna().tolist() if isinstance(p, dict)]
    return {
        "ids": ids,
        "files": sorted({p.get("source_file") for p in provenance if p.get("source_file")}),
        "sheets": sorted({p.get("source_sheet") for p in provenance if p.get("source_sheet")}),
        "rows": sorted({int(p.get("source_row")) for p in provenance if p.get("source_row")}),
        "cells": sorted({p.get("source_cells") for p in provenance if p.get("source_cells")}),
    }


def _signal(
    *,
    id: str,
    type: str,
    horizon: str,
    research_question: str,
    headline: str,
    fund: str,
    asset_class: str | None = None,
    manager: str | None = None,
    primary_metric: str,
    primary_value: float | str,
    supporting_metrics: dict[str, Any],
    observation: str,
    interpretation: str,
    why_it_matters: str,
    cio_question: str,
    significance_score: float,
    source_rows: pd.DataFrame,
    limitations: str,
    visual: dict[str, Any] | None = None,
    related_analysis: list[dict[str, Any]] | None = None,
    possible_explanations: list[str] | None = None,
    what_to_check_next: list[str] | None = None,
) -> ResearchSignal:
    refs = _source_refs(source_rows)
    return ResearchSignal(
        id=id,
        type=type,
        horizon=horizon,
        research_question=research_question,
        headline=headline,
        fund=fund,
        period=horizon,
        asset_class=asset_class,
        manager=manager,
        primary_metric=primary_metric,
        primary_value=primary_value,
        supporting_metrics=supporting_metrics,
        observation=observation,
        interpretation=interpretation,
        why_it_matters=why_it_matters,
        cio_question=cio_question,
        significance_score=round(float(significance_score), 2),
        source_record_ids=refs["ids"],
        source_files=refs["files"],
        source_sheets=refs["sheets"],
        source_rows=refs["rows"],
        source_cells=refs["cells"],
        limitations=limitations,
        visual=visual or {},
        related_analysis=related_analysis or [],
        possible_explanations=possible_explanations or _default_possible_explanations(type),
        what_to_check_next=what_to_check_next or _default_check_next(type),
    )


def _default_possible_explanations(signal_type: str) -> list[str]:
    return {
        "policy_drift": [
            "market movement may have pulled the quarter-end allocation away from the policy target",
            "rebalancing may have been delayed or intentionally staged because of implementation timing",
            "cash flows or manager activity may have changed the denominator used for allocation percentages",
        ],
        "manager_consistency": [
            "manager-specific execution may have lagged the benchmark opportunity set",
            "the selected benchmark may not fully match the manager's mandate or style exposure",
            "style, sector, or factor exposures may have been out of favor during the measured period",
        ],
        "cash_flow": [
            "scheduled benefit payments or distributions may have increased the need to monitor available liquidity",
            "cash may have been intentionally staged for near-term obligations or planned deployment",
            "net flows may have interacted with allocation drift, creating a rebalancing question rather than proving a liquidity problem",
        ],
        "relative_performance": [
            "asset-class mix versus policy may have concentrated the benchmark-relative result",
            "manager selection within one or two asset classes may explain more of the result than broad market exposure",
            "benchmark-relative market conditions may have rewarded or penalized specific portfolio exposures",
        ],
        "cross_fund": [
            "the two funds may have different policy targets for the same asset class",
            "different fund sizes can make the same percentage drift represent different dollar significance",
            "different liquidity needs may make a similar allocation signal more important for one fund than the other",
        ],
        "emerging_signal": [
            "late-period market movement may have changed the signal between quarters",
            "manager-specific relative results may have shifted enough to warrant follow-up",
            "short-horizon volatility may be amplifying a signal that needs confirmation in the next period",
        ],
    }.get(signal_type, ["data-supported relationship that needs follow-up before assigning cause"])


def _default_check_next(signal_type: str) -> list[str]:
    return {
        "policy_drift": ["review Q1-Q4 drift path", "check policy ranges", "confirm rebalancing or deployment plan"],
        "manager_consistency": ["review quarterly excess-return path", "compare manager exposure size", "check mandate and benchmark fit"],
        "cash_flow": ["review contribution and distribution timing", "compare net flows with AUM movement and allocation drift", "check whether liquidity planning assumptions still support rebalancing needs"],
        "relative_performance": ["review asset-class relative drivers", "separate allocation and manager effects", "compare with policy benchmark"],
        "cross_fund": ["compare policy targets", "review dollar variance by fund", "check whether governance question differs by fund"],
        "emerging_signal": ["review Q3-to-Q4 movement", "test whether change persisted", "compare with full-year pattern"],
    }.get(signal_type, ["review supporting metrics and source records"])


def horizon_quarters(horizon: str) -> list[str]:
    return HORIZONS[horizon]["quarters"]


def horizon_label(horizon: str) -> str:
    return HORIZONS[horizon]["label"]


def horizon_snapshot(horizon: str) -> str:
    return HORIZONS[horizon]["snapshot"]


def fund_horizon_view(fund_view: pd.DataFrame, horizon: str) -> pd.DataFrame:
    quarters = horizon_quarters(horizon)
    if horizon == "FY2026":
        out = fund_view[fund_view["Quarter"] == "Q4"].copy()
        out["Horizon"] = horizon
        out["HorizonReturnPct"] = out["FYTDReturnPct"]
        out["HorizonBenchmarkPct"] = out["PolicyBenchmarkFYTDReturnPct"]
        out["HorizonExcessPp"] = out["HorizonReturnPct"] - out["HorizonBenchmarkPct"]
        return out
    if len(quarters) == 1:
        out = fund_view[fund_view["Quarter"] == quarters[0]].copy()
        out["Horizon"] = horizon
        out["HorizonReturnPct"] = out["QTDReturnPct"]
        out["HorizonBenchmarkPct"] = out["PolicyBenchmarkQTDReturnPct"]
        out["HorizonExcessPp"] = out["HorizonReturnPct"] - out["HorizonBenchmarkPct"]
        return out
    rows = []
    for fund_code, group in fund_view[fund_view["Quarter"].isin(quarters)].groupby("FundCode", sort=False):
        ordered = group.sort_values("Quarter", key=lambda s: s.map({q: i for i, q in enumerate(PERIODS)}))
        first = ordered.iloc[0]
        last = ordered.iloc[-1]
        h_return = _link_returns(ordered["QTDReturnPct"])
        h_benchmark = _link_returns(ordered["PolicyBenchmarkQTDReturnPct"])
        record = last.to_dict()
        record.update(
            {
                "Horizon": horizon,
                "BeginningMarketValue": float(first["BeginningMarketValue"]),
                "EndingMarketValue": float(last["EndingMarketValue"]),
                "Contributions_or_Gifts": float(ordered["Contributions_or_Gifts"].sum()),
                "BenefitPayments_or_Distributions": float(ordered["BenefitPayments_or_Distributions"].sum()),
                "AdminFees": float(ordered["AdminFees"].sum()),
                "InvestmentManagementFees": float(ordered["InvestmentManagementFees"].sum()),
                "NetCashFlow": float(ordered["NetCashFlow"].sum()),
                "InvestmentGainLoss": float(ordered["InvestmentGainLoss"].sum()),
                "HorizonReturnPct": h_return,
                "HorizonBenchmarkPct": h_benchmark,
                "HorizonExcessPp": h_return - h_benchmark,
                "source_record_id": f"Fund_Horizon|{horizon}|{fund_code}",
            }
        )
        rows.append(record)
    return pd.DataFrame(rows)


def allocation_horizon_view(allocation_view: pd.DataFrame, horizon: str) -> pd.DataFrame:
    quarters = horizon_quarters(horizon)
    snapshot = horizon_snapshot(horizon)
    snap = allocation_view[allocation_view["Quarter"] == snapshot].copy()
    if horizon == "FY2026":
        snap["Horizon"] = horizon
        snap["HorizonReturnPct"] = snap["FYTDReturnPct"]
        snap["HorizonBenchmarkPct"] = snap["BenchmarkFYTDReturnPct"]
        snap["HorizonExcessPp"] = snap["HorizonReturnPct"] - snap["HorizonBenchmarkPct"]
        return snap
    if len(quarters) == 1:
        snap["Horizon"] = horizon
        snap["HorizonReturnPct"] = snap["QTDReturnPct"]
        snap["HorizonBenchmarkPct"] = snap["BenchmarkQTDReturnPct"]
        snap["HorizonExcessPp"] = snap["HorizonReturnPct"] - snap["HorizonBenchmarkPct"]
        return snap
    rows = []
    for (fund_code, asset), group in allocation_view[allocation_view["Quarter"].isin(quarters)].groupby(["FundCode", "AssetClassLevel1"], sort=False):
        ordered = group.sort_values("Quarter", key=lambda s: s.map({q: i for i, q in enumerate(PERIODS)}))
        last = ordered.iloc[-1].to_dict()
        h_return = _link_returns(ordered["QTDReturnPct"])
        h_benchmark = _link_returns(ordered["BenchmarkQTDReturnPct"])
        last.update(
            {
                "Horizon": horizon,
                "HorizonReturnPct": h_return,
                "HorizonBenchmarkPct": h_benchmark,
                "HorizonExcessPp": h_return - h_benchmark,
                "source_record_id": f"Asset_Horizon|{horizon}|{fund_code}|{asset}",
            }
        )
        rows.append(last)
    return pd.DataFrame(rows)


def manager_horizon_view(manager_view: pd.DataFrame, horizon: str) -> pd.DataFrame:
    quarters = horizon_quarters(horizon)
    if horizon == "FY2026":
        out = manager_view[manager_view["Quarter"] == "Q4"].copy()
        out["Horizon"] = horizon
        out["HorizonReturnPct"] = out["FYTDReturnPct"]
        out["HorizonBenchmarkPct"] = out["BenchmarkReturnPct"]
        out["HorizonExcessPp"] = out["ExcessFYTDReturnPp"]
        out["HorizonQuartersAhead"] = out["QuartersAhead"]
        out["HorizonQuartersUnder"] = 4 - out["QuartersAhead"]
        return out
    if len(quarters) == 1:
        out = manager_view[manager_view["Quarter"] == quarters[0]].copy()
        out["Horizon"] = horizon
        out["HorizonReturnPct"] = out["QTDReturnPct"]
        out["HorizonBenchmarkPct"] = out["BenchmarkQTDReturnPct"]
        out["HorizonExcessPp"] = out["ExcessQTDReturnPp"]
        out["HorizonQuartersAhead"] = (out["HorizonExcessPp"] > 0).astype(int)
        out["HorizonQuartersUnder"] = (out["HorizonExcessPp"] < 0).astype(int)
        return out
    rows = []
    for (fund_code, manager, asset), group in manager_view[manager_view["Quarter"].isin(quarters)].groupby(["FundCode", "ManagerName", "AssetClassLevel1"], sort=False):
        ordered = group.sort_values("Quarter", key=lambda s: s.map({q: i for i, q in enumerate(PERIODS)}))
        last = ordered.iloc[-1].to_dict()
        h_return = _link_returns(ordered["QTDReturnPct"])
        h_benchmark = _link_returns(ordered["BenchmarkQTDReturnPct"])
        q_excess = [float(v) for v in ordered["ExcessQTDReturnPp"].tolist()]
        last.update(
            {
                "Horizon": horizon,
                "HorizonReturnPct": h_return,
                "HorizonBenchmarkPct": h_benchmark,
                "HorizonExcessPp": h_return - h_benchmark,
                "HorizonQuartersAhead": sum(1 for value in q_excess if value > 0),
                "HorizonQuartersUnder": sum(1 for value in q_excess if value < 0),
                "HorizonExcessPathPp": q_excess,
                "source_record_id": f"Manager_Horizon|{horizon}|{fund_code}|{asset}|{manager}",
            }
        )
        rows.append(last)
    return pd.DataFrame(rows)


def q4_vs_q3(allocation_view: pd.DataFrame, manager_view: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    allocation = []
    for (fund, asset), group in allocation_view[allocation_view["Quarter"].isin(["Q3", "Q4"])].groupby(["FundCode", "AssetClassLevel1"]):
        if set(group["Quarter"]) != {"Q3", "Q4"}:
            continue
        q3 = group[group["Quarter"] == "Q3"].iloc[0]
        q4 = group[group["Quarter"] == "Q4"].iloc[0]
        allocation.append(
            {
                "FundCode": fund,
                "AssetClassLevel1": asset,
                "actual_allocation_change_pp": float(q4["PctOfFundTotal"] - q3["PctOfFundTotal"]),
                "drift_change_pp": float(q4["VarianceToTargetPct"] - q3["VarianceToTargetPct"]),
                "q3_drift_pp": float(q3["VarianceToTargetPct"]),
                "q4_drift_pp": float(q4["VarianceToTargetPct"]),
            }
        )
    managers = []
    for (fund, manager, asset), group in manager_view[manager_view["Quarter"].isin(["Q3", "Q4"])].groupby(["FundCode", "ManagerName", "AssetClassLevel1"]):
        if set(group["Quarter"]) != {"Q3", "Q4"}:
            continue
        q3 = group[group["Quarter"] == "Q3"].iloc[0]
        q4 = group[group["Quarter"] == "Q4"].iloc[0]
        managers.append(
            {
                "FundCode": fund,
                "ManagerName": manager,
                "AssetClassLevel1": asset,
                "excess_change_pp": float(q4["ExcessQTDReturnPp"] - q3["ExcessQTDReturnPp"]),
                "q3_excess_pp": float(q3["ExcessQTDReturnPp"]),
                "q4_excess_pp": float(q4["ExcessQTDReturnPp"]),
            }
        )
    return {"allocation": allocation, "manager": managers}


def h2_vs_h1(fund_view: pd.DataFrame, manager_view: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    rows = []
    h1 = fund_horizon_view(fund_view, "Q1")
    q2 = fund_horizon_view(fund_view, "Q2")
    h2 = fund_horizon_view(fund_view, "H2 FY2026")
    for fund in ["BPT", "BLE", "All"]:
        q1_row = h1[h1["FundCode"] == fund].iloc[0]
        q2_row = q2[q2["FundCode"] == fund].iloc[0]
        h1_return = _link_returns(pd.Series([q1_row["QTDReturnPct"], q2_row["QTDReturnPct"]]))
        h1_benchmark = _link_returns(pd.Series([q1_row["PolicyBenchmarkQTDReturnPct"], q2_row["PolicyBenchmarkQTDReturnPct"]]))
        h2_row = h2[h2["FundCode"] == fund].iloc[0]
        rows.append(
            {
                "FundCode": fund,
                "h1_return_pct": h1_return,
                "h2_return_pct": float(h2_row["HorizonReturnPct"]),
                "h1_excess_pp": h1_return - h1_benchmark,
                "h2_excess_pp": float(h2_row["HorizonExcessPp"]),
                "h2_vs_h1_excess_change_pp": float(h2_row["HorizonExcessPp"] - (h1_return - h1_benchmark)),
                "h1_net_flow_m": float(q1_row["NetCashFlow"] + q2_row["NetCashFlow"]),
                "h2_net_flow_m": float(h2_row["NetCashFlow"]),
            }
        )
    manager_rows = []
    for (fund, manager, asset), group in manager_view.groupby(["FundCode", "ManagerName", "AssetClassLevel1"]):
        if set(group["Quarter"]) != set(PERIODS):
            continue
        ordered = group.sort_values("Quarter", key=lambda s: s.map({q: i for i, q in enumerate(PERIODS)}))
        h1_excess = _link_returns(ordered.iloc[:2]["QTDReturnPct"]) - _link_returns(ordered.iloc[:2]["BenchmarkQTDReturnPct"])
        h2_excess = _link_returns(ordered.iloc[2:]["QTDReturnPct"]) - _link_returns(ordered.iloc[2:]["BenchmarkQTDReturnPct"])
        manager_rows.append(
            {
                "FundCode": fund,
                "ManagerName": manager,
                "AssetClassLevel1": asset,
                "h1_excess_pp": h1_excess,
                "h2_excess_pp": h2_excess,
                "h2_vs_h1_excess_change_pp": h2_excess - h1_excess,
            }
        )
    return {"fund": rows, "manager": manager_rows}


def q1_to_q4_trajectory(allocation_view: pd.DataFrame, manager_view: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    allocation = []
    for (fund, asset), group in allocation_view[allocation_view["FundCode"].isin(["BPT", "BLE"])].groupby(["FundCode", "AssetClassLevel1"]):
        ordered = group.sort_values("Quarter", key=lambda s: s.map({q: i for i, q in enumerate(PERIODS)}))
        if len(ordered) != 4:
            continue
        drift = [float(v) for v in ordered["VarianceToTargetPct"].tolist()]
        allocation.append({"FundCode": fund, "AssetClassLevel1": asset, "drift_path_pp": drift, "q1_to_q4_change_pp": drift[-1] - drift[0]})
    manager = []
    for (fund, name, asset), group in manager_view.groupby(["FundCode", "ManagerName", "AssetClassLevel1"]):
        ordered = group.sort_values("Quarter", key=lambda s: s.map({q: i for i, q in enumerate(PERIODS)}))
        if len(ordered) != 4:
            continue
        excess = [float(v) for v in ordered["ExcessQTDReturnPp"].tolist()]
        manager.append({"FundCode": fund, "ManagerName": name, "AssetClassLevel1": asset, "excess_path_pp": excess, "q1_to_q4_change_pp": excess[-1] - excess[0]})
    return {"allocation": allocation, "manager": manager}


def analyse_relative_performance(fund_horizon: pd.DataFrame, allocation_horizon: pd.DataFrame, horizon: str) -> list[ResearchSignal]:
    signals: list[ResearchSignal] = []
    for _, fund in fund_horizon[fund_horizon["FundCode"].isin(["BPT", "BLE"])].iterrows():
        fcode = fund["FundCode"]
        assets = allocation_horizon[allocation_horizon["FundCode"] == fcode].copy()
        assets["relative_pp"] = assets["HorizonExcessPp"]
        assets["relative_weighted_pp"] = assets["relative_pp"] * assets["PctOfFundTotal"] / 100
        top = assets.sort_values("relative_weighted_pp", ascending=False).iloc[0]
        bottom = assets.sort_values("relative_weighted_pp").iloc[0]
        period_text = horizon_label(horizon)
        signals.append(
            _signal(
                id=f"REL-{horizon}-{fcode}",
                type="relative_performance",
                horizon=horizon,
                research_question="What actually drove excess return?",
                headline=f"{fcode} relative performance in {period_text} was concentrated.",
                fund=fcode,
                primary_metric="Horizon excess return",
                primary_value=float(fund["HorizonExcessPp"]),
                supporting_metrics={
                    "fund_return_pct": float(fund["HorizonReturnPct"]),
                    "policy_benchmark_pct": float(fund["HorizonBenchmarkPct"]),
                    "excess_return_pp": float(fund["HorizonExcessPp"]),
                    "largest_positive_asset": top["AssetClassLevel1"],
                    "largest_positive_weighted_relative_pp": float(top["relative_weighted_pp"]),
                    "largest_negative_asset": bottom["AssetClassLevel1"],
                    "largest_negative_weighted_relative_pp": float(bottom["relative_weighted_pp"]),
                    "attribution_status": "Attribution not fully supported; relative performance drivers are shown instead.",
                },
                observation=(
                    f"{fcode} returned {_fmt_pct(fund['HorizonReturnPct'])} versus a policy benchmark of "
                    f"{_fmt_pct(fund['HorizonBenchmarkPct'])} in {period_text}, a relative result of {_fmt_pp(fund['HorizonExcessPp'])}."
                ),
                interpretation=f"{top['AssetClassLevel1']} was the strongest weighted relative driver, while {bottom['AssetClassLevel1']} detracted.",
                why_it_matters="The CIO can see whether the selected horizon's result was broad-based or dependent on a narrow set of portfolio areas.",
                cio_question="Which relative performance drivers are repeatable, and which need manager or policy review?",
                significance_score=72 + abs(float(fund["HorizonExcessPp"])) * 8 + abs(float(top["relative_weighted_pp"])) * 12,
                source_rows=pd.concat([pd.DataFrame([fund]), assets[assets["AssetClassLevel1"].isin([top["AssetClassLevel1"], bottom["AssetClassLevel1"]])]]),
                limitations="The workbooks do not provide holdings-level attribution or average intra-period weights, so this is not labelled formal attribution.",
                visual={"kind": "relative_bar", "items": assets.sort_values("relative_weighted_pp", ascending=False)[["AssetClassLevel1", "relative_pp", "relative_weighted_pp"]].to_dict(orient="records")},
                related_analysis=assets.sort_values("relative_weighted_pp", ascending=False)[["AssetClassLevel1", "relative_pp", "relative_weighted_pp"]].to_dict(orient="records"),
            )
        )
    return signals


def rank_allocation_drift(allocation_horizon: pd.DataFrame, full_allocation: pd.DataFrame, horizon: str) -> list[ResearchSignal]:
    signals: list[ResearchSignal] = []
    for fcode in ["BPT", "BLE"]:
        rows = allocation_horizon[allocation_horizon["FundCode"] == fcode].copy()
        rows["abs_drift"] = rows["VarianceToTargetPct"].abs()
        largest = rows.sort_values("abs_drift", ascending=False).iloc[0]
        history = full_allocation[(full_allocation["FundCode"] == fcode) & (full_allocation["AssetClassLevel1"] == largest["AssetClassLevel1"])].sort_values("Quarter")
        drift_path = [float(v) for v in history["VarianceToTargetPct"].tolist()]
        horizon_text = horizon_label(horizon)
        if horizon == "FY2026":
            interpretation = "The year-end position matters because the drift persisted across the full Q1-Q4 path."
        elif horizon == "H2 FY2026":
            h2_change = float(history[history["Quarter"] == "Q4"]["VarianceToTargetPct"].iloc[0] - history[history["Quarter"] == "Q3"]["VarianceToTargetPct"].iloc[0])
            interpretation = f"The H2 signal is the Q3-to-Q4 change of {_fmt_pp(h2_change)}, not a restatement of the full-year finding."
        else:
            interpretation = "For a single-quarter horizon, the research signal is the largest policy deviation visible at that quarter end."
        signals.append(
            _signal(
                id=f"DRIFT-{horizon}-{fcode}-{largest['AssetClassLevel1'].replace(' ', '-')}",
                type="policy_drift",
                horizon=horizon,
                research_question="Is policy drift becoming material?",
                headline=f"{largest['AssetClassLevel1']} was {fcode}'s largest policy drift in {horizon_text}.",
                fund=fcode,
                asset_class=largest["AssetClassLevel1"],
                primary_metric="Allocation drift",
                primary_value=float(largest["VarianceToTargetPct"]),
                supporting_metrics={
                    "q4_actual_pct": float(largest["PctOfFundTotal"]),
                    "policy_target_pct": float(largest["PolicyTargetPct"]),
                    "drift_pp": float(largest["VarianceToTargetPct"]),
                    "dollar_variance_m": float(largest["DollarVariance"]),
                    "q1_to_q4_drift_path": drift_path,
                    "trajectory": interpretation,
                },
                observation=(
                    f"{largest['AssetClassLevel1']} stood at {_fmt_pct(largest['PctOfFundTotal'])} versus "
                    f"{_fmt_pct(largest['PolicyTargetPct'])} policy, a drift of {_fmt_pp(largest['VarianceToTargetPct'])}."
                ),
                interpretation=interpretation,
                why_it_matters="Policy drift can change the portfolio's risk posture even when no explicit policy decision has been made.",
                cio_question="Should the drift be tolerated as market movement, rebalanced, or reviewed against policy ranges?",
                significance_score=70 + abs(float(largest["VarianceToTargetPct"])) * 7,
                source_rows=history,
                limitations="Policy bands and liquidity constraints are not supplied, so materiality is ranked by observed magnitude rather than an approved tolerance band.",
                visual={"kind": "drift_line", "items": history[["Quarter", "PctOfFundTotal", "PolicyTargetPct", "VarianceToTargetPct", "DollarVariance"]].to_dict(orient="records")},
                related_analysis=rows.sort_values("abs_drift", ascending=False)[["AssetClassLevel1", "PctOfFundTotal", "PolicyTargetPct", "VarianceToTargetPct", "DollarVariance"]].to_dict(orient="records"),
            )
        )
    return signals


def calculate_manager_consistency(manager_horizon: pd.DataFrame, full_manager: pd.DataFrame, horizon: str) -> list[ResearchSignal]:
    signals: list[ResearchSignal] = []
    for fcode in ["BPT", "BLE"]:
        rows = manager_horizon[manager_horizon["FundCode"] == fcode].copy()
        if rows.empty:
            continue
        if horizon == "FY2026":
            rows["ranking"] = rows["HorizonQuartersUnder"] * 10 + rows["HorizonExcessPp"].abs()
            selected = rows.sort_values("ranking", ascending=False).iloc[0]
            path_rows = full_manager[(full_manager["FundCode"] == fcode) & (full_manager["ManagerName"] == selected["ManagerName"]) & (full_manager["AssetClassLevel1"] == selected["AssetClassLevel1"])].sort_values("Quarter")
            path = [float(v) for v in path_rows["ExcessQTDReturnPp"].tolist()]
            headline = f"{selected['ManagerName']} underperformed in {int(selected['HorizonQuartersUnder'])} of 4 quarters."
            observation = f"{selected['ManagerName']} lagged its associated benchmark in {int(selected['HorizonQuartersUnder'])} of 4 quarters; quarterly excess was {', '.join(_fmt_pp(x) for x in path)}."
        elif horizon == "H2 FY2026":
            comp = []
            for (manager, asset), group in full_manager[(full_manager["FundCode"] == fcode) & (full_manager["Quarter"].isin(["Q3", "Q4"]))].groupby(["ManagerName", "AssetClassLevel1"]):
                q3 = group[group["Quarter"] == "Q3"].iloc[0]
                q4 = group[group["Quarter"] == "Q4"].iloc[0]
                comp.append({"ManagerName": manager, "AssetClassLevel1": asset, "q3": float(q3["ExcessQTDReturnPp"]), "q4": float(q4["ExcessQTDReturnPp"]), "change": float(q4["ExcessQTDReturnPp"] - q3["ExcessQTDReturnPp"])})
            det = sorted(comp, key=lambda x: x["change"])[0]
            selected = rows[(rows["ManagerName"] == det["ManagerName"]) & (rows["AssetClassLevel1"] == det["AssetClassLevel1"])].iloc[0]
            path_rows = full_manager[(full_manager["FundCode"] == fcode) & (full_manager["ManagerName"] == selected["ManagerName"]) & (full_manager["AssetClassLevel1"] == selected["AssetClassLevel1"]) & (full_manager["Quarter"].isin(["Q3", "Q4"]))].sort_values("Quarter")
            path = [float(v) for v in path_rows["ExcessQTDReturnPp"].tolist()]
            headline = f"{selected['ManagerName']} deteriorated from Q3 to Q4."
            observation = f"{selected['ManagerName']} moved from {_fmt_pp(det['q3'])} in Q3 to {_fmt_pp(det['q4'])} in Q4, a change of {_fmt_pp(det['change'])}."
        else:
            rows["ranking"] = rows["HorizonExcessPp"]
            selected = rows.sort_values("ranking").iloc[0]
            path_rows = full_manager[(full_manager["FundCode"] == fcode) & (full_manager["ManagerName"] == selected["ManagerName"]) & (full_manager["AssetClassLevel1"] == selected["AssetClassLevel1"]) & (full_manager["Quarter"] == horizon)]
            path = [float(selected["HorizonExcessPp"])]
            headline = f"{selected['ManagerName']} was the largest manager detractor in {horizon}."
            observation = f"{selected['ManagerName']} returned {_fmt_pct(selected['HorizonReturnPct'])} versus {_fmt_pct(selected['HorizonBenchmarkPct'])} benchmark in {horizon}, a relative result of {_fmt_pp(selected['HorizonExcessPp'])}."
        matrix = []
        for (manager, asset), group in full_manager[full_manager["FundCode"] == fcode].groupby(["ManagerName", "AssetClassLevel1"]):
            ordered = group.sort_values("Quarter", key=lambda s: s.map({q: i for i, q in enumerate(PERIODS)}))
            q_path = [float(v) for v in ordered["ExcessQTDReturnPp"].tolist()]
            matrix.append({"ManagerName": manager, "AssetClassLevel1": asset, "q_excess": q_path, "ahead": sum(1 for v in q_path if v > 0), "under": sum(1 for v in q_path if v < 0), "fy_excess": float(ordered.iloc[-1]["ExcessFYTDReturnPp"])})
        signals.append(
            _signal(
                id=f"MGR-CONSISTENCY-{horizon}-{fcode}",
                type="manager_consistency",
                horizon=horizon,
                research_question="Who consistently created or destroyed value?",
                headline=headline,
                fund=fcode,
                asset_class=selected["AssetClassLevel1"],
                manager=selected["ManagerName"],
                primary_metric="Manager excess return",
                primary_value=float(selected["HorizonExcessPp"]),
                supporting_metrics={
                    "quarters_ahead": int(selected["HorizonQuartersAhead"]),
                    "quarters_underperforming": int(selected["HorizonQuartersUnder"]),
                    "q1_q4_excess_path_pp": path,
                    "fy_excess_pp": float(selected["HorizonExcessPp"]),
                    "trend_change_pp": float(path[-1] - path[0]) if len(path) > 1 else 0.0,
                },
                observation=observation,
                interpretation="The horizon-specific pattern is more useful than ranking managers by absolute return alone.",
                why_it_matters="Benchmark-relative trajectory can surface diligence questions that are not obvious in headline return tables.",
                cio_question="Is this a mandate-cycle issue, benchmark mismatch, or manager-specific concern requiring follow-up?",
                significance_score=72 + abs(float(selected["HorizonExcessPp"])) * 4 + int(selected["HorizonQuartersUnder"]) * 6,
                source_rows=path_rows,
                limitations="The dataset does not contain holdings, style exposures, or fees by manager, so the cause of underperformance cannot be established.",
                visual={"kind": "manager_matrix", "items": sorted(matrix, key=lambda x: (x["under"], abs(x["fy_excess"])), reverse=True)[:7]},
                related_analysis=sorted(matrix, key=lambda x: (x["under"], abs(x["fy_excess"])), reverse=True)[:10],
            )
        )
    return signals


def analyse_cash_flow_patterns(fund_horizon: pd.DataFrame, cash_flow: pd.DataFrame, horizon: str) -> list[ResearchSignal]:
    q = horizon_quarters(horizon)
    funds = fund_horizon[fund_horizon["FundCode"].isin(["BPT", "BLE"])].copy()
    funds["net_flow_to_aum_pct"] = funds["NetCashFlow"] / funds["EndingMarketValue"] * 100
    more_negative = funds.sort_values("net_flow_to_aum_pct").iloc[0]
    related = funds[["FundCode", "Contributions_or_Gifts", "BenefitPayments_or_Distributions", "AdminFees", "InvestmentManagementFees", "NetCashFlow", "EndingMarketValue", "net_flow_to_aum_pct"]]
    source = cash_flow[cash_flow["Quarter"].isin(q)]
    return [
        _signal(
            id=f"CASH-FLOW-{horizon}",
            type="cash_flow",
            horizon=horizon,
            research_question="What are cash flows telling us?",
            headline=f"{more_negative['FundCode']} showed greater net outflow pressure in {horizon_label(horizon)}.",
            fund="All",
            primary_metric="Net flow / ending AUM",
            primary_value=float(more_negative["net_flow_to_aum_pct"]),
            supporting_metrics={
                row["FundCode"]: {
                    "inflows_m": float(row["Contributions_or_Gifts"]),
                    "outflows_m": float(row["BenefitPayments_or_Distributions"] + row["AdminFees"] + row["InvestmentManagementFees"]),
                    "net_flow_m": float(row["NetCashFlow"]),
                    "net_flow_to_aum_pct": float(row["net_flow_to_aum_pct"]),
                }
                for _, row in related.iterrows()
            },
            observation=f"{more_negative['FundCode']} net cash flow was {_fmt_money(more_negative['NetCashFlow'])}, or {_fmt_pct(more_negative['net_flow_to_aum_pct'])} of ending AUM.",
            interpretation="The observed flow pattern points to monitoring liquidity and rebalancing needs more closely, but it does not establish a liquidity problem.",
            why_it_matters="Cash-flow posture can affect how closely portfolio drift, liquid reserves, and rebalancing timing should be monitored.",
            cio_question="Are current liquidity sources aligned with benefit/distribution requirements and potential rebalancing needs?",
            significance_score=76 + abs(float(more_negative["net_flow_to_aum_pct"])) * 3,
            source_rows=source,
            limitations="The dataset has cash-flow categories but no liquidity schedule, capital-call pipeline, or spending-policy detail.",
            visual={"kind": "cash_table", "items": related.to_dict(orient="records")},
            related_analysis=related.to_dict(orient="records"),
        )
    ]


def compare_funds(allocation_horizon: pd.DataFrame, horizon: str) -> list[ResearchSignal]:
    q = allocation_horizon[allocation_horizon["FundCode"].isin(["BPT", "BLE"])]
    pivot = q.pivot(index="AssetClassLevel1", columns="FundCode", values=["VarianceToTargetPct", "PctOfFundTotal", "PolicyTargetPct"])
    rows = []
    for asset in pivot.index:
        drift_gap = float(pivot.loc[asset, ("VarianceToTargetPct", "BPT")] - pivot.loc[asset, ("VarianceToTargetPct", "BLE")])
        rows.append({"asset": asset, "drift_gap_pp": drift_gap})
    largest = sorted(rows, key=lambda r: abs(r["drift_gap_pp"]), reverse=True)[0]
    source = q[q["AssetClassLevel1"] == largest["asset"]]
    return [
        _signal(
            id=f"CROSS-FUND-{horizon}-{largest['asset'].replace(' ', '-')}",
            type="cross_fund",
            horizon=horizon,
            research_question="Where did BPT and BLE diverge?",
            headline=f"{largest['asset']} had the largest BPT-versus-BLE policy drift gap in {horizon_label(horizon)}.",
            fund="All",
            asset_class=largest["asset"],
            primary_metric="Cross-fund drift gap",
            primary_value=largest["drift_gap_pp"],
            supporting_metrics={
                "asset_class": largest["asset"],
                "bpt_drift_pp": float(pivot.loc[largest["asset"], ("VarianceToTargetPct", "BPT")]),
                "ble_drift_pp": float(pivot.loc[largest["asset"], ("VarianceToTargetPct", "BLE")]),
                "drift_gap_pp": largest["drift_gap_pp"],
            },
            observation=f"{largest['asset']} showed a {_fmt_pp(largest['drift_gap_pp'])} gap between BPT and BLE drift versus policy.",
            interpretation="The difference appears policy-linked: both funds use the same asset-class menu, but their targets and dollar significance differ.",
            why_it_matters="A shared investment area can create different governance questions depending on each fund's policy target and size.",
            cio_question="Should the same asset-class or manager discussion be framed differently for the pension plan and endowment?",
            significance_score=68 + abs(largest["drift_gap_pp"]) * 6,
            source_rows=source,
            limitations="The dataset can show divergence in policy consequence, but not board-level risk tolerance or policy-band intent.",
            visual={"kind": "cross_fund_bars", "items": rows},
            related_analysis=rows,
        )
    ]


def detect_emerging_signal(allocation_horizon: pd.DataFrame, full_allocation: pd.DataFrame, manager_horizon: pd.DataFrame, full_manager: pd.DataFrame, horizon: str) -> list[ResearchSignal]:
    if horizon == "H2 FY2026":
        comp = q4_vs_q3(full_allocation, full_manager)["manager"]
        det = sorted(comp, key=lambda row: row["excess_change_pp"])[0]
        source = full_manager[(full_manager["FundCode"] == det["FundCode"]) & (full_manager["ManagerName"] == det["ManagerName"]) & (full_manager["AssetClassLevel1"] == det["AssetClassLevel1"]) & (full_manager["Quarter"].isin(["Q3", "Q4"]))]
        return [
            _signal(
                id=f"EMERGING-H2-{det['FundCode']}-{det['ManagerName'].replace(' ', '-')}",
                type="emerging_signal",
                horizon=horizon,
                research_question="What changed late in the year?",
                headline=f"{det['ManagerName']} showed the largest Q4 versus Q3 relative deterioration.",
                fund=det["FundCode"],
                asset_class=det["AssetClassLevel1"],
                manager=det["ManagerName"],
                primary_metric="Q4 vs Q3 excess change",
                primary_value=float(det["excess_change_pp"]),
                supporting_metrics=det,
                observation=f"{det['ManagerName']} moved from {_fmt_pp(det['q3_excess_pp'])} in Q3 to {_fmt_pp(det['q4_excess_pp'])} in Q4.",
                interpretation="This is an H2-specific signal because it compares the two quarters inside the selected horizon rather than restating FYTD results.",
                why_it_matters="Late-period deterioration can help prioritize where research follow-up should start.",
                cio_question="Was the Q4 change temporary, mandate-related, or evidence of a broader manager trend?",
                significance_score=82 + abs(float(det["excess_change_pp"])) * 7,
                source_rows=source,
                limitations="The dataset does not include holdings-level or factor attribution to explain the Q4 deterioration.",
                visual={"kind": "manager_matrix", "items": [{"ManagerName": det["ManagerName"], "AssetClassLevel1": det["AssetClassLevel1"], "q_excess": [det["q3_excess_pp"], det["q4_excess_pp"]], "ahead": sum(1 for v in [det["q3_excess_pp"], det["q4_excess_pp"]] if v > 0)}]},
                related_analysis=sorted(comp, key=lambda row: abs(row["excess_change_pp"]), reverse=True)[:8],
            )
        ]
    if horizon in PERIODS:
        rows = manager_horizon[manager_horizon["FundCode"].isin(["BPT", "BLE"])].copy()
        hidden = rows[(rows["HorizonReturnPct"] > 0) & (rows["HorizonExcessPp"] < 0)].sort_values("HorizonExcessPp")
        if hidden.empty:
            selected = rows.sort_values("HorizonExcessPp").iloc[0]
        else:
            selected = hidden.iloc[0]
        source = full_manager[(full_manager["FundCode"] == selected["FundCode"]) & (full_manager["ManagerName"] == selected["ManagerName"]) & (full_manager["AssetClassLevel1"] == selected["AssetClassLevel1"]) & (full_manager["Quarter"] == horizon)]
        return [
            _signal(
                id=f"EMERGING-{horizon}-{selected['FundCode']}-{selected['ManagerName'].replace(' ', '-')}",
                type="emerging_signal",
                horizon=horizon,
                research_question="What did the quarter reveal?",
                headline=f"{selected['ManagerName']} had positive absolute performance but lagged benchmark in {horizon}.",
                fund=selected["FundCode"],
                asset_class=selected["AssetClassLevel1"],
                manager=selected["ManagerName"],
                primary_metric="Quarter excess return",
                primary_value=float(selected["HorizonExcessPp"]),
                supporting_metrics={"quarter_return_pct": float(selected["HorizonReturnPct"]), "quarter_benchmark_pct": float(selected["HorizonBenchmarkPct"]), "quarter_excess_pp": float(selected["HorizonExcessPp"])},
                observation=f"{selected['ManagerName']} returned {_fmt_pct(selected['HorizonReturnPct'])} versus {_fmt_pct(selected['HorizonBenchmarkPct'])} benchmark in {horizon}.",
                interpretation="Positive absolute performance can still be weak on a benchmark-relative basis.",
                why_it_matters="This avoids letting positive market beta hide relative manager weakness.",
                cio_question="Should quarterly reviews emphasize relative performance even where absolute returns are positive?",
                significance_score=80 + abs(float(selected["HorizonExcessPp"])) * 5,
                source_rows=source,
                limitations="The dataset does not show holdings-level causes of the benchmark-relative gap.",
                visual={"kind": "manager_matrix", "items": [{"ManagerName": selected["ManagerName"], "AssetClassLevel1": selected["AssetClassLevel1"], "q_excess": [float(selected["HorizonExcessPp"])], "ahead": int(selected["HorizonExcessPp"] > 0)}]},
                related_analysis=rows.sort_values("HorizonExcessPp")[["ManagerName", "AssetClassLevel1", "HorizonReturnPct", "HorizonBenchmarkPct", "HorizonExcessPp"]].head(8).to_dict(orient="records"),
            )
        ]
    trajectory = q1_to_q4_trajectory(full_allocation, full_manager)["allocation"]
    accel = sorted(trajectory, key=lambda row: abs(row["q1_to_q4_change_pp"]), reverse=True)[0]
    source = full_allocation[(full_allocation["FundCode"] == accel["FundCode"]) & (full_allocation["AssetClassLevel1"] == accel["AssetClassLevel1"])]
    return [
        _signal(
            id=f"EMERGING-FY-{accel['FundCode']}-{accel['AssetClassLevel1'].replace(' ', '-')}",
            type="emerging_signal",
            horizon=horizon,
            research_question="What did the standard report not immediately show?",
            headline=f"{accel['AssetClassLevel1']} had the largest Q1-to-Q4 drift movement.",
            fund=accel["FundCode"],
            asset_class=accel["AssetClassLevel1"],
            manager=None,
            primary_metric="Q1-to-Q4 drift change",
            primary_value=float(accel["q1_to_q4_change_pp"]),
            supporting_metrics=accel,
            observation=f"{accel['AssetClassLevel1']} drift changed by {_fmt_pp(accel['q1_to_q4_change_pp'])} from Q1 to Q4 for {accel['FundCode']}.",
            interpretation="This trajectory is easier to see in the research layer than in a point-in-time reporting table.",
            why_it_matters="A full-year drift trajectory can inform whether year-end positioning looks episodic or persistent.",
            cio_question="Was the drift expected market movement, or does it require a rebalancing discussion?",
            significance_score=78 + abs(float(accel["q1_to_q4_change_pp"])) * 7,
            source_rows=source,
            limitations="The data does not identify trades, market movements, or manager flows behind the drift change.",
            visual={"kind": "drift_line", "items": source[["Quarter", "PctOfFundTotal", "PolicyTargetPct", "VarianceToTargetPct", "DollarVariance"]].to_dict(orient="records")},
            related_analysis=source[["Quarter", "PctOfFundTotal", "PolicyTargetPct", "VarianceToTargetPct", "DollarVariance"]].to_dict(orient="records"),
        )
    ]


def generate_research_candidates(canonical: dict[str, pd.DataFrame], analytics: dict[str, pd.DataFrame], horizon: str = "FY2026") -> list[ResearchSignal]:
    fund_h = fund_horizon_view(analytics["fund_summary_view"], horizon)
    alloc_h = allocation_horizon_view(analytics["asset_allocation_view"], horizon)
    manager_h = manager_horizon_view(analytics["manager_performance_view"], horizon)
    candidates: list[ResearchSignal] = []
    candidates.extend(analyse_relative_performance(fund_h, alloc_h, horizon))
    candidates.extend(rank_allocation_drift(alloc_h, analytics["asset_allocation_view"], horizon))
    candidates.extend(calculate_manager_consistency(manager_h, analytics["manager_performance_view"], horizon))
    candidates.extend(analyse_cash_flow_patterns(fund_h, canonical["Cash_Flow_Detail"], horizon))
    candidates.extend(compare_funds(alloc_h, horizon))
    candidates.extend(detect_emerging_signal(alloc_h, analytics["asset_allocation_view"], manager_h, analytics["manager_performance_view"], horizon))
    return sorted(candidates, key=lambda signal: signal.significance_score, reverse=True)


def rank_research_candidates(candidates: list[ResearchSignal]) -> list[ResearchSignal]:
    selected: list[ResearchSignal] = []
    preferred = ["relative_performance", "policy_drift", "manager_consistency", "cash_flow", "emerging_signal"]
    for signal_type in preferred:
        matches = [s for s in candidates if s.type == signal_type and s not in selected]
        if matches:
            selected.append(matches[0])
    if len(selected) < 5:
        for signal in candidates:
            if signal not in selected:
                selected.append(signal)
            if len(selected) == 5:
                break
    for idx, signal in enumerate(selected[:5], start=1):
        signal.selected = True
        signal.visual["story_number"] = f"{idx:02d}"
    return selected[:5]


def build_research_by_horizon(canonical: dict[str, pd.DataFrame], analytics: dict[str, pd.DataFrame]) -> dict[str, dict[str, Any]]:
    output = {}
    for horizon in HORIZONS:
        candidates = generate_research_candidates(canonical, analytics, horizon)
        final = rank_research_candidates(candidates)
        output[horizon] = {
            "label": horizon_label(horizon),
            "candidates": signals_to_records(candidates),
            "final_signals": signals_to_records(final),
            "summary": research_summary(final, horizon),
        }
    return output


def research_summary(final_signals: list[ResearchSignal], horizon: str = "FY2026") -> str:
    label = horizon_label(horizon)
    rel = next((s for s in final_signals if s.type == "relative_performance"), None)
    drift = next((s for s in final_signals if s.type == "policy_drift"), None)
    mgr = next((s for s in final_signals if s.type == "manager_consistency"), None)
    cash = next((s for s in final_signals if s.type == "cash_flow"), None)
    parts = []
    if rel:
        parts.append(f"In {label}, {rel.fund} showed benchmark-relative results that were concentrated rather than evenly distributed.")
    if drift:
        parts.append(f"{drift.asset_class} created the clearest policy-drift question at {_fmt_pp(float(drift.primary_value))}.")
    if mgr:
        parts.append(f"{mgr.manager} stood out on manager trajectory and benchmark-relative consistency.")
    if cash:
        parts.append("Cash-flow patterns add context for liquidity and rebalancing discussions without proving a liquidity issue.")
    return " ".join(parts)


def _clean_json(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return _clean_json(value.drop(columns=["_provenance"], errors="ignore").to_dict(orient="records"))
    if isinstance(value, pd.Series):
        return _clean_json(value.drop(labels=["_provenance"], errors="ignore").to_dict())
    if isinstance(value, dict):
        return {str(k): _clean_json(v) for k, v in value.items() if k != "source"}
    if isinstance(value, list):
        return [_clean_json(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if not isinstance(value, (str, bytes, bool)):
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
    if hasattr(value, "item"):
        return _clean_json(value.item())
    return value


def signals_to_records(signals: list[ResearchSignal]) -> list[dict[str, Any]]:
    return [_clean_json(signal.__dict__) for signal in signals]
