from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beacon_data import AskBeaconConversation, ToolSelectingTestAdapter, build_model


CHECKPOINT_PATH = ROOT / "data" / "runtime" / "ask_beacon_checkpoints.sqlite"
REPORT_PATH = ROOT / "LANGGRAPH_NL_STRESS_TEST_REPORT.md"


JOURNEYS: list[dict[str, Any]] = [
    {
        "id": "J1",
        "context": {"fund": "BPT", "period": "FY2026"},
        "turns": [
            ("What was BPT's FY2026 return?", "fund_performance"),
            ("Compare with BLE.", "fund_comparison"),
            ("Relative to benchmark.", "fund_comparison"),
            ("What about Q3?", "fund_comparison"),
            ("Source?", "source_evidence"),
        ],
    },
    {
        "id": "J2",
        "context": {"fund": "BPT", "period": "FY2026"},
        "turns": [
            ("What should I investigate about BPT?", "research_signals"),
            ("Why?", "research_signals"),
            ("Show me the numbers.", "answer"),
            ("What about managers?", "manager_ranking"),
            ("The worst one?", "manager_ranking"),
            ("Has that worsened?", "quarterly_trend"),
            ("Source?", "source_evidence"),
        ],
    },
    {
        "id": "J3",
        "context": {"fund": "BPT", "period": "FY2026"},
        "turns": [
            ("Where is BPT off policy?", "allocation_drift"),
            ("What about cash?", "allocation_drift"),
            ("Has it worsened?", "allocation_history"),
            ("And BLE?", "allocation_drift"),
            ("Source?", "source_evidence"),
        ],
    },
    {
        "id": "J4",
        "context": {"period": "FY2026"},
        "turns": [
            ("Which fund performed best?", "clarification"),
            ("Relative to benchmark.", "fund_comparison"),
            ("Why?", "fund_comparison"),
        ],
    },
    {
        "id": "J5",
        "context": {"fund": "BPT", "period": "FY2026"},
        "turns": [
            ("bpt perf", "fund_performance"),
            ("and ble?", "fund_performance"),
            ("worst mgr q4", "manager_ranking"),
            ("why?", "manager_performance"),
            ("source?", "source_evidence"),
        ],
    },
]


