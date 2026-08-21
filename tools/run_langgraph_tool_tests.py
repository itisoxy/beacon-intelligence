from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beacon_data import AskBeaconConversation, ToolSelectingTestAdapter, build_model


CHECKPOINT_PATH = ROOT / "data" / "runtime" / "ask_beacon_checkpoints.sqlite"
REPORT_PATH = ROOT / "LANGGRAPH_TOOL_TEST_REPORT.md"


QUESTIONS = [
    "What was BPT's FY2026 return?",
    "How far was BPT Cash from policy in Q4?",
    "Which manager underperformed its benchmark most in Q4?",
    "What was BLE Private Equity allocation versus policy in Q3?",
]


def main() -> None:
    model = build_model(ROOT / "Data", ROOT / ".tmp-agent-debug", ROOT / ".tmp-agent-debug" / "beacon.duckdb")
    conversation = AskBeaconConversation(ToolSelectingTestAdapter(), CHECKPOINT_PATH, model=model)
    run_id = uuid.uuid4().hex[:8]
    results = []

    for index, question in enumerate(QUESTIONS, start=1):
        response = conversation.ask(f"thread_langgraph_tool_{run_id}_{index}", question)
        selected = next(event for event in response["turn_tool_events"] if event["event"] == "tool_selected")
        completed = next(event for event in response["turn_tool_events"] if event["event"] == "tool_completed")
        tool_messages = [message for message in response["turn_messages"] if message["role"] == "tool"]
        observation = json.loads(tool_messages[-1]["content"])
        passed = bool(completed["ok"] and completed["record_ids"] and response["turn_sources"] and response["answer"])
        results.append(
            {
                "status": "PASS" if passed else "FAIL",
                "question": question,
                "model_selected_tool": selected["tool"],
                "arguments": selected["arguments"],
                "actual_tool_result": observation,
                "provenance": response["turn_sources"],
                "natural_language_answer": response["answer"],
            }
        )

    report = {
        "status": "PASS" if all(item["status"] == "PASS" for item in results) else "FAIL",
        "checkpoint_path": str(CHECKPOINT_PATH.relative_to(ROOT)),
        "results": results,
    }
    REPORT_PATH.write_text(
        "# LangGraph Tool Test Report\n\n"
        f"Status: {report['status']}\n\n"
        "```json\n"
        f"{json.dumps(report, indent=2)}\n"
        "```\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))
    conversation.close()


if __name__ == "__main__":
    main()
