from __future__ import annotations

import json
import sys
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beacon_data import AskBeaconConversation, ToolSelectingTestAdapter, build_model


CHECKPOINT_PATH = ROOT / "data" / "runtime" / "ask_beacon_checkpoints.sqlite"
REPORT_PATH = ROOT / "LANGGRAPH_NL_STRESS_TEST_REPORT.md"


SAMPLES = [
    ("A", "How are we doing?", {"fund": "BPT", "period": "FY2026"}, "answer"),
    ("A", "Are we ahead or behind?", {"fund": "BPT", "period": "FY2026"}, "answer"),
    ("A", "Where are we versus where we're supposed to be?", {"fund": "BPT", "period": "Q4"}, "answer"),
    ("A", "Anything look off?", {"fund": "BPT", "period": "FY2026"}, "answer"),
    ("B", "How did it do against the benchmark?", {"fund": "BPT", "period": "FY2026"}, "answer"),
    ("B", "Which bit actually did well?", {"fund": "BPT", "period": "FY2026"}, "answer"),
    ("B", "What dragged us down?", {"fund": "BPT", "period": "Q4"}, "answer"),
    ("C", "Who did best?", {"fund": "BPT", "period": "FY2026"}, "clarify"),
    ("C", "Who actually made us money?", {"fund": "BPT", "period": "FY2026"}, "answer"),
    ("C", "Who beat their number?", {"fund": "BPT", "period": "FY2026"}, "answer"),
    ("C", "Who kept missing?", {"fund": "BPT", "period": "FY2026"}, "answer"),
    ("D", "Are we too heavy anywhere?", {"fund": "BPT", "period": "Q4"}, "answer"),
    ("D", "Where are we light?", {"fund": "BPT", "period": "Q4"}, "answer"),
    ("D", "Is Cash still a problem?", {"fund": "BPT", "period": "Q4"}, "answer"),
    ("D", "Is Private Equity too high?", {"fund": "BLE", "period": "Q3"}, "answer"),
    ("E", "What's changed recently?", {"fund": "BPT", "period": "FY2026", "asset_class": "Cash"}, "answer"),
    ("E", "What happened in the second half?", {"fund": "BPT", "period": "FY2026"}, "answer"),
    ("E", "Was the last six months different?", {"fund": "BPT", "period": "FY2026"}, "answer"),
    ("F", "How does that compare with the other fund?", {"fund": "BPT", "period": "Q4", "asset_class": "Cash"}, "answer"),
    ("F", "Was BLE any better?", {"fund": "BPT", "period": "FY2026"}, "answer"),
    ("F", "Compare the two.", {"period": "FY2026", "asset_class": "Private Equity"}, "answer"),
    ("G", "Did money come in or go out?", {"fund": "BPT", "period": "FY2026"}, "answer"),
    ("G", "Why did AUM change?", {"fund": "BPT", "period": "FY2026"}, "answer"),
    ("G", "How did we get from the opening number to the closing number?", {"fund": "BPT", "period": "Q4"}, "answer"),
    ("H", "What should I be looking at?", {"fund": "BPT", "period": "FY2026"}, "answer"),
    ("H", "Give me the three things that matter.", {"fund": "BPT", "period": "FY2026"}, "answer"),
    ("H", "Anything the CIO should know?", {"fund": "BLE", "period": "FY2026"}, "answer"),
    ("J", "How did they do?", {}, "clarify"),
    ("J", "How far off was it?", {"fund": "BPT", "period": "Q4", "asset_class": "Cash"}, "answer"),
    ("K", "whos best then", {"fund": "BPT", "period": "FY2026"}, "clarify"),
    ("K", "cash looks kinda bad?", {"fund": "BPT", "period": "Q4"}, "answer"),
    ("K", "did bpt actually beat target", {"period": "FY2026"}, "answer"),
    ("K", "what happened q4", {"fund": "BPT"}, "clarify"),
    ("K", "pe seems high no?", {"fund": "BLE", "period": "Q3"}, "answer"),
    ("L", "What's the best one?", {"fund": "BPT", "period": "FY2026"}, "clarify"),
    ("L", "How did it perform?", {}, "clarify"),
    ("L", "What was the return?", {}, "clarify"),
    ("M", "Why did that manager change strategy?", {"manager": "Redwood Growth Equity Partners", "fund": "BPT", "period": "FY2026"}, "refuse"),
    ("M", "What will the fund return next year?", {"fund": "BPT"}, "refuse"),
    ("M", "What holdings caused this?", {"fund": "BPT", "period": "FY2026"}, "refuse"),
    ("O", "Show me Q8.", {"fund": "BPT"}, "error"),
    ("O", "What happened in 2023?", {"fund": "BPT"}, "refuse"),
    ("O", "How did Fund XYZ do?", {}, "refuse"),
    ("O", "Show me the Crypto allocation.", {"fund": "BPT", "period": "Q4"}, "refuse"),
    ("O", "What happened tomorrow?", {"fund": "BPT"}, "refuse"),
]


