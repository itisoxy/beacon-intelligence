from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beacon_data import AskBeaconConversation, ScriptedChatAdapter

CHECKPOINT_PATH = ROOT / "data" / "runtime" / "ask_beacon_checkpoints.sqlite"
REPORT_PATH = ROOT / "LANGGRAPH_THREAD_TEST_REPORT.md"


def main() -> None:
    conversation = AskBeaconConversation(ScriptedChatAdapter(), CHECKPOINT_PATH)
    suffix = uuid.uuid4().hex[:8]
    thread_id = f"thread_langgraph_demo_{suffix}_001"
    isolated_thread_id = f"thread_langgraph_demo_{suffix}_002"

    turns = [
        ("Who performed best?", {"fund": "BPT", "period": "FY2026"}),
        ("Relative to benchmark.", None),
        ("What about consistency?", None),
    ]
    responses = []
    for message, context in turns:
        responses.append(conversation.ask(thread_id, message, context))

    isolated = conversation.ask(isolated_thread_id, "What about consistency?", {"fund": "BLE", "period": "Q4"})

    same_thread_users = [message["content"] for message in responses[-1]["messages"] if message["role"] == "user"]
    isolated_users = [message["content"] for message in isolated["messages"] if message["role"] == "user"]
    checks = {
        "same_thread_has_three_user_turns": same_thread_users == [turn[0] for turn in turns],
        "same_thread_context_persisted": responses[-1]["application_context"] == {"fund": "BPT", "period": "FY2026"},
        "same_thread_followup_uses_history": "same BPT FY2026 manager question" in responses[-1]["answer"],
        "separate_thread_isolated": isolated_users == ["What about consistency?"] and "benchmark-relative" not in isolated["answer"],
        "checkpoint_created": CHECKPOINT_PATH.exists(),
    }
    passed = all(checks.values())
    payload = {
        "status": "PASS" if passed else "FAIL",
        "checkpoint_path": str(CHECKPOINT_PATH.relative_to(ROOT)),
        "thread_id": thread_id,
        "isolated_thread_id": isolated_thread_id,
        "checks": checks,
        "same_thread": [{"user": turns[index][0], "assistant": response["answer"]} for index, response in enumerate(responses)],
        "isolated_thread": {"user": "What about consistency?", "assistant": isolated["answer"]},
    }

    REPORT_PATH.write_text(
        "# LangGraph Thread Test Report\n\n"
        f"Status: {payload['status']}\n\n"
        "```json\n"
        f"{json.dumps(payload, indent=2)}\n"
        "```\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2))
    conversation.close()


if __name__ == "__main__":
    main()
