from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beacon_data import AskBeaconConversation, ToolSelectingTestAdapter, build_model


CHECKPOINT_PATH = ROOT / "data" / "runtime" / "ask_beacon_checkpoints.sqlite"
REPORT_PATH = ROOT / "LANGGRAPH_AMBIGUITY_TEST_REPORT.md"


def _tool_observations(response: dict) -> list[dict]:
    observations = []
    for message in response["turn_messages"]:
        if message["role"] != "tool":
            continue
        observations.append(json.loads(message["content"]))
    return observations


def _selected_tools(response: dict) -> list[dict]:
    return [event for event in response["turn_tool_events"] if event["event"] == "tool_selected"]


def _completed_tools(response: dict) -> list[dict]:
    return [event for event in response["turn_tool_events"] if event["event"] == "tool_completed"]


def main() -> None:
    model = build_model(ROOT / "Data", ROOT / ".tmp-agent-debug", ROOT / ".tmp-agent-debug" / "beacon.duckdb")
    conversation = AskBeaconConversation(ToolSelectingTestAdapter(), CHECKPOINT_PATH, model=model)
    run_id = uuid.uuid4().hex[:8]

    benchmark_thread = f"thread_langgraph_ambiguity_{run_id}_benchmark"
    first = conversation.ask(benchmark_thread, "Who performed best?", {"fund": "BPT", "period": "FY2026"})
    second = conversation.ask(benchmark_thread, "Relative to benchmark.")
    third = conversation.ask(benchmark_thread, "How consistent were they?")

    absolute_thread = f"thread_langgraph_ambiguity_{run_id}_absolute"
    abs_first = conversation.ask(absolute_thread, "Who performed best?", {"fund": "BPT", "period": "FY2026"})
    abs_second = conversation.ask(absolute_thread, "Absolute return.")

    benchmark_rank = next(event for event in _selected_tools(second) if event["tool"] == "rank_managers")
    benchmark_perf = next(event for event in _selected_tools(second) if event["tool"] == "get_manager_performance")
    followup_tool = next(event for event in _selected_tools(third) if event["tool"] == "get_manager_performance")
    absolute_rank = next(event for event in _selected_tools(abs_second) if event["tool"] == "rank_managers")

    checks = {
        "first_turn_clarifies_without_tool": "highest absolute return" in first["answer"] and not _selected_tools(first),
        "benchmark_reply_selects_rank_managers": benchmark_rank["arguments"] == {
            "fund": "BPT",
            "period": "FY2026",
            "metric": "excess_return",
            "direction": "descending",
            "limit": 1,
        },
        "benchmark_answer_uses_real_tool_data": bool(second["turn_sources"] and "strongest benchmark-relative performance" in second["answer"]),
        "pronoun_followup_uses_prior_manager": followup_tool["arguments"]["manager"] == benchmark_perf["arguments"]["manager"] and "outperformed in" in third["answer"],
        "absolute_thread_uses_absolute_return": absolute_rank["arguments"]["metric"] == "absolute_return" and "highest absolute return" in abs_second["answer"],
    }

    report = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checkpoint_path": str(CHECKPOINT_PATH.relative_to(ROOT)),
        "benchmark_relative_flow": {
            "thread_id": benchmark_thread,
            "application_context": {"fund": "BPT", "period": "FY2026"},
            "first_user_message": "Who performed best?",
            "assistant_clarification": first["answer"],
            "user_clarification": "Relative to benchmark.",
            "model_selected_tools": _selected_tools(second),
            "tool_arguments": [event["arguments"] for event in _selected_tools(second)],
            "actual_data_results": _tool_observations(second),
            "provenance": second["turn_sources"],
            "final_natural_language_response": second["answer"],
            "completed_tools": _completed_tools(second),
        },
        "follow_up_pronoun_test": {
            "user_message": "How consistent were they?",
            "model_selected_tools": _selected_tools(third),
            "actual_data_results": _tool_observations(third),
            "provenance": third["turn_sources"],
            "final_natural_language_response": third["answer"],
        },
        "absolute_return_flow": {
            "thread_id": absolute_thread,
            "first_user_message": "Who performed best?",
            "assistant_clarification": abs_first["answer"],
            "user_clarification": "Absolute return.",
            "model_selected_tools": _selected_tools(abs_second),
            "actual_data_results": _tool_observations(abs_second),
            "provenance": abs_second["turn_sources"],
            "final_natural_language_response": abs_second["answer"],
        },
        "checks": checks,
    }
    REPORT_PATH.write_text(
        "# LangGraph Ambiguity Test Report\n\n"
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
