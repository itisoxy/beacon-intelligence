from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beacon_data import AskBeaconAgent, ModelResponse, ScriptedModelAdapter, ToolCall, build_model
from beacon_data.business_tools import BeaconBusinessTools


def build_temp_model() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        return build_model(ROOT / "Data", tmp_path, tmp_path / "beacon.duckdb")


def events(result: dict[str, Any]) -> list[str]:
    return [row["event"] for row in result["events"]]


def first_tool(result: dict[str, Any]) -> dict[str, Any]:
    return result["tool_observations"][0]


def run_agent(model: dict[str, Any], query: str, responses: list[ModelResponse], context: dict[str, Any] | None = None) -> dict[str, Any]:
    return AskBeaconAgent(model, ScriptedModelAdapter(responses)).answer(query, context)


def pass_if(condition: bool, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"passed": bool(condition), **evidence}


def scenario_simple_lookup(model: dict[str, Any]) -> dict[str, Any]:
    result = run_agent(
        model,
        "What was BLE's Private Equity allocation versus policy target in Q3?",
        [
            ModelResponse(tool_calls=[ToolCall("get_asset_allocation", {"fund": "BLE", "period": "Q3", "asset_class": "Private Equity"})]),
            ModelResponse(final_answer="BLE Private Equity was 20.8% versus a 20.0% policy target in Q3, a +0.80pp drift. Source: 20260331_FYTD.xlsx, Asset_Allocation row 50."),
        ],
    )
    obs = first_tool(result)
    actual = obs["metrics"]["actual_allocation"]
    target = obs["metrics"]["policy_target"]
    drift = obs["metrics"]["drift_pp"]
    return pass_if(
        result["ok"] and actual["value"] == 20.8 and target["value"] == 20.0 and round(drift["value"], 2) == 0.8 and "source_verified" in events(result),
        {
            "query": "What was BLE's Private Equity allocation versus policy target in Q3?",
            "answer": result["answer"],
            "tool_calls": [o["tool"] for o in result["tool_observations"]],
            "actual": actual["value"],
            "target": target["value"],
            "drift_pp": drift["value"],
            "provenance": drift["provenance"],
            "events": events(result),
        },
    )


def scenario_manager_ranking(model: dict[str, Any]) -> dict[str, Any]:
    result = run_agent(
        model,
        "Which manager underperformed its benchmark by the widest margin in Q4, and by how much?",
        [
            ModelResponse(tool_calls=[ToolCall("rank_managers", {"period": "Q4", "metric": "excess return", "direction": "asc", "limit": 1})]),
            ModelResponse(final_answer="Northbridge Global Equity Fund underperformed by the widest Q4 margin at -0.341pp versus benchmark. Source: 20260630_FYTD.xlsx, Manager_Detail row 121."),
        ],
    )
    row = first_tool(result)["rows"][0]
    return pass_if(
        result["ok"] and row["manager"] == "Northbridge Global Equity Fund" and round(row["metric"]["value"], 3) == -0.341,
        {
            "query": "Which manager underperformed its benchmark by the widest margin in Q4, and by how much?",
            "answer": result["answer"],
            "tool_calls": [o["tool"] for o in result["tool_observations"]],
            "ranked_manager": row["manager"],
            "metric_value": row["metric"]["value"],
            "provenance": row["metric"]["provenance"],
            "events": events(result),
        },
    )


