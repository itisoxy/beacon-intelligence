from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beacon_data import AskBeaconAgent, ModelResponse, ScriptedModelAdapter, ToolCall, build_model


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        model = build_model(ROOT / "Data", tmp_path, tmp_path / "beacon.duckdb")
    simple = AskBeaconAgent(
        model,
        ScriptedModelAdapter(
            [
                ModelResponse(tool_calls=[ToolCall("get_asset_allocation", {"fund": "BLE", "period": "Q3", "asset_class": "Private Equity"})]),
                ModelResponse(final_answer="BLE Private Equity was 20.8% versus a 20.0% policy target in Q3, a +0.80pp drift. Source: 20260331_FYTD.xlsx, Asset_Allocation row 50."),
            ]
        ),
    ).answer("What was BLE's Private Equity allocation versus target in Q3?")
    multi = AskBeaconAgent(
        model,
        ScriptedModelAdapter(
            [
                ModelResponse(tool_calls=[ToolCall("get_fund_summary", {"fund": "BPT", "period": "FY2026"})]),
                ModelResponse(tool_calls=[ToolCall("get_research_signals", {"fund": "BPT", "period": "FY2026"})]),
                ModelResponse(tool_calls=[ToolCall("rank_managers", {"period": "FY2026", "metric": "excess return", "direction": "asc", "fund": "BPT", "limit": 3})]),
                ModelResponse(tool_calls=[ToolCall("get_cash_flows", {"fund": "BPT", "period": "FY2026"})]),
                ModelResponse(final_answer="Investigate BPT's largest policy drift, weakest benchmark-relative managers, and cash-flow pattern. These are sourced observations, not unsupported causal claims."),
            ]
        ),
    ).answer("What should I investigate about BPT this year?")
    payload = {
        "simple": {"status": simple["status"], "answer": simple["answer"], "events": simple["events"]},
        "multi_step": {"status": multi["status"], "answer": multi["answer"], "events": multi["events"]},
    }
    (ROOT / "ASK_BEACON_AGENT_EXAMPLES.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"simple": simple["status"], "multi_step": multi["status"], "output": "ASK_BEACON_AGENT_EXAMPLES.json"}, indent=2))


if __name__ == "__main__":
    main()