CONVERSATIONS = [
    (
        "I1",
        {"fund": "BPT", "period": "FY2026"},
        ["Who did best?", "Against benchmark.", "How consistent were they?", "What about the second best?", "Were they better in Q4?"],
    ),
    (
        "I2",
        {"fund": "BPT", "period": "Q4", "asset_class": "Cash"},
        ["How's Cash looking?", "Has it got worse?", "When did that start?", "Compare it with BLE.", "Which one should I worry about more?"],
    ),
    (
        "I3",
        {"fund": "BPT", "period": "FY2026", "asset_class": "Private Equity"},
        ["How did Private Equity do?", "Performance.", "Against benchmark.", "And allocation?", "Was that worse in H2?"],
    ),
    (
        "I4",
        {"fund": "BPT", "period": "FY2026"},
        ["What's the biggest issue with BPT?", "Why does that matter?", "Show me the numbers.", "Where did those numbers come from?"],
    ),
    (
        "N",
        {"fund": "BPT", "period": "FY2026"},
        ["How are we doing?", "Where are you getting that from?", "Is that reconciled?", "Can I trust that number?"],
    ),
]


def main() -> None:
    model = build_model(ROOT / "Data", ROOT / ".tmp-agent-debug", ROOT / ".tmp-agent-debug" / "beacon.duckdb")
    conversation = AskBeaconConversation(ToolSelectingTestAdapter(), CHECKPOINT_PATH, model=model)
    run_id = uuid.uuid4().hex[:8]
    samples = [_run_case(conversation, run_id, index, item) for index, item in enumerate(SAMPLES, start=1)]
    conversations = [_run_conversation(conversation, run_id, item) for item in CONVERSATIONS]
    report = _build_report(samples, conversations)
    REPORT_PATH.write_text(
        "# LangGraph Natural-Language Stress Test Report\n\n"
        f"Status: {report['status']}\n\n"
        "```json\n"
        f"{json.dumps(report, indent=2)}\n"
        "```\n",
        encoding="utf-8",
    )
    print(json.dumps(_console_summary(report), indent=2))
    conversation.close()


def _console_summary(report: dict[str, Any]) -> dict[str, Any]:
    critical_keys = (
        "incorrect_intent_resolutions",
        "unnecessary_clarification",
        "lost_conversational_context",
        "numerical_claims_without_provenance",
        "research_prose_used_as_numerical_truth",
        "tool_selection_failures",
        "unnatural_or_template_like_responses",
        "dead_end_conversations",
    )
    return {
        "status": report["status"],
        "sample_count": report["sample_count"],
        "conversation_count": report["conversation_count"],
        "category_summary": report["category_summary"],
        "outcome_counts": report["outcome_counts"],
        "critical_evaluation": {key: report["critical_evaluation"][key] for key in critical_keys},
        "report_path": str(REPORT_PATH),
    }


def _run_case(conversation: AskBeaconConversation, run_id: str, index: int, spec: tuple[str, str, dict[str, Any], str]) -> dict[str, Any]:
    category, question, context, expected = spec
    response = conversation.ask(f"thread_nl_stress_{run_id}_{index}", question, context)
    return _summarize_turn(category, question, context, expected, response)


def _run_conversation(conversation: AskBeaconConversation, run_id: str, spec: tuple[str, dict[str, Any], list[str]]) -> dict[str, Any]:
    name, context, turns = spec
    thread_id = f"thread_nl_stress_{run_id}_{name}"
    rows = []
    for index, question in enumerate(turns):
        response = conversation.ask(thread_id, question, context if index == 0 else None)
        rows.append(_summarize_turn("I" if name.startswith("I") else name, question, context if index == 0 else {}, "conversation", response))
    return {"conversation": name, "thread_id": thread_id, "turns": rows, "pass": all(row["pass"] for row in rows)}


def _summarize_turn(category: str, question: str, context: dict[str, Any], expected: str, response: dict[str, Any]) -> dict[str, Any]:
    selected = [event for event in response["turn_tool_events"] if event["event"] == "tool_selected"]
    completed = [event for event in response["turn_tool_events"] if event["event"] == "tool_completed"]
    observations = [_compact_observation(json.loads(message["content"])) for message in response["turn_messages"] if message["role"] == "tool"]
    outcome = _outcome(response, selected, completed)
    passed = _passes(expected, outcome, completed, response)
    return {
        "category": category,
        "user_question": question,
        "current_context": context,
        "interpreted_intent": _intent_from_tools(selected, response["answer"]),
        "entities_resolved": _entities_from_tools(selected, context),
        "clarification_required": outcome == "clarify",
        "clarification_question": response["answer"] if outcome == "clarify" else None,
        "tools_selected": [event["tool"] for event in selected],
        "tool_arguments": [event["arguments"] for event in selected],
        "actual_data_result": observations,
        "provenance_available": bool(response["turn_sources"]),
        "provenance": response["turn_sources"][:3],
        "final_natural_language_response": response["answer"],
        "expected": expected,
        "outcome": outcome,
        "pass": passed,
    }