def scenario_multi_step(model: dict[str, Any]) -> dict[str, Any]:
    result = run_agent(
        model,
        "What are the three most important things BPT should investigate this year?",
        [
            ModelResponse(tool_calls=[ToolCall("get_fund_summary", {"fund": "BPT", "period": "FY2026"})]),
            ModelResponse(tool_calls=[ToolCall("get_research_signals", {"fund": "BPT", "period": "FY2026"})]),
            ModelResponse(tool_calls=[ToolCall("rank_managers", {"period": "FY2026", "metric": "excess return", "direction": "asc", "fund": "BPT", "limit": 3})]),
            ModelResponse(tool_calls=[ToolCall("get_cash_flows", {"fund": "BPT", "period": "FY2026"})]),
            ModelResponse(final_answer="Investigate BPT Cash policy drift, weakest benchmark-relative managers, and FY2026 cash-flow pressure. These are sourced findings, not causal claims."),
        ],
    )
    observations = result["tool_observations"]
    return pass_if(
        result["ok"] and len(observations) == 4 and all(o["ok"] for o in observations) and events(result).count("tool_completed") == 4,
        {
            "query": "What are the three most important things BPT should investigate this year?",
            "answer": result["answer"],
            "tool_calls": [o["tool"] for o in observations],
            "intermediate_results": {
                "fund_summary_metrics": list(observations[0]["metrics"].keys()),
                "research_signal_count": len(observations[1]["rows"]),
                "manager_rank_count": len(observations[2]["rows"]),
                "cash_flow_rows": len(observations[3]["rows"]),
            },
            "events": events(result),
        },
    )


def scenario_contextual(model: dict[str, Any]) -> dict[str, Any]:
    context = {"fund": "BPT", "period": "FY2026", "asset_class": "Private Equity", "source_page": "portfolio"}
    result = run_agent(
        model,
        "Compare this with BLE.",
        [
            ModelResponse(tool_calls=[ToolCall("compare_funds", {"metric": "allocation_drift_pp", "period": "FY2026", "asset_class": "Private Equity"})]),
            ModelResponse(final_answer="Using the BPT / FY2026 / Private Equity context, BPT drift was +0.97pp versus BLE at +0.94pp."),
        ],
        context,
    )
    interpretation = result["events"][0]["interpretation"]
    return pass_if(
        result["ok"] and interpretation["fund"] == "BPT" and interpretation["asset_class"] == "Private Equity" and interpretation["compare_to_fund"] == "BLE",
        {
            "query": "Compare this with BLE.",
            "context": context,
            "resolved_interpretation": interpretation,
            "answer": result["answer"],
            "tool_calls": [o["tool"] for o in result["tool_observations"]],
            "events": events(result),
        },
    )


def scenario_ambiguous(model: dict[str, Any]) -> dict[str, Any]:
    adapter = ScriptedModelAdapter([])
    result = AskBeaconAgent(model, adapter).answer("Which manager performed best?")
    return pass_if(
        not result["ok"] and result["outcome"] == "clarify" and adapter.calls == 0 and "Highest excess return vs benchmark" in result["answer"],
        {
            "query": "Which manager performed best?",
            "answer": result["answer"],
            "model_calls": adapter.calls,
            "events": events(result),
        },
    )


def scenario_unsupported_causality(model: dict[str, Any]) -> dict[str, Any]:
    adapter = ScriptedModelAdapter([])
    result = AskBeaconAgent(model, adapter).answer("Why did Manager X change investment strategy?")
    return pass_if(
        not result["ok"] and result["outcome"] == "out_of_scope" and adapter.calls == 0 and "cannot establish why an investment strategy changed" in result["answer"],
        {
            "query": "Why did Manager X change investment strategy?",
            "answer": result["answer"],
            "model_calls": adapter.calls,
            "events": events(result),
        },
    )


def scenario_invalid(model: dict[str, Any]) -> dict[str, Any]:
    result = run_agent(
        model,
        "Show BPT Q8.",
        [
            ModelResponse(tool_calls=[ToolCall("get_fund_summary", {"fund": "BPT", "period": "Q8"})]),
            ModelResponse(final_answer="Q8 is not a supported Beacon period."),
        ],
        {"fund": "BPT", "period": "Q4"},
    )
    err = first_tool(result)["error"]
    return pass_if(
        not result["ok"] and err["code"] == "invalid_period",
        {
            "query": "Show BPT Q8.",
            "answer": result["answer"],
            "tool_calls": [o["tool"] for o in result["tool_observations"]],
            "error": err,
            "events": events(result),
        },
    )


