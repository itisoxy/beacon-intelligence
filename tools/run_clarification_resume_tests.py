from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beacon_data import AskBeaconService, build_model


def run_case(value: str, label: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        model = build_model(ROOT / "Data", tmp_path, tmp_path / "beacon.duckdb")
    service = AskBeaconService(model)
    first = service.create_request("Who performed best?", {"fund": "BPT", "period": "FY2026"})
    resumed = service.clarify(first["request_id"], {"field": "ranking_metric", "value": value, "label": label})
    events = [event["status"] for event in resumed["debug_state"]["events"]]
    return {
        "label": label,
        "field": "ranking_metric",
        "value": value,
        "same_request_id": resumed["request_id"] == first["request_id"],
        "initial_type": first["type"],
        "initial_status": first["status"],
        "final_type": resumed["type"],
        "final_status": resumed["debug_state"]["current_status"],
        "answer": resumed["answer"],
        "metric_ids": [metric["metric_id"] for metric in resumed["metrics"]],
        "source_record_ids": resumed["metrics"][2]["provenance"]["source_record_ids"],
        "lifecycle": events,
        "passed": (
            first["type"] == "clarification"
            and resumed["type"] == "answer"
            and resumed["request_id"] == first["request_id"]
            and "rank_managers" in [event.get("tool_selected") for event in resumed["debug_state"]["events"]]
            and "validated" in events
            and bool(resumed["metrics"][2]["provenance"]["source_record_ids"])
        ),
    }


def write_report(results: list[dict]) -> None:
    passed = sum(1 for result in results if result["passed"])
    lines = [
        "# Ask Beacon Clarification Resume Test Report",
        "",
        f"Summary: {passed}/{len(results)} clarification choices resumed successfully.",
        "",
        "| Choice | Machine Value | Same Request | Final Status | Source Records | Result |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        lines.append(
            f"| {result['label']} | `{result['value']}` | {result['same_request_id']} | {result['final_status']} | {len(result['source_record_ids'])} | {'PASS' if result['passed'] else 'FAIL'} |"
        )
    lines.extend(["", "## Detailed Lifecycle", "", "```json", json.dumps(results, indent=2), "```"])
    (ROOT / "CLARIFICATION_RESUME_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    cases = [
        ("manager_return_pct", "Highest absolute return"),
        ("manager_excess_return_pp", "Highest return vs benchmark"),
        ("manager_consistency", "Most consistent outperformer"),
    ]
    results = [run_case(value, label) for value, label in cases]
    write_report(results)
    failed = [result for result in results if not result["passed"]]
    print(json.dumps({"passed": len(results) - len(failed), "failed": len(failed), "report": "CLARIFICATION_RESUME_REPORT.md"}, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