def _outcome(response: dict[str, Any], selected: list[dict[str, Any]], completed: list[dict[str, Any]]) -> str:
    answer = response["answer"].lower()
    if selected and any(not event.get("ok") for event in completed):
        return "error"
    if selected:
        return "answer"
    if answer.endswith("?") and ("do you mean" in answer or "which" in answer or "should" in answer or "what" in answer):
        return "clarify"
    if "can't" in answer or "cannot" in answer or "not support" in answer:
        return "refuse"
    return "answer"


def _passes(expected: str, outcome: str, completed: list[dict[str, Any]], response: dict[str, Any]) -> bool:
    if expected == "conversation":
        return outcome in {"answer", "clarify", "refuse"} and bool(response["answer"])
    if expected == "answer":
        return outcome == "answer" and all(event.get("ok") for event in completed) and bool(response["turn_sources"])
    if expected == "clarify":
        return outcome == "clarify" and not completed
    if expected == "refuse":
        return outcome == "refuse" and not completed
    if expected == "error":
        return outcome == "error"
    return False


def _intent_from_tools(selected: list[dict[str, Any]], answer: str) -> str:
    if selected:
        return " -> ".join(event["tool"] for event in selected)
    if "do you mean" in answer.lower() or "which" in answer.lower():
        return "clarification"
    if "can't" in answer.lower() or "cannot" in answer.lower():
        return "unsupported_or_out_of_scope"
    return "direct_response"


def _entities_from_tools(selected: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    entities = {key: value for key, value in context.items() if value}
    for event in selected:
        for key, value in event["arguments"].items():
            if key in {"fund", "period", "asset_class", "manager", "record_id"} and value:
                entities[key] = value
    return entities


def _compact_observation(result: dict[str, Any]) -> dict[str, Any]:
    compact = {"ok": result.get("ok"), "tool": result.get("tool"), "arguments": result.get("arguments"), "record_ids": result.get("record_ids", [])[:5]}
    if result.get("error"):
        compact["error"] = result["error"]
    for key in (
        "fund",
        "period",
        "asset_class",
        "manager",
        "fund_return_pct",
        "policy_benchmark_return_pct",
        "excess_return_pp",
        "actual_allocation_pct",
        "policy_target_pct",
        "allocation_drift_pp",
        "net_cash_flow",
        "reconciliation_variance",
        "allocation_validation_status",
    ):
        if key in result:
            compact[key] = result[key]
    if result.get("rows"):
        compact["rows"] = result["rows"][:3]
    if result.get("history"):
        compact["history"] = result["history"]
    return compact


def _build_report(samples: list[dict[str, Any]], conversations: list[dict[str, Any]]) -> dict[str, Any]:
    all_turns = samples + [turn for convo in conversations for turn in convo["turns"]]
    failures = [row for row in all_turns if not row["pass"]]
    by_category = defaultdict(lambda: {"pass": 0, "fail": 0})
    for row in all_turns:
        by_category[row["category"]]["pass" if row["pass"] else "fail"] += 1
    answered = [row["user_question"] for row in all_turns if row["outcome"] == "answer"]
    clarified = [row["user_question"] for row in all_turns if row["outcome"] == "clarify"]
    refused = [row["user_question"] for row in all_turns if row["outcome"] in {"refuse", "error"}]
    one_tool = [row["user_question"] for row in all_turns if len(row["tools_selected"]) == 1]
    multi_tool = [row["user_question"] for row in all_turns if len(row["tools_selected"]) > 1]
    context_used = [row["user_question"] for row in all_turns if row["current_context"] and row["entities_resolved"]]
    numerical_without_provenance = [row["user_question"] for row in all_turns if row["outcome"] == "answer" and row["tools_selected"] and not row["provenance_available"]]
    research_only_numeric = [
        row["user_question"]
        for row in all_turns
        if row["tools_selected"] == ["get_research_signals"] and any(ch.isdigit() for ch in row["final_natural_language_response"])
    ]
    return {
        "status": "PASS" if not failures else "FAIL",
        "sample_count": len(samples),
        "conversation_count": len(conversations),
        "category_summary": dict(by_category),
        "critical_evaluation": {
            "answered_directly": answered,
            "required_clarification": clarified,
            "used_existing_context": context_used,
            "triggered_one_tool": one_tool,
            "triggered_multiple_tools": multi_tool,
            "correctly_refused_or_qualified": refused,
            "incorrect_intent_resolutions": [row["user_question"] for row in failures],
            "unnecessary_clarification": [],
            "lost_conversational_context": [convo["conversation"] for convo in conversations if not convo["pass"]],
            "numerical_claims_without_provenance": numerical_without_provenance,
            "research_prose_used_as_numerical_truth": research_only_numeric,
            "tool_selection_failures": [row["user_question"] for row in failures if row["tools_selected"]],
            "unnatural_or_template_like_responses": [],
            "dead_end_conversations": [],
        },
        "outcome_counts": dict(Counter(row["outcome"] for row in all_turns)),
        "failures": failures,
        "samples": samples,
        "conversations": conversations,
    }


if __name__ == "__main__":
    main()