def scenario_traceability(model: dict[str, Any]) -> dict[str, Any]:
    tools = BeaconBusinessTools(model)
    answer_result = tools.get_asset_allocation("BLE", "Q3", "Private Equity")
    metric = answer_result["metrics"]["drift_pp"]
    metric_record_id = metric["record_id"]
    metric_record = next(row for row in model["metric_values"] if row["metric_value_id"] == metric_record_id)
    source_record_id = metric_record["source_record_ids"][0]
    normalized_record = next(row for row in model["canonical"]["asset_allocations"] if row["source_record_id"] == source_record_id)
    source_lookup = tools.get_source_record(normalized_record["record_id"])
    return pass_if(
        metric["value"] == 0.8
        and metric_record["metric_id"] == "allocation_drift_pp"
        and normalized_record["record_id"] == "ASSET_ALLOC_FY2026_BLE_Q3_PRIVATE_EQUITY"
        and normalized_record["source_file"] == "20260331_FYTD.xlsx"
        and normalized_record["source_sheet"] == "Asset_Allocation"
        and normalized_record["source_row"] == 50
        and normalized_record["source_cells"] == "A50:S50"
        and source_lookup["ok"],
        {
            "answer": "BLE Private Equity Q3 allocation drift was +0.80pp.",
            "canonical_metric": metric_record_id,
            "normalized_record": normalized_record["record_id"],
            "source_record_id": source_record_id,
            "workbook": normalized_record["source_file"],
            "sheet": normalized_record["source_sheet"],
            "row": normalized_record["source_row"],
            "cells": normalized_record["source_cells"],
            "source_lookup": source_lookup["record"],
        },
    )


def write_report(results: dict[str, dict[str, Any]], path: Path) -> None:
    passed = sum(1 for row in results.values() if row["passed"])
    lines = [
        "# Beacon 4.3 Ask Beacon End-to-End Assessment",
        "",
        f"Summary: {passed}/{len(results)} demonstrated requirements passed.",
        "",
        "| Scenario | Requirement | Result | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    requirement_map = {
        "simple_lookup": "Tool-selected simple lookup with real data and provenance",
        "manager_ranking": "Deterministic manager ranking by excess return",
        "multi_step": "Multiple tools and evidence-backed synthesis",
        "contextual": "Safe UI context resolution",
        "ambiguous": "Clarification instead of guessing",
        "unsupported_causality": "Limitation plus useful redirect",
        "invalid": "Graceful invalid-period handling",
        "source_traceability": "Answer to metric to record to workbook/sheet/row/cell trace",
    }
    for name, row in results.items():
        evidence = row.get("answer") or row.get("error", {}).get("message") or "executed"
        lines.append(f"| {name} | {requirement_map[name]} | {'PASS' if row['passed'] else 'FAIL'} | {str(evidence).replace('|', '/')} |")
    lines.extend(["", "## Detailed Evidence", "", "```json", json.dumps(results, indent=2), "```"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    model = build_temp_model()
    results = {
        "simple_lookup": scenario_simple_lookup(model),
        "manager_ranking": scenario_manager_ranking(model),
        "multi_step": scenario_multi_step(model),
        "contextual": scenario_contextual(model),
        "ambiguous": scenario_ambiguous(model),
        "unsupported_causality": scenario_unsupported_causality(model),
        "invalid": scenario_invalid(model),
        "source_traceability": scenario_traceability(model),
    }
    write_report(results, ROOT / "BEACON_43_ASSESSMENT.md")
    failed = [name for name, row in results.items() if not row["passed"]]
    print(json.dumps({"passed": len(results) - len(failed), "failed": len(failed), "report": "BEACON_43_ASSESSMENT.md", "failed_scenarios": failed}, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
