from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beacon_data import build_model
from beacon_data.business_tools import BeaconBusinessTools


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _metric_value(result: dict[str, Any], path: list[str]) -> Any:
    value: Any = result
    for key in path:
        value = value[key]
    return value


def run_suite() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        model = build_model(ROOT / "Data", Path(tmp), Path(tmp) / "beacon.duckdb")
    tools = BeaconBusinessTools(model)
    cases: list[tuple[str, str, Callable[[], dict[str, Any]], Callable[[dict[str, Any]], None]]] = [
        (
            "DIRECT LOOKUP",
            "What was BLE's Private Equity allocation versus policy in Q3?",
            lambda: tools.get_asset_allocation("BLE", "Q3", "Private Equity"),
            lambda r: (
                _assert(r["ok"], "direct lookup should succeed"),
                _assert(r["metrics"]["actual_allocation"]["value"] == 20.8, "actual allocation should match canonical metric"),
                _assert(r["metrics"]["policy_target"]["value"] == 20.0, "policy target should match canonical metric"),
                _assert(round(r["metrics"]["drift_pp"]["value"], 2) == 0.8, "drift should be +0.80pp"),
                _assert(bool(r["metrics"]["drift_pp"]["provenance"]["source_files"]), "drift should include provenance"),
            ),
        ),
        (
            "MANAGER RANKING",
            "Which manager had the weakest benchmark-relative performance in Q4?",
            lambda: tools.rank_managers("Q4", "excess return", "asc", limit=1),
            lambda r: (
                _assert(r["ok"], "manager ranking should succeed"),
                _assert(r["rows"][0]["manager"] == "Northbridge Global Equity Fund", "weakest Q4 manager should be deterministic"),
                _assert(r["rows"][0]["metric"]["metric_id"] == "manager_excess_return_pp", "ranking metric should be excess return"),
                _assert(bool(r["rows"][0]["metric"]["provenance"]["source_record_ids"]), "ranking should include source record"),
            ),
        ),
        (
            "PERIOD COMPARISON",
            "How did BPT Cash allocation change between Q3 and Q4?",
            lambda: tools.compare_periods("Cash", "allocation_drift_pp", "Q3", "Q4", fund="BPT"),
            lambda r: (
                _assert(r["ok"], "period comparison should succeed"),
                _assert([row["period"] for row in r["rows"]] == ["Q3", "Q4"], "should retrieve two periods"),
                _assert(round(r["comparison"]["period_b_minus_period_a"], 2) == -1.58, "Q4 minus Q3 drift should be deterministic"),
                _assert(all(row["metric"]["provenance"]["source_record_ids"] for row in r["rows"]), "both periods should include source records"),
            ),
        ),
        (
            "FUND COMPARISON",
            "Compare BPT and BLE Private Equity allocation in Q4.",
            lambda: tools.compare_funds("allocation_drift_pp", "Q4", asset_class="Private Equity"),
            lambda r: (
                _assert(r["ok"], "fund comparison should succeed"),
                _assert([row["fund"] for row in r["rows"]] == ["BPT", "BLE"], "should compare BPT and BLE"),
                _assert(round(r["rows"][0]["metric"]["value"], 2) == 0.97, "BPT Q4 PE drift should match metric"),
                _assert(round(r["rows"][1]["metric"]["value"], 2) == 0.94, "BLE Q4 PE drift should match metric"),
            ),
        ),
        (
            "RESEARCH SIGNAL",
            "What are the largest BPT research signals?",
            lambda: tools.get_research_signals(fund="BPT", period="FY2026"),
            lambda r: (
                _assert(r["ok"], "research signal lookup should succeed"),
                _assert(len(r["rows"]) >= 1, "should return BPT research signals"),
                _assert(all(row["provenance"]["source_record_ids"] for row in r["rows"]), "signals should include provenance"),
            ),
        ),
        (
            "RECONCILIATION",
            "Does BPT Q4 reconcile?",
            lambda: tools.validate_reconciliation("BPT", "Q4"),
            lambda r: (
                _assert(r["ok"], "reconciliation should succeed"),
                _assert(r["metrics"]["allocation_validation"]["value_text"] == "pass", "allocation validation should pass"),
                _assert(abs(r["metrics"]["reconciliation_variance"]["value"]) <= 0.05, "roll-forward variance should be within tolerance"),
            ),
        ),
        (
            "INVALID PERIOD",
            "Q8",
            lambda: tools.get_fund_summary("BPT", "Q8"),
            lambda r: _assert(r["error"]["code"] == "invalid_period", "Q8 should return invalid_period"),
        ),
        (
            "UNKNOWN FUND",
            "unknown fund",
            lambda: tools.get_fund_summary("XYZ", "Q4"),
            lambda r: _assert(r["error"]["code"] == "unknown_entity", "unknown fund should return unknown_entity"),
        ),
        (
            "UNKNOWN MANAGER",
            "unknown manager",
            lambda: tools.get_manager_history("Unknown Manager"),
            lambda r: _assert(r["error"]["code"] == "unknown_entity", "unknown manager should return unknown_entity"),
        ),
        (
            "MISSING DATA",
            "period outside FY2026",
            lambda: tools.get_fund_summary("BPT", "FY2027"),
            lambda r: _assert(r["error"]["code"] == "no_data", "period outside FY2026 should return no_data"),
        ),
        (
            "UNSUPPORTED METRIC",
            "unsupported metric",
            lambda: tools.compare_funds("sharpe_ratio", "Q4"),
            lambda r: _assert(r["error"]["code"] == "unsupported_metric", "unsupported metric should return unsupported_metric"),
        ),
    ]

    results = []
    for category, question, call, check in cases:
        response = call()
        try:
            check(response)
            status = "pass"
            error = None
        except AssertionError as exc:
            status = "fail"
            error = str(exc)
        results.append({"category": category, "question": question, "status": status, "error": error, "response": response})
    return results, model


def write_report(results: list[dict[str, Any]], path: Path) -> None:
    passed = sum(1 for row in results if row["status"] == "pass")
    lines = [
        "# Beacon Tool Test Report",
        "",
        f"Summary: {passed}/{len(results)} checks passed.",
        "",
        "| Category | Question | Status | Error Code |",
        "| --- | --- | --- | --- |",
    ]
    for row in results:
        response = row["response"]
        code = response.get("error", {}).get("code", "")
        lines.append(f"| {row['category']} | {row['question']} | {row['status']} | {code} |")
    lines.extend(
        [
            "",
            "Representative provenance:",
            "",
            "```json",
            json.dumps(_metric_value(results[0]["response"], ["metrics", "drift_pp", "provenance"]), indent=2),
            "```",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    results, _ = run_suite()
    write_report(results, ROOT / "TOOL_TEST_REPORT.md")
    failed = [row for row in results if row["status"] != "pass"]
    print(json.dumps({"passed": len(results) - len(failed), "failed": len(failed), "report": "TOOL_TEST_REPORT.md"}, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