def main() -> None:
    args = _parse_args()
    runner = EndpointRunner(args.endpoint) if args.endpoint else LocalRunner()
    run_id = uuid.uuid4().hex[:8]
    flows = []
    try:
        for journey in JOURNEYS:
            flows.append(_run_journey(runner, run_id, journey))
    finally:
        runner.close()
    report = _build_report(flows, args.endpoint)
    REPORT_PATH.write_text(_render_markdown(report), encoding="utf-8")
    print(json.dumps(_console_summary(report), indent=2))
    if report["status"] == "FAIL":
        raise SystemExit(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run high-value Ask Beacon conversational journey stress tests.")
    parser.add_argument(
        "--endpoint",
        help="Optional hosted Ask Beacon endpoint, e.g. https://your-app.vercel.app/api/ask-beacon. Omit for deterministic local tests.",
    )
    return parser.parse_args()


class LocalRunner:
    name = "deterministic-local"

    def __init__(self) -> None:
        model = build_model(ROOT / "Data", ROOT / ".tmp-agent-debug", ROOT / ".tmp-agent-debug" / "beacon.duckdb")
        self.conversation = AskBeaconConversation(ToolSelectingTestAdapter(), CHECKPOINT_PATH, model=model)

    def ask(self, thread_id: str, message: str, context: dict[str, Any] | None) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            return self.conversation.ask(thread_id, message, context)
        except Exception as exc:
            return {
                "ok": False,
                "answer": str(exc),
                "error": {"code": exc.__class__.__name__, "message": str(exc)},
                "turn_tool_events": [],
                "turn_messages": [],
                "turn_sources": [],
                "resolved_context": {},
                "validation_errors": [exc.__class__.__name__],
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            }

    def close(self) -> None:
        self.conversation.close()


class EndpointRunner:
    name = "hosted-endpoint"

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    def ask(self, thread_id: str, message: str, context: dict[str, Any] | None) -> dict[str, Any]:
        payload = json.dumps({"thread_id": thread_id, "message": message, "application_context": context or {}}).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                body = response.read().decode("utf-8")
                data = json.loads(body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            data = {"ok": False, "error": {"code": "http_error", "message": body, "status": exc.code}}
        except Exception as exc:
            data = {"ok": False, "error": {"code": "request_error", "message": str(exc)}}
        data.setdefault("elapsed_ms", round((time.perf_counter() - started) * 1000, 2))
        return data

    def close(self) -> None:
        return None


def _run_journey(runner: Any, run_id: str, journey: dict[str, Any]) -> dict[str, Any]:
    thread_id = f"nl_loop_{run_id}_{journey['id']}"
    rows = []
    previous: dict[str, Any] | None = None
    for index, (message, expected_type) in enumerate(journey["turns"], start=1):
        started = time.perf_counter()
        response = runner.ask(thread_id, message, journey["context"] if index == 1 else None)
        response.setdefault("elapsed_ms", round((time.perf_counter() - started) * 1000, 2))
        row = _summarize_turn(journey["id"], index, message, expected_type, response, previous)
        rows.append(row)
        previous = row
    return {
        "conversation": journey["id"],
        "thread_id": thread_id,
        "initial_context": journey["context"],
        "status": _status_for_rows(rows),
        "turns": rows,
    }


def _summarize_turn(
    journey_id: str,
    turn_number: int,
    message: str,
    expected_type: str,
    response: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    tool_events = response.get("turn_tool_events") or response.get("grounded_response", {}).get("activity_events") or []
    selected_tools = [event for event in tool_events if event.get("event") == "tool_selected"]
    completed_tools = [event for event in tool_events if event.get("event") == "tool_completed"]
    grounded = response.get("grounded_response") or {}
    response_type = grounded.get("response_type") or grounded.get("structured_response", {}).get("response_type")
    answer = str(grounded.get("answer") or response.get("answer") or response.get("error", {}).get("message") or "")
    resolved_context = response.get("resolved_context") or grounded.get("application_context") or response.get("application_context") or {}
    final_type = response_type or _end_state(answer, selected_tools, response)
    row = {
        "conversation": journey_id,
        "turn_number": turn_number,
        "user_message": message,
        "resolved_context": resolved_context,
        "response_type": response_type,
        "selected_tools": [event.get("tool") for event in selected_tools],
        "tool_arguments": [event.get("arguments") for event in selected_tools],
        "final_answer": answer,
        "followup_chips": grounded.get("followups") or [],
        "model_iteration_count": len([event for event in tool_events if event.get("event") == "model_completed"]),
        "elapsed_ms": response.get("elapsed_ms"),
        "end_state": final_type,
        "expected_response_type": expected_type,
        "validation_errors": response.get("validation_errors") or grounded.get("validation_errors") or [],
        "flags": [],
    }
    row["flags"] = _flags_for_turn(row, previous, completed_tools, response)
    row["status"] = _turn_status(row)
    return row


def _flags_for_turn(
    row: dict[str, Any],
    previous: dict[str, Any] | None,
    completed_tools: list[dict[str, Any]],
    response: dict[str, Any],
) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    message = row["user_message"].lower()
    answer = row["final_answer"].lower()
    expected = row["expected_response_type"]
    response_type = row["response_type"]

    if previous and row["selected_tools"] and previous["selected_tools"]:
        if row["selected_tools"] == previous["selected_tools"] and row["tool_arguments"] == previous["tool_arguments"]:
            flags.append(_fail("repeated_tool", "Identical tool and arguments repeated consecutively."))
    if previous and _is_clarification(row) and _is_clarification(previous) and row["final_answer"] == previous["final_answer"]:
        flags.append(_fail("repeated_clarification", "Identical clarification repeated."))
    if "maximum" in answer and "iteration" in answer or "max_iterations_reached" in str(row["validation_errors"]):
        flags.append(_fail("max_iterations", "Agent hit max iterations."))
    if previous and answer and answer == previous["final_answer"]:
        flags.append(_fail("repeated_answer", "Answer repeated without progress."))
    if _lost_known_context(row):
        flags.append(_fail("lost_context", "Known fund/period context was unexpectedly lost."))
    if "period specified is outside" in answer and _has_period_context(row):
        flags.append(_fail("period_validation_before_context", "Period validation appears to have run before applying context."))
    if "would you like me to retrieve" in answer or "would you like me to check" in answer:
        flags.append(_fail("retrieve_offer", "Agent offered to retrieve data instead of autonomously using tools."))
    if _tool_should_have_been_called(message, expected) and not row["selected_tools"] and expected != "clarification":
        flags.append(_fail("missing_tool", "Available tool should have been called autonomously."))
    if expected.startswith("allocation") and row["response_type"] != expected:
        flags.append(_fail("allocation_context_failure", f"Expected allocation flow {expected}, got {response_type or row['end_state']}."))
    if expected != "answer" and expected != row["response_type"] and not (expected == "clarification" and _is_clarification(row)):
        flags.append(_fail("wrong_response_type", f"Expected {expected}, got {response_type or row['end_state']}."))
    if "source" in message and row["selected_tools"] and row["selected_tools"][0] != "get_source_record":
        flags.append(_fail("source_started_analysis", "Source follow-up launched analysis instead of provenance lookup."))
    if _chip_repeats_action(row):
        flags.append(_warning("stale_chip", "Follow-up chip repeats the action just completed."))
    if "traceback" in answer or "exception" in answer or "unexpected token" in answer or "not valid json" in answer:
        flags.append(_fail("raw_error", "Raw model/tool/platform error reached the UI answer."))
    if _simple_factual(message) and row["model_iteration_count"] > 3:
        flags.append(_warning("excessive_iterations", "Simple factual request used excessive model iterations."))
    if any(event.get("ok") is False for event in completed_tools):
        flags.append(_fail("tool_error", "A selected tool completed with ok=false."))
    return flags


def _turn_status(row: dict[str, Any]) -> str:
    if any(flag["level"] == "FAIL" for flag in row["flags"]):
        return "FAIL"
    if any(flag["level"] == "WARNING" for flag in row["flags"]):
        return "WARNING"
    return "PASS"


def _status_for_rows(rows: list[dict[str, Any]]) -> str:
    if any(row["status"] == "FAIL" for row in rows):
        return "FAIL"
    if any(row["status"] == "WARNING" for row in rows):
        return "WARNING"
    return "PASS"


def _build_report(flows: list[dict[str, Any]], endpoint: str | None) -> dict[str, Any]:
    turns = [turn for flow in flows for turn in flow["turns"]]
    status = _status_for_rows(turns)
    return {
        "status": status,
        "mode": "hosted-endpoint" if endpoint else "deterministic-local",
        "endpoint": endpoint,
        "turn_count": len(turns),
        "flow_count": len(flows),
        "flows": flows,
        "summary": [
            {
                "status": turn["status"],
                "conversation": turn["conversation"],
                "turn_number": turn["turn_number"],
                "failure_reason": "; ".join(flag["reason"] for flag in turn["flags"]) or "",
                "resolved_context": turn["resolved_context"],
                "tool_selected": turn["selected_tools"],
                "response_type": turn["response_type"],
            }
            for turn in turns
            if turn["status"] != "PASS"
        ],
        "hosted_command": "python tools/run_langgraph_nl_stress_tests.py --endpoint https://YOUR-VERCEL-APP.vercel.app/api/ask-beacon",
    }


def _console_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "mode": report["mode"],
        "turn_count": report["turn_count"],
        "flow_count": report["flow_count"],
        "flagged_turns": report["summary"],
        "report_path": str(REPORT_PATH),
        "hosted_command": report["hosted_command"],
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# LangGraph Natural-Language Stress Test Report",
        "",
        f"Status: {report['status']}",
        f"Mode: {report['mode']}",
        f"Turns: {report['turn_count']}",
        "",
        "## Flagged Turns",
        "",
    ]
    if report["summary"]:
        lines.append("| Status | Flow | Turn | Failure reason | Tool | Response type |")
        lines.append("| --- | --- | ---: | --- | --- | --- |")
        for row in report["summary"]:
            lines.append(
                f"| {row['status']} | {row['conversation']} | {row['turn_number']} | "
                f"{_md(row['failure_reason'])} | {_md(', '.join(row['tool_selected']))} | {_md(row['response_type'] or '')} |"
            )
    else:
        lines.append("No failures or warnings.")
    lines.extend(
        [
            "",
            "## Hosted Endpoint Command",
            "",
            "```powershell",
            report["hosted_command"],
            "```",
            "",
            "## Full JSON",
            "",
            "```json",
            json.dumps(report, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _md(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _end_state(answer: str, selected_tools: list[dict[str, Any]], response: dict[str, Any]) -> str:
    if response.get("ok") is False:
        return "DATA_LIMITATION"
    if selected_tools:
        return "ANSWERED"
    if _looks_like_clarification(answer):
        return "CLARIFICATION"
    if "cannot" in answer.lower() or "can't" in answer.lower() or "not available" in answer.lower():
        return "DATA_LIMITATION"
    return "ANSWERED"


def _is_clarification(row: dict[str, Any]) -> bool:
    return row["response_type"] == "clarification" or _looks_like_clarification(row["final_answer"])


def _looks_like_clarification(answer: str) -> bool:
    text = str(answer or "").lower().strip()
    return text.endswith("?") and ("do you mean" in text or "which" in text or "should i" in text or "what" in text)


def _lost_known_context(row: dict[str, Any]) -> bool:
    text = row["user_message"].lower()
    context = row["resolved_context"]
    if any(term in text for term in ("ble", "bpt", "q3", "q4", "fy2026", "cash", "private equity")):
        return False
    if row["turn_number"] == 1:
        return False
    if row["selected_tools"] and not (context.get("period") or context.get("active_period")):
        return True
    return False


def _has_period_context(row: dict[str, Any]) -> bool:
    context = row["resolved_context"]
    return bool(context.get("period") or context.get("active_period"))


def _tool_should_have_been_called(message: str, expected: str) -> bool:
    if expected == "clarification":
        return False
    return any(
        term in message
        for term in (
            "return",
            "compare",
            "benchmark",
            "investigate",
            "numbers",
            "managers",
            "worst",
            "worsened",
            "source",
            "policy",
            "cash",
            "perf",
            "mgr",
        )
    )


def _chip_repeats_action(row: dict[str, Any]) -> bool:
    action = row["user_message"].strip().lower().rstrip(".?")
    chips = row.get("followup_chips") or []
    for chip in chips:
        label = str(chip.get("label") if isinstance(chip, dict) else chip).strip().lower().rstrip(".?")
        if label and label == action:
            return True
    return False


def _simple_factual(message: str) -> bool:
    text = message.lower()
    return "what was" in text and ("return" in text or "allocation" in text)


def _fail(code: str, reason: str) -> dict[str, str]:
    return {"level": "FAIL", "code": code, "reason": reason}


def _warning(code: str, reason: str) -> dict[str, str]:
    return {"level": "WARNING", "code": code, "reason": reason}


if __name__ == "__main__":
    main()
