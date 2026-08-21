from __future__ import annotations

import operator
import os
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Annotated, Any, Protocol, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from .business_tools import BeaconBusinessTools
from .pipeline import build_model


DEFAULT_CHECKPOINT_PATH = Path("data/runtime/ask_beacon_checkpoints.sqlite")
DEFAULT_MODEL = "qwen3:1.7b"
MAX_AGENT_ITERATIONS = min(int(os.getenv("ASK_BEACON_MAX_AGENT_ITERATIONS", "5")), 5)
DEFAULT_DATA_DIR = Path("Data")
DEFAULT_OUTPUT_DIR = Path(".tmp-agent-debug")
DEFAULT_STORE_PATH = DEFAULT_OUTPUT_DIR / "beacon.duckdb"


def _merge_context(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(left or {})
    for key, value in (right or {}).items():
        if value is not None:
            merged[key] = value
    return merged


class AskBeaconState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    application_context: Annotated[dict[str, Any], _merge_context]
    resolved_context: Annotated[dict[str, Any], _merge_context]
    sources: Annotated[list[dict[str, Any]], operator.add]
    tool_events: Annotated[list[dict[str, Any]], operator.add]
    tool_results: Annotated[list[dict[str, Any]], operator.add]
    validation_errors: Annotated[list[str], operator.add]


class ProviderUnavailable(RuntimeError):
    pass


class AgentIterationLimitExceeded(RuntimeError):
    pass


class ChatModelAdapter(Protocol):
    provider_name: str
    model_name: str

    def invoke(self, messages: list[BaseMessage], tools: list[StructuredTool]) -> BaseMessage:
        ...


class OllamaChatAdapter:
    provider_name = "Ollama"

    def __init__(self, model_name: str | None = None, temperature: float = 0.0):
        self.model_name = model_name or os.getenv("AI_MODEL") or os.getenv("OLLAMA_MODEL", DEFAULT_MODEL)
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:
            raise ProviderUnavailable("langchain-ollama is not installed.") from exc
        model_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "reasoning": False,
            "temperature": temperature,
            "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            "client_kwargs": {"timeout": float(os.getenv("OLLAMA_TIMEOUT", "30"))},
        }
        if os.getenv("OLLAMA_NUM_PREDICT"):
            model_kwargs["num_predict"] = int(os.getenv("OLLAMA_NUM_PREDICT", "0"))
        self._model = ChatOllama(**model_kwargs)

    def invoke(self, messages: list[BaseMessage], tools: list[StructuredTool]) -> BaseMessage:
        try:
            return self._model.bind_tools(tools).invoke(messages)
        except Exception as exc:
            print(f"[OLLAMA ERROR]\ntype={exc.__class__.__name__}\nmessage={exc}", flush=True)
            raise


class ScriptedChatAdapter:
    provider_name = "scripted"
    model_name = "scripted-conversation-model"

    def __init__(self):
        self.calls = 0

    def invoke(self, messages: list[BaseMessage], tools: list[StructuredTool]) -> BaseMessage:
        del tools
        self.calls += 1
        human_messages = [message.content for message in messages if isinstance(message, HumanMessage)]
        latest = str(human_messages[-1]).strip().lower() if human_messages else ""
        prior = " ".join(str(message).lower() for message in human_messages[:-1])

        if "who performed best" in latest or "who was the best" in latest:
            return AIMessage(
                content=(
                    "For BPT in FY2026, do you mean highest absolute return, strongest "
                    "performance relative to benchmark, or most consistent outperformance?"
                )
            )
        if "relative to benchmark" in latest or "benchmark" in latest:
            return AIMessage(
                content=(
                    "Got it: benchmark-relative performance for BPT in FY2026. "
                    "I can answer once Beacon financial tools are connected."
                )
            )
        if "consistency" in latest:
            if "relative to benchmark" in prior or "benchmark-relative" in prior:
                return AIMessage(
                    content=(
                        "Now we're looking at the same BPT FY2026 manager question through "
                        "consistency. I can answer once Beacon financial tools are connected."
                    )
                )
            context_text = " ".join(str(message.content) for message in messages if isinstance(message, SystemMessage)).lower()
            fund = "BLE" if '"fund": "BLE"'.lower() in context_text else "BPT"
            period = "Q4" if '"period": "Q4"'.lower() in context_text else "FY2026"
            return AIMessage(
                content=(
                    f"Do you want manager consistency for {fund} in {period}, or a different fund "
                    "or period?"
                )
            )
        return AIMessage(content="I can discuss that conversationally, but portfolio tools are not connected yet.")


class ToolSelectingTestAdapter:
    provider_name = "scripted"
    model_name = "scripted-tool-selecting-model"

    def __init__(self):
        self.calls = 0

    def invoke(self, messages: list[BaseMessage], tools: list[StructuredTool]) -> BaseMessage:
        del tools
        self.calls += 1
        if isinstance(messages[-1], ToolMessage):
            result = json.loads(str(messages[-1].content))
            return AIMessage(content=_safe_answer_from_tool_result(result, messages))

        human_messages = [str(message.content) for message in messages if isinstance(message, HumanMessage)]
        latest = human_messages[-1].lower() if human_messages else ""
        prior = " ".join(message.lower() for message in human_messages[:-1])
        context = _conversation_context_from_messages(messages)
        intent = _interpret_natural_language(latest, prior, context, messages)
        if intent["type"] == "tool":
            return _tool_call_message(intent["tool"], intent["arguments"])
        if intent["type"] == "answer":
            return AIMessage(content=intent["answer"])
        if intent["type"] == "clarify":
            return AIMessage(content=intent["question"])
        return AIMessage(content="I need a Beacon financial tool for that question, but no matching tool was selected.")


def _interpret_natural_language(text: str, prior: str, context: dict[str, Any], messages: list[BaseMessage]) -> dict[str, Any]:
    explicit_fund = _fund_from_text(text)
    explicit_period = _period_from_text(text)
    explicit_asset_class = _asset_from_text(text)
    context_fund = context.get("active_fund") or context.get("fund")
    context_period = context.get("active_period") or context.get("period")
    context_asset_class = context.get("active_asset_class") or context.get("asset_class")
    is_comparison_followup = _is_comparison_followup(text, explicit_fund, context)
    fund = context_fund if is_comparison_followup else (explicit_fund or context_fund)
    period = explicit_period or context_period or "FY2026"
    asset_class = explicit_asset_class or context_asset_class
    recent_context = _recent_conversation_context(messages, context)
    recent_fund = recent_context.get("active_fund") or recent_context.get("fund")
    recent_period = recent_context.get("active_period") or recent_context.get("period")
    recent_asset_class = recent_context.get("active_asset_class") or recent_context.get("asset_class")
    fund = fund or recent_fund
    period = explicit_period or recent_period or period
    asset_class = asset_class or recent_asset_class
    previous_manager = _latest_manager_from_tool_messages(messages)
    previous_asset = _latest_asset_from_tool_messages(messages) or asset_class
    comparison_fund = recent_context.get("comparison_fund") or context.get("comparison_fund")
    pending_clarification = recent_context.get("pending_clarification") or context.get("pending_clarification")

    if pending_clarification:
        resolved = _resolve_pending_clarification(text, pending_clarification, fund, period, previous_manager, previous_asset, recent_context)
        if resolved:
            return resolved

    if _has_any(text, "q8", "q5"):
        return {"type": "tool", "tool": "get_fund_performance", "arguments": {"fund": fund or "BPT", "period": "Q8" if "q8" in text else "Q5"}}
    if _has_any(text, "2023", "fy2024", "tomorrow"):
        return {"type": "answer", "answer": "The supplied Beacon dataset only supports FY2026 portfolio periods, so I can't answer that from the available data."}
    if "fund xyz" in text or "manager that isn't" in text or "isnt in the data" in text:
        return {"type": "answer", "answer": "I can't find that entity in the supplied Beacon dataset. I can analyze BPT, BLE, or managers present in the FY2026 records."}
    if "crypto" in text:
        return {"type": "answer", "answer": "Crypto is not an asset class in the normalized Beacon dataset, so I can't show a Crypto allocation."}
    if _has_any(text, "change strategy", "what were they thinking", "markets fall", "sell those stocks", "next year", "should we fire", "holdings caused", "investment committee", "why exactly"):
        return {"type": "answer", "answer": "The supplied Beacon data cannot establish that conclusion. I can show sourced performance, benchmark-relative results, allocation movement, cash flows, or provenance instead."}

    if _has_any(text, "source", "evidence", "where did that come from", "where did this come from"):
        record_id = _latest_record_id(messages, recent_context)
        if record_id:
            return {"type": "tool", "tool": "get_source_record", "arguments": {"record_id": record_id}}
        return {"type": "clarify", "question": "Which result should I source: the latest performance result, allocation result, manager result, or research signal?"}

    if _is_research_signal_followup(text):
        signal_id = _latest_research_signal_id(messages, recent_context)
        if signal_id:
            return {"type": "tool", "tool": "get_research_signals", "arguments": {"fund": fund, "period": period, "asset_class": asset_class, "signal_id": signal_id}}
        if recent_context.get("last_response_type") == "fund_comparison":
            return {"type": "tool", "tool": "compare_funds", "arguments": {"metric": recent_context.get("metric") or "fund_excess_return_pp", "period": period}}
        if "signal" in text:
            return {"type": "clarify", "question": "Which research signal should I explain?"}
        if previous_manager:
            return {"type": "tool", "tool": "get_manager_performance", "arguments": previous_manager}
        if previous_asset:
            return {"type": "tool", "tool": "get_allocation_history", "arguments": {"fund": fund or "BPT", "asset_class": previous_asset}}
        return {"type": "answer", "answer": "The prior Beacon result is worth attention because it identifies a sourced area for follow-up, but the dataset does not establish a causal explanation on its own."}

    if _has_any(text, "what about managers", "managers?"):
        return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": fund, "period": period, "asset_class": asset_class, "metric": "excess_return", "direction": "ascending", "limit": 5}}

    if explicit_fund and _is_fund_only_request(text, explicit_fund):
        active_dimension = recent_context.get("active_dimension")
        if active_dimension == "allocation" and (asset_class or previous_asset):
            return {"type": "tool", "tool": "get_asset_allocation", "arguments": {"fund": explicit_fund, "period": period, "asset_class": asset_class or previous_asset}}
        if active_dimension == "manager":
            return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": explicit_fund, "period": period, "metric": _latest_rank_metric(messages) or "excess_return", "direction": "ascending", "limit": 5}}
        if active_dimension == "research":
            return {"type": "tool", "tool": "get_research_signals", "arguments": {"fund": explicit_fund, "period": period, "asset_class": asset_class}}
        return {"type": "tool", "tool": "get_fund_performance", "arguments": {"fund": explicit_fund, "period": period}}

    if _has_any(text, "mgr", "manager") and _has_any(text, "worst", "weakest", "underperform", "q4"):
        return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": fund, "period": explicit_period or period or "Q4", "metric": "excess_return", "direction": "ascending", "limit": 1}}

    if explicit_period and comparison_fund and recent_context.get("last_response_type") == "fund_comparison":
        return {"type": "tool", "tool": "compare_funds", "arguments": {"metric": recent_context.get("metric") or "fund_excess_return_pp", "period": explicit_period}}

    if explicit_period and _is_period_followup(text):
        return {"type": "tool", "tool": "get_fund_performance", "arguments": {"fund": fund or "BPT", "period": explicit_period}}

    if _has_any(text, "relative to benchmark", "against benchmark") and comparison_fund:
        return {"type": "tool", "tool": "rank_funds", "arguments": {"period": period, "metric": "fund_excess_return_pp", "direction": "descending"}}

    if _has_any(text, "which fund performed best", "which fund did best", "which fund was best"):
        return {"type": "clarify", "question": "Do you mean highest absolute return or strongest return relative to benchmark?"}

    if _has_any(text, "absolute return") and _has_any(prior, "which fund performed best", "which fund did best", "which fund was best"):
        return {"type": "tool", "tool": "rank_funds", "arguments": {"period": period, "metric": "fund_return_pct", "direction": "descending"}}

    if _has_any(text, "relative to benchmark", "against benchmark") and _has_any(prior, "which fund performed best", "which fund did best", "which fund was best"):
        return {"type": "tool", "tool": "rank_funds", "arguments": {"period": period, "metric": "fund_excess_return_pp", "direction": "descending"}}

    if asset_class and recent_context.get("active_dimension") == "allocation" and _has_any(text, "what about", "and "):
        return {"type": "tool", "tool": "get_asset_allocation", "arguments": {"fund": fund or "BPT", "period": period, "asset_class": asset_class}}

    if _has_any(text, "asset allocation trend"):
        if asset_class or previous_asset:
            return {"type": "tool", "tool": "get_allocation_history", "arguments": {"fund": fund or "BPT", "asset_class": asset_class or previous_asset}}
        return {"type": "clarify", "question": "Which asset allocation trend should I show: Cash, Private Equity, Public Equity, Fixed Income, Real Assets, or Hedge Funds?"}

    if _has_any(text, "manager performance trend"):
        if previous_manager:
            return {"type": "tool", "tool": "get_manager_history", "arguments": {"manager": previous_manager["manager"], "fund": previous_manager.get("fund") or fund}}
        return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": fund, "period": period, "metric": "excess_return", "direction": "ascending", "limit": 5}}

    if _has_any(text, "fund return trend"):
        return {"type": "tool", "tool": "get_fund_performance", "arguments": {"fund": fund or "BPT", "period": period}}

    if asset_class and _has_any(prior, "which allocation should i compare", "which quarterly trend", "allocation trend", "compare allocation"):
        if _has_any(prior, "compare") and not _has_any(text, "trend", "quarterly"):
            return {"type": "tool", "tool": "compare_funds", "arguments": {"metric": "allocation_drift_pp", "period": period, "asset_class": asset_class}}
        return {"type": "tool", "tool": "get_allocation_history", "arguments": {"fund": fund or "BPT", "asset_class": asset_class}}

    if _has_any(text, "compare allocation", "compare allocations", "compare allocation drift", "compare policy"):
        if asset_class or previous_asset:
            return {
                "type": "tool",
                "tool": "compare_funds",
                "arguments": {"metric": "allocation_drift_pp", "period": period, "asset_class": asset_class or previous_asset},
            }
        if fund:
            return {"type": "tool", "tool": "rank_asset_allocations", "arguments": {"fund": fund, "period": period, "direction": "largest_absolute", "limit": 5}}
        return {"type": "clarify", "question": "Which allocation should I compare: Cash, Private Equity, Public Equity, Fixed Income, Real Assets, or Hedge Funds?"}

    if _has_any(text, "show quarterly trend", "quarterly trend", "show trend", "show quarterly"):
        if previous_manager:
            return {"type": "tool", "tool": "get_manager_history", "arguments": {"manager": previous_manager["manager"], "fund": previous_manager.get("fund") or fund}}
        if previous_asset and recent_context.get("active_dimension") == "allocation":
            return {"type": "tool", "tool": "get_allocation_history", "arguments": {"fund": fund or "BPT", "asset_class": previous_asset}}
        last_response_type = recent_context.get("last_response_type")
        active_dimension = recent_context.get("active_dimension")
        if last_response_type == "fund_performance" or active_dimension == "performance":
            return {
                "type": "tool",
                "tool": "compare_periods",
                "arguments": {"entity": fund or "BPT", "metric": "fund_return_pct", "period_a": "Q1", "period_b": "Q4", "fund": fund or "BPT"},
            }
        if last_response_type in {"manager_ranking", "manager_performance", "quarterly_trend"} or active_dimension == "manager":
            if previous_manager:
                return {"type": "tool", "tool": "get_manager_history", "arguments": {"manager": previous_manager["manager"], "fund": previous_manager.get("fund") or fund}}
            return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": fund, "period": period, "metric": "excess_return", "direction": "ascending", "limit": 5}}
        if last_response_type in {"allocation_drift", "allocation_history"} or active_dimension == "allocation":
            if asset_class or previous_asset:
                return {"type": "tool", "tool": "get_allocation_history", "arguments": {"fund": fund or "BPT", "asset_class": asset_class or previous_asset}}
            return {"type": "tool", "tool": "rank_asset_allocations", "arguments": {"fund": fund or "BPT", "period": period, "direction": "largest_absolute", "limit": 5}}
        if asset_class or previous_asset:
            return {"type": "tool", "tool": "get_allocation_history", "arguments": {"fund": fund or "BPT", "asset_class": asset_class or previous_asset}}
        if previous_manager:
            return {"type": "tool", "tool": "get_manager_history", "arguments": {"manager": previous_manager["manager"], "fund": previous_manager.get("fund") or fund}}
        return {"type": "clarify", "question": "For quarterly performance, do you mean fund returns, manager performance, or asset allocation?"}

    if _is_fund_followup(text, "BLE"):
        if previous_asset:
            return {"type": "tool", "tool": "get_asset_allocation", "arguments": {"fund": "BLE", "period": period, "asset_class": previous_asset}}
        return {"type": "tool", "tool": "get_fund_performance", "arguments": {"fund": "BLE", "period": period}}

    if _is_fund_followup(text, "BPT"):
        if previous_asset:
            return {"type": "tool", "tool": "get_asset_allocation", "arguments": {"fund": "BPT", "period": period, "asset_class": previous_asset}}
        return {"type": "tool", "tool": "get_fund_performance", "arguments": {"fund": "BPT", "period": period}}

    if _has_any(text, "has that worsened", "has it worsened", "has this worsened", "has that got worse", "has it got worse"):
        if previous_manager:
            return {"type": "tool", "tool": "get_manager_history", "arguments": {"manager": previous_manager["manager"], "fund": previous_manager.get("fund") or fund}}
        if previous_asset:
            return {"type": "tool", "tool": "get_allocation_history", "arguments": {"fund": fund or recent_context.get("fund") or "BPT", "asset_class": previous_asset}}
        return {"type": "clarify", "question": "Do you mean the allocation drift, manager result, or fund performance?"}

    if _has_any(text, "the worst one", "worst one", "weakest one", "lowest one"):
        if recent_context.get("active_dimension") == "manager" or previous_manager:
            return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": fund, "period": period, "asset_class": asset_class, "metric": _latest_rank_metric(messages) or "excess_return", "direction": "ascending", "limit": 1}}
        if recent_context.get("active_dimension") == "allocation" or previous_asset:
            return {"type": "tool", "tool": "rank_asset_allocations", "arguments": {"fund": fund or "BPT", "period": period, "direction": "largest_absolute", "limit": 1}}
        return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": fund, "period": period, "asset_class": asset_class, "metric": _latest_rank_metric(messages) or "excess_return", "direction": "ascending", "limit": 1}}

    if _has_any(text, "anything else", "what else", "anything more"):
        return {"type": "tool", "tool": "get_research_signals", "arguments": {"fund": fund, "period": period, "asset_class": asset_class}}

    if ("relative to benchmark" in text or "against benchmark" in text) and _has_any(prior, "who performed best", "who was the best", "who did best", "strongest", "top"):
        return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": fund, "period": period, "metric": "excess_return", "direction": "descending", "limit": 1}}
    if "absolute return" in text and _has_any(prior, "who performed best", "who was the best", "who did best", "strongest", "top"):
        return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": fund, "period": period, "metric": "absolute_return", "direction": "descending", "limit": 1}}
    if _has_any(text, "consistent", "consistency"):
        previous_manager = _latest_manager_from_tool_messages(messages)
        if previous_manager:
            return {"type": "tool", "tool": "get_manager_performance", "arguments": previous_manager}

    if _has_any(text, "where did those numbers come from", "where did that number come from"):
        record_id = _latest_record_id(messages, context)
        if record_id:
            return {"type": "tool", "tool": "get_source_record", "arguments": {"record_id": record_id}}
        return {"type": "clarify", "question": "Which result should I source: the latest performance result, allocation result, manager result, or research signal?"}
    if _has_any(text, "where are you getting", "show me the source", "which workbook", "where exactly", "how do you know", "prove that", "what did you use"):
        record_id = _latest_record_id(messages, context)
        if record_id:
            return {"type": "tool", "tool": "get_source_record", "arguments": {"record_id": record_id}}
        return {"type": "clarify", "question": "Which result should I source: the latest performance result, allocation result, manager result, or research signal?"}
    if _has_any(text, "reconciled", "data clean", "trust that number", "can i trust"):
        return {"type": "tool", "tool": "validate_reconciliation", "arguments": {"fund": fund or "BPT", "period": _quarter_for_validation(period)}}

    if _has_any(text, "what should", "worry", "cio", "interesting", "hiding", "weird", "stands out", "three things", "where would you start", "worth digging", "missing", "should i care", "biggest issue"):
        if _has_any(text, "which one") and _has_any(prior, "compare", "ble", "other fund", "both"):
            return {"type": "tool", "tool": "compare_funds", "arguments": {"metric": "allocation_drift_pp", "period": period, "asset_class": asset_class}}
        return {"type": "tool", "tool": "get_research_signals", "arguments": {"fund": fund, "period": period, "asset_class": asset_class}}

    if _has_any(text, "money go", "money come", "go out", "came out", "flows", "aum change", "fund value", "opening number", "closing number", "driving the change", "make", "made", "making"):
        if "who" in text or "manager" in text:
            return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": fund, "period": period, "metric": "absolute_return", "direction": "descending", "limit": 1}}
        return {"type": "tool", "tool": "get_cash_flows", "arguments": {"fund": fund or "BPT", "period": period}}

    if _has_any(text, "weaker", "weaker than", "stronger", "stronger than", "worse than", "better than") and _has_any(text, "bpt", "ble", "other fund", "fund"):
        metric = "fund_excess_return_pp" if _has_any(text, "benchmark", "relative", "excess") else "fund_return_pct"
        return {"type": "tool", "tool": "compare_funds", "arguments": {"metric": metric, "period": period}}

    if _has_any(text, "other fund", "with ble", "with bpt", "compare the two", "compare that", "ble any better", "what about bpt", "both", "pension doing", "bigger problem", "closer to target"):
        if asset_class:
            return {"type": "tool", "tool": "compare_funds", "arguments": {"metric": "allocation_drift_pp", "period": period, "asset_class": asset_class}}
        metric = "fund_excess_return_pp" if _has_any(text, "benchmark", "relative", "excess") else "fund_return_pct"
        return {"type": "tool", "tool": "compare_funds", "arguments": {"metric": metric, "period": period}}

    if _has_any(text, "changed", "recently", "middle of the year", "second half", "towards the end", "building all year", "last quarter", "different now", "q4 worse", "improve", "start moving", "last six months", "recent thing", "got worse"):
        if previous_manager and ("q4" in text or "they" in text):
            previous_manager["period"] = "Q4" if "q4" in text else previous_manager["period"]
            return {"type": "tool", "tool": "get_manager_performance", "arguments": previous_manager}
        if asset_class or "cash" in text:
            return {"type": "tool", "tool": "get_allocation_history", "arguments": {"fund": fund or "BPT", "asset_class": asset_class or "Cash"}}
        return {"type": "tool", "tool": "get_fund_performance", "arguments": {"fund": fund or "BPT", "period": "H2 FY2026" if _has_any(text, "second half", "last six months") else period}}

    if _has_any(text, "heavy", "light", "off target", "away from policy", "furthest", "biggest gap", "drifting", "out of line", "too high", "too low", "allocation", "policy", "supposed to be", "problem", "bad", "high no") or (asset_class and "looking" in text):
        if asset_class or "cash" in text or "pe" in text:
            return {"type": "tool", "tool": "get_asset_allocation", "arguments": {"fund": fund or "BPT", "period": period, "asset_class": asset_class or "Cash"}}
        direction = "overweight" if _has_any(text, "heavy", "too high") else "underweight" if _has_any(text, "light", "too low") else "largest_absolute"
        return {"type": "tool", "tool": "rank_asset_allocations", "arguments": {"fund": fund or "BPT", "period": period, "direction": direction, "limit": 3}}

    if asset_class and _has_any(text, "performance", "perform", "how did"):
        return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": fund, "period": period, "metric": "excess_return", "direction": "descending", "limit": 1}}

    if ("manager" in text and _has_any(text, "underperformed", "underperform", "benchmark", "q4")) or _has_any(text, "who beat their number", "who fell behind", "kept missing", "consistently good", "bad all year", "kept underperforming", "suddenly dropped", "suddenly go wrong", "which one would you flag"):
        metric = "consistency" if _has_any(text, "consistently", "kept", "all year") else "excess_return"
        direction = "descending" if _has_any(text, "beat", "good") else "ascending"
        return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": fund, "period": period, "metric": metric, "direction": direction, "limit": 1}}

    if _has_any(text, "against the benchmark", "beat the market", "ahead", "behind", "gap", "outperform", "relative to benchmark", "beat it", "beat target", "bit actually did well", "dragged us down"):
        if "who" in text or "manager" in text or _has_any(text, "bit", "dragged"):
            direction = "ascending" if _has_any(text, "behind", "dragged", "worst", "underperform", "fell") else "descending"
            return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": fund, "period": period, "metric": "excess_return", "direction": direction, "limit": 1}}
        return {"type": "tool", "tool": "get_fund_performance", "arguments": {"fund": fund or "BPT", "period": period}}

    if _has_any(text, "second best"):
        return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": fund, "period": period, "metric": _latest_rank_metric(messages) or "excess_return", "direction": "descending", "limit": 2}}

    if _has_any(text, "who performed best", "who was the best", "who did best", "who was strongest", "came out on top", "best year", "whos best", "which managers actually did alright"):
        if _has_any(text, "made us money"):
            return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": fund, "period": period, "metric": "absolute_return", "direction": "descending", "limit": 1}}
        fund_label = fund or "the selected fund"
        period_label = period or "the selected period"
        return {
            "type": "clarify",
            "question": f"For {fund_label} in {period_label}, do you mean highest absolute return, strongest performance relative to benchmark, or most consistent outperformance?",
        }
    if _has_any(text, "consistent", "consistency"):
        return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": fund, "period": period, "metric": "consistency", "direction": "descending", "limit": 1}}

    if _has_any(text, "how are we doing", "actually finish", "did we do alright", "number looking", "portfolio", "return", "perf", "look off", "anything look off"):
        if not fund:
            return {"type": "clarify", "question": "Which fund should I use, BPT or BLE?"}
        return {"type": "tool", "tool": "get_fund_performance", "arguments": {"fund": fund, "period": period}}

    if _has_any(text, "why does that matter"):
        return {"type": "answer", "answer": "It matters because the previous Beacon result identifies a sourced area for follow-up review, but the data alone does not establish causality."}
    if _has_any(text, "show me the numbers"):
        if previous_asset:
            return {"type": "tool", "tool": "get_asset_allocation", "arguments": {"fund": fund or "BPT", "period": period, "asset_class": previous_asset}}
        return {"type": "tool", "tool": "get_fund_performance", "arguments": {"fund": fund or "BPT", "period": period}}

    if _has_any(text, "how did they do", "did that improve", "what about this one", "was it worse before", "how far off was it", "what happened to them", "normal", "changed there", "dig into that"):
        if previous_manager:
            return {"type": "tool", "tool": "get_manager_performance", "arguments": previous_manager}
        if asset_class:
            return {"type": "tool", "tool": "get_allocation_history", "arguments": {"fund": fund or "BPT", "asset_class": asset_class}}
        return {"type": "clarify", "question": "What should I look at: a fund, asset class, manager, or previous signal?"}

    if _has_any(text, "what happened q4"):
        return {"type": "clarify", "question": "For Q4, should I review fund performance, allocation versus policy, manager results, or cash flows?"}

    if _has_any(text, "best one", "which is better", "how did it perform", "biggest change", "how far off are we", "was this good", "did they do badly", "how much did it move", "which one matters", "what was the return"):
        return {"type": "clarify", "question": "Which fund, asset class, or manager should I use?"}

    return {"type": "clarify", "question": "Can you clarify the specific Beacon item you mean?"}


def _is_research_signal_followup(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if normalized in {
        "why",
        "why?",
        "why is that",
        "why is that?",
        "why does that matter",
        "why does that matter?",
        "why does this matter",
        "why does this matter?",
    }:
        return True
    return _has_any(
        normalized,
        "explain the top signal",
        "explain top signal",
        "explain that",
        "explain this",
        "tell me more about it",
        "tell me more about that",
        "show me the numbers",
        "show the numbers",
        "what should i check next",
        "what should i look at next",
        "has this worsened",
        "has that worsened",
    )


class BeaconToolAdapter:
    def __init__(self, model: dict[str, Any]):
        self.tools = BeaconBusinessTools(model)

    def get_fund_performance(self, fund: str, period: str) -> dict[str, Any]:
        """Retrieve canonical fund-level return, policy benchmark, excess return, AUM, cash flow and gain/loss for a fund and period. Use for fund performance or benchmark-relative performance questions."""
        result = self.tools.get_fund_summary(fund, period)
        if not result.get("ok"):
            return result
        metrics = result["metrics"]
        return _clean_result(
            "get_fund_performance",
            result["arguments"],
            {
                "fund": fund,
                "period": period,
                "ending_aum": _metric_payload(metrics["aum"]),
                "fund_return_pct": _metric_payload(metrics["return"]),
                "policy_benchmark_return_pct": _metric_payload(metrics["policy_benchmark"]),
                "excess_return_pp": _metric_payload(metrics["excess_return"]),
                "net_cash_flow": _metric_payload(metrics["net_cash_flow"]),
                "investment_gain_loss": _metric_payload(metrics["gain_loss"]),
            },
        )

    def get_asset_allocation(self, fund: str, period: str, asset_class: str) -> dict[str, Any]:
        """Retrieve actual versus policy allocation, drift and dollar variance for one fund, period and asset class. Use for allocation, overweight/underweight or policy-deviation questions."""
        result = self.tools.get_asset_allocation(fund, period, asset_class)
        if not result.get("ok"):
            return result
        metrics = result["metrics"]
        return _clean_result(
            "get_asset_allocation",
            result["arguments"],
            {
                "fund": fund,
                "period": period,
                "asset_class": asset_class,
                "market_value": _metric_payload(metrics["market_value"]),
                "actual_allocation_pct": _metric_payload(metrics["actual_allocation"]),
                "policy_target_pct": _metric_payload(metrics["policy_target"]),
                "allocation_drift_pp": _metric_payload(metrics["drift_pp"]),
                "dollar_variance_to_policy": _metric_payload(metrics["dollar_variance"]),
                "allocation_validation_status": _metric_payload(metrics["allocation_validation"]),
                "status": result.get("status"),
            },
        )

    def get_allocation_history(self, fund: str, asset_class: str) -> dict[str, Any]:
        """Retrieve Q1-Q4 allocation movement for a fund and asset class. Use to determine whether a position or drift is increasing, decreasing, persistent or worsening."""
        result = self.tools.get_allocation_history(fund, asset_class)
        if not result.get("ok"):
            return result
        return _clean_result(
            "get_allocation_history",
            result["arguments"],
            {
                "fund": fund,
                "asset_class": asset_class,
                "history": [
                    {
                        "period": row["period"],
                        "actual_allocation_pct": _metric_payload(row["actual_allocation"]),
                        "policy_target_pct": _metric_payload(row["policy_target"]),
                        "allocation_drift_pp": _metric_payload(row["drift_pp"]),
                    }
                    for row in result["rows"]
                ],
            },
        )

    def rank_asset_allocations(self, fund: str, period: str, direction: str = "largest_absolute", limit: int = 5) -> dict[str, Any]:
        """Rank asset classes by canonical allocation drift. Use for largest overweight, underweight, policy gap, allocation concern or where-off-policy questions."""
        if direction == "overweight":
            sort_direction = "descending"
            key = lambda row: float(row["metrics"]["drift_pp"]["value"] or 0)
        elif direction == "underweight":
            sort_direction = "ascending"
            key = lambda row: float(row["metrics"]["drift_pp"]["value"] or 0)
        else:
            sort_direction = "largest_absolute"
            key = lambda row: abs(float(row["metrics"]["drift_pp"]["value"] or 0))
        rows = []
        for asset_class in self.tools.model["dimensions"]["asset_classes"]:
            result = self.tools.get_asset_allocation(fund, period, asset_class)
            if result.get("ok"):
                metrics = result["metrics"]
                rows.append(
                    {
                        "fund": fund,
                        "period": period,
                        "asset_class": asset_class,
                        "status": result.get("status"),
                        "metrics": {
                            "actual_allocation_pct": _metric_payload(metrics["actual_allocation"]),
                            "policy_target_pct": _metric_payload(metrics["policy_target"]),
                            "drift_pp": _metric_payload(metrics["drift_pp"]),
                            "dollar_variance": _metric_payload(metrics["dollar_variance"]),
                        },
                    }
                )
        if direction == "underweight":
            rows = [row for row in rows if float(row["metrics"]["drift_pp"]["value"] or 0) < 0]
        if direction == "overweight":
            rows = [row for row in rows if float(row["metrics"]["drift_pp"]["value"] or 0) > 0]
        rows = sorted(rows, key=key, reverse=sort_direction != "ascending")[: int(limit)]
        return _clean_result(
            "rank_asset_allocations",
            {"fund": fund, "period": period, "direction": direction, "limit": limit},
            {"fund": fund, "period": period, "direction": direction, "rows": rows},
        )

    def rank_funds(self, period: str, metric: str = "fund_return_pct", direction: str = "descending") -> dict[str, Any]:
        """Rank BPT and BLE using canonical fund metrics. Use when the user asks which fund performed better, strongest/weakest fund, absolute return, benchmark-relative return or AUM ranking."""
        metric = _clean_optional_text(metric) or "fund_return_pct"
        period = _clean_optional_text(period) or "FY2026"
        direction = _clean_optional_text(direction) or "descending"
        metric_aliases = {
            "absolute_return": "fund_return_pct",
            "absolute return": "fund_return_pct",
            "return": "fund_return_pct",
            "fund_return": "fund_return_pct",
            "fund_return_pct": "fund_return_pct",
            "benchmark_relative": "fund_excess_return_pp",
            "benchmark relative": "fund_excess_return_pp",
            "benchmark-relative": "fund_excess_return_pp",
            "benchmark relative return": "fund_excess_return_pp",
            "benchmark-relative return": "fund_excess_return_pp",
            "excess_return": "fund_excess_return_pp",
            "excess return": "fund_excess_return_pp",
            "fund_excess_return_pp": "fund_excess_return_pp",
            "aum": "ending_aum",
            "more aum": "ending_aum",
            "assets": "ending_aum",
            "ending_aum": "ending_aum",
        }
        metric_id = metric_aliases.get(metric, metric)
        result = self.tools.compare_funds(metric=metric_id, period=period)
        if not result.get("ok"):
            return result
        reverse = direction not in {"ascending", "asc", "lowest"}
        rows = sorted(result["rows"], key=lambda row: float(row["metric"]["value"] or 0), reverse=reverse)
        return _clean_result(
            "rank_funds",
            {"period": period, "metric": metric, "direction": direction},
            {
                "period": period,
                "metric": metric_id,
                "direction": direction,
                "rows": [{"rank": index + 1, "fund": row["fund"], "metric": _metric_payload(row["metric"])} for index, row in enumerate(rows)],
            },
        )

    def get_manager_performance(
        self,
        manager: str | None = None,
        fund: str | None = None,
        period: str | None = None,
        asset_class: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve canonical manager return, benchmark, excess return and consistency rows. Use for manager performance details after a manager or manager set is known."""
        manager = _clean_optional_text(manager)
        fund = _clean_optional_text(fund)
        period = _clean_optional_text(period)
        asset_class = _clean_optional_text(asset_class)
        result = self.tools.get_manager_performance(manager=manager, fund=fund, period=period, asset_class=asset_class)
        if not result.get("ok"):
            return result
        return _clean_result("get_manager_performance", result["arguments"], {"rows": [_manager_payload(row) for row in result["rows"]]})

    def rank_managers(
        self,
        fund: str | None = None,
        period: str | None = None,
        metric: str = "excess_return",
        direction: str = "descending",
        limit: int = 5,
    ) -> dict[str, Any]:
        """Rank managers using canonical performance and benchmark-relative metrics. Use for strongest/weakest manager, who struggled, who outperformed, consistency or manager-ranking questions."""
        fund = _clean_optional_text(fund)
        period = _clean_optional_text(period) or "FY2026"
        metric = _clean_optional_text(metric) or "excess_return"
        direction = _clean_optional_text(direction) or "descending"
        metric_alias = {
            "excess_return": "excess return",
            "excess return": "excess return",
            "benchmark-relative return": "excess return",
            "benchmark relative return": "excess return",
            "relative return": "excess return",
            "absolute_return": "absolute return",
            "absolute return": "absolute return",
        }.get(metric, metric)
        direction_alias = {"ascending": "asc", "descending": "desc"}.get(direction, direction)
        result = self.tools.rank_managers(period=period, metric=metric_alias, direction=direction_alias, fund=fund, limit=limit)
        if not result.get("ok"):
            return result
        return _clean_result(
            "rank_managers",
            {"fund": fund, "period": period, "metric": metric, "direction": direction, "limit": limit},
            {
                "period": period,
                "metric": metric,
                "direction": direction,
                "rows": [
                    {
                        "rank": row["rank"],
                        "fund": row["fund"],
                        "asset_class": row["asset_class"],
                        "manager": row["manager"],
                        "metric": _metric_payload(row["metric"]),
                        **_manager_detail_payload(self.tools, row["manager"], row["fund"], period, row["asset_class"]),
                    }
                    for row in result["rows"]
                ],
            },
        )

    def get_manager_history(self, manager: str, fund: str | None = None) -> dict[str, Any]:
        """Retrieve manager performance across Q1-Q4. Use for consistency, deterioration, improvement or trend analysis for a known manager."""
        result = self.tools.get_manager_history(manager, fund=fund)
        if not result.get("ok"):
            return result
        return _clean_result("get_manager_history", result["arguments"], {"manager": manager, "fund": fund, "history": [_manager_payload(row) for row in result["rows"]]})

    def get_cash_flows(self, fund: str, period: str) -> dict[str, Any]:
        """Retrieve canonical contributions, distributions and net cash flow for a fund and period. Use for cash-flow, inflow/outflow and net-flow questions."""
        result = self.tools.get_cash_flows(fund, period)
        if not result.get("ok"):
            return result
        return _clean_result(
            "get_cash_flows",
            result["arguments"],
            {
                "fund": fund,
                "period": period,
                "net_cash_flow": _metric_payload(result["metrics"]["net_cash_flow"]),
                "cash_flow_records": [
                    {
                        "quarter": row["quarter"],
                        "flow_type": row["flow_type"],
                        "amount": _metric_payload(row["amount"]),
                    }
                    for row in result["rows"]
                ],
            },
        )

    def get_research_signals(
        self,
        fund: str | None = None,
        period: str | None = None,
        asset_class: str | None = None,
        manager: str | None = None,
        signal_id: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve ranked evidence-backed Beacon research/Insight objects. Use for what stands out, what to investigate, risks, concerns, CIO focus areas, red flags or unusual developments."""
        result = self.tools.get_research_signals(fund=fund, period=period, asset_class=asset_class, manager=manager)
        if not result.get("ok"):
            return result
        rows = []
        for row in result["rows"]:
            if signal_id and signal_id not in {row.get("id"), row.get("signal_id")}:
                continue
            enriched = dict(row)
            enriched.setdefault("fund", fund or row.get("fund"))
            enriched.setdefault("asset_class", asset_class or row.get("asset_class") or _asset_from_text(row["headline"].lower()))
            enriched.setdefault("manager", manager or row.get("manager") or _manager_from_headline(row["headline"], self.tools.model))
            rows.append(enriched)
        if signal_id and not rows:
            return _clean_result(
                "get_research_signals",
                {**result["arguments"], "signal_id": signal_id},
                {"rows": []},
            )
        return _clean_result("get_research_signals", {**result["arguments"], "signal_id": signal_id}, {"rows": rows})

    def compare_funds(self, metric: str, period: str, asset_class: str | None = None) -> dict[str, Any]:
        """Compare canonical metrics across BPT and BLE. Use for other-fund, both-funds, which fund did better, closer-to-policy or cross-fund comparison questions."""
        result = self.tools.compare_funds(metric=metric, period=period, asset_class=asset_class)
        if not result.get("ok"):
            return result
        rows = []
        for row in result["rows"]:
            payload = {"fund": row["fund"], "metric": _metric_payload(row["metric"])}
            if asset_class is None:
                summary = self.tools.get_fund_summary(row["fund"], period)
                if summary.get("ok"):
                    metrics = summary["metrics"]
                    payload.update(
                        {
                            "return_pct": _metric_payload(metrics["return"]),
                            "benchmark_pct": _metric_payload(metrics["policy_benchmark"]),
                            "excess_return_pp": _metric_payload(metrics["excess_return"]),
                        }
                    )
            rows.append(payload)
        return _clean_result(
            "compare_funds",
            result["arguments"],
            {
                "metric": metric,
                "period": period,
                "asset_class": asset_class,
                "rows": rows,
                "comparison": result["comparison"],
            },
        )

    def compare_periods(self, entity: str, metric: str, period_a: str, period_b: str, fund: str | None = None) -> dict[str, Any]:
        """Compare one canonical metric across two supported FY2026 periods. Use for Q4 versus Q3, recent changes, worsening, improvement or did-it-change questions when the entity is known."""
        result = self.tools.compare_periods(entity=entity, metric=metric, period_a=period_a, period_b=period_b, fund=fund)
        if not result.get("ok"):
            return result
        return _clean_result(
            "compare_periods",
            result["arguments"],
            {
                "entity": entity,
                "metric": metric,
                "period_a": period_a,
                "period_b": period_b,
                "fund": fund,
                "rows": [{"period": row["period"], "metric": _metric_payload(row["metric"])} for row in result["rows"]],
                "comparison": result["comparison"],
            },
        )

    def validate_reconciliation(self, fund: str, period: str) -> dict[str, Any]:
        """Retrieve canonical reconciliation variance and allocation validation status. Use for trust, reconcile, data-quality or can-I-rely-on-this questions."""
        result = self.tools.validate_reconciliation(fund, period)
        if not result.get("ok"):
            return result
        summary = self.tools.get_fund_summary(fund, period)
        allocation = self.tools.get_asset_allocation(fund, period, "Cash")
        source_records = []
        if summary.get("ok"):
            source_records.append(_metric_payload(summary["metrics"]["aum"]))
        if allocation.get("ok"):
            source_records.append(_metric_payload(allocation["metrics"]["actual_allocation"]))
        return _clean_result(
            "validate_reconciliation",
            result["arguments"],
            {
                "fund": fund,
                "period": period,
                "reconciliation_variance": _metric_payload(result["metrics"]["reconciliation_variance"]),
                "allocation_validation_status": _metric_payload(result["metrics"]["allocation_validation"]),
                "validation_rows": result["rows"],
                "source_records": source_records,
            },
        )

    def get_source_record(self, record_id: str) -> dict[str, Any]:
        """Retrieve workbook, sheet, row and cell provenance for a metric, research signal or previous source record ID. Use for source, evidence or how-do-you-know follow-ups."""
        result = self.tools.get_source_record(record_id)
        if not result.get("ok"):
            return result
        return _clean_result("get_source_record", result["arguments"], {"record": result.get("record", {})})

    def as_langchain_tools(self) -> list[StructuredTool]:
        return [
            StructuredTool.from_function(self.get_fund_performance),
            StructuredTool.from_function(self.get_asset_allocation),
            StructuredTool.from_function(self.get_allocation_history),
            StructuredTool.from_function(self.rank_asset_allocations),
            StructuredTool.from_function(self.rank_funds),
            StructuredTool.from_function(self.get_manager_performance),
            StructuredTool.from_function(self.rank_managers),
            StructuredTool.from_function(self.get_manager_history),
            StructuredTool.from_function(self.get_cash_flows),
            StructuredTool.from_function(self.get_research_signals),
            StructuredTool.from_function(self.compare_funds),
            StructuredTool.from_function(self.compare_periods),
            StructuredTool.from_function(self.validate_reconciliation),
            StructuredTool.from_function(self.get_source_record),
        ]


class AskBeaconConversation:
    def __init__(
        self,
        adapter: ChatModelAdapter | None = None,
        checkpoint_path: str | Path = DEFAULT_CHECKPOINT_PATH,
        model: dict[str, Any] | None = None,
        tools_connected: bool = True,
    ):
        self.adapter = adapter or build_default_adapter()
        self.model = model
        self.tools_connected = tools_connected
        self.tool_adapter = BeaconToolAdapter(model or _build_default_model()) if tools_connected else None
        self.tools = self.tool_adapter.as_langchain_tools() if self.tool_adapter else []
        self.tool_map = {tool.name: tool for tool in self.tools}
        self.checkpoint_path = Path(checkpoint_path)
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.checkpoint_path), check_same_thread=False)
        self.checkpointer = SqliteSaver(self._connection)
        self.checkpointer.setup()
        self.graph = build_ask_beacon_graph(self.adapter, self.checkpointer, self.tools, self.tool_map)

    def ask(
        self,
        thread_id: str,
        message: str,
        application_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_stage = "conversation_started"
        _safe_log_event({"event": last_stage})
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": MAX_AGENT_ITERATIONS}
        try:
            prior_values = self.graph.get_state(config).values or {}
            prior_message_count = len(prior_values.get("messages", []))
            prior_event_count = len(prior_values.get("tool_events", []))
            prior_source_count = len(prior_values.get("sources", []))
            prior_tool_result_count = len(prior_values.get("tool_results", []))
            state: AskBeaconState = {
                "messages": [HumanMessage(content=message)],
                "application_context": application_context or {},
                "resolved_context": _context_from_user_message(message, application_context or {}),
                "sources": [],
                "tool_events": [{"event": "message_received"}],
                "tool_results": [],
                "validation_errors": [],
            }
            started = time.perf_counter()
            last_stage = "graph_started"
            _safe_log_event({"event": last_stage})
            result = self.graph.invoke(state, config=config)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            messages = [_message_to_dict(item) for item in result.get("messages", [])]
            answers = [item["content"] for item in messages if item["role"] == "assistant"]
            tool_events = result.get("tool_events", [])
            sources = result.get("sources", [])
            turn_messages = messages[prior_message_count:]
            turn_tool_events = tool_events[prior_event_count:]
            turn_sources = sources[prior_source_count:]
            turn_tool_results = result.get("tool_results", [])[prior_tool_result_count:]
            last_stage = "validation_started"
            _safe_log_event({"event": last_stage})
            validation_started = time.perf_counter()
            grounded_response = build_grounded_response(
                answer=answers[-1] if answers else "",
                user_message=message,
                application_context=result.get("resolved_context", {}) or result.get("application_context", {}),
                turn_messages=_tool_results_as_turn_messages(turn_tool_results) or turn_messages,
                turn_tool_events=turn_tool_events,
                turn_sources=turn_sources,
            )
            last_stage = "validation_completed"
            _safe_log_event({"event": last_stage})
            validation_elapsed_ms = round((time.perf_counter() - validation_started) * 1000, 2)
            return {
                "thread_id": thread_id,
                "answer": grounded_response["answer"],
                "grounded_response": grounded_response,
                "messages": messages,
                "turn_messages": turn_messages,
                "application_context": result.get("application_context", {}),
                "sources": sources,
                "turn_sources": turn_sources,
                "tool_events": tool_events,
                "turn_tool_events": turn_tool_events + [{"event": "answer_completed", "ok": not grounded_response["validation_errors"]}],
                "resolved_context": result.get("resolved_context", {}),
                "validation_errors": result.get("validation_errors", []) + grounded_response["validation_errors"],
                "elapsed_ms": elapsed_ms,
                "validation_elapsed_ms": validation_elapsed_ms,
            }
        except GraphRecursionError as exc:
            _safe_log_runtime_error(exc, last_stage)
            raise AgentIterationLimitExceeded(f"Ask Beacon reached the maximum of {MAX_AGENT_ITERATIONS} agent/tool iterations for this request.") from exc
        except Exception as exc:
            _safe_log_runtime_error(exc, last_stage)
            raise

    def get_state(self, thread_id: str) -> dict[str, Any]:
        state = self.graph.get_state({"configurable": {"thread_id": thread_id}})
        values = state.values or {}
        return {
            "messages": [_message_to_dict(item) for item in values.get("messages", [])],
            "application_context": values.get("application_context", {}),
            "resolved_context": values.get("resolved_context", {}),
            "sources": values.get("sources", []),
            "tool_events": values.get("tool_events", []),
            "validation_errors": values.get("validation_errors", []),
        }

    def close(self) -> None:
        self._connection.close()


def build_ask_beacon_graph(adapter: ChatModelAdapter, checkpointer: SqliteSaver, tools: list[StructuredTool] | None = None, tool_map: dict[str, StructuredTool] | None = None):
    tools = tools or []
    tool_map = tool_map or {}

    def chat_node(state: AskBeaconState) -> dict[str, Any]:
        prompt_messages = [SystemMessage(content=system_prompt())]
        if state.get("application_context"):
            prompt_messages.append(SystemMessage(content=f"Application context: {json.dumps(state['application_context'], sort_keys=True)}"))
        if state.get("resolved_context"):
            prompt_messages.append(SystemMessage(content=f"Resolved conversation context: {json.dumps(state['resolved_context'], sort_keys=True)}"))
        prompt_messages.extend(_recent_conversation_messages(state.get("messages", []), max_items=6))
        model_started = time.perf_counter()
        _safe_log_event({"event": "model_started", "provider": adapter.provider_name, "model": adapter.model_name})
        response = adapter.invoke(prompt_messages, tools)
        elapsed_ms = round((time.perf_counter() - model_started) * 1000, 2)
        tool_calls = [call.get("name") for call in getattr(response, "tool_calls", [])]
        _safe_log_event({"event": "model_completed", "provider": adapter.provider_name, "model": adapter.model_name, "elapsed_ms": elapsed_ms, "tool_calls": tool_calls})
        resolved_context = _pending_context_from_clarification(response, state)
        return {
            "messages": [response],
            "tool_events": [
                {
                    "event": "sent_to_model",
                    "provider": adapter.provider_name,
                    "model": adapter.model_name,
                },
                {
                    "event": "model_completed",
                    "provider": adapter.provider_name,
                    "model": adapter.model_name,
                    "elapsed_ms": elapsed_ms,
                    "tools_connected": bool(tools),
                    "tool_calls": tool_calls,
                }
            ],
            "sources": [],
            "validation_errors": [],
            "resolved_context": resolved_context,
        }

    def tool_node(state: AskBeaconState) -> dict[str, Any]:
        last = state["messages"][-1]
        outputs: list[ToolMessage] = []
        events: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        resolved_context: dict[str, Any] = {}
        validation_errors: list[str] = []
        for call in getattr(last, "tool_calls", []) or []:
            name = call["name"]
            args = call.get("args", {})
            call_id = call["id"]
            tool_started = time.perf_counter()
            _safe_log_event({"event": "tool_started", "tool": name})
            _safe_log_event({"event": "tool_selected", "tool": name, "arguments": args})
            events.append({"event": "tool_selected", "tool": name, "arguments": args})
            tool = tool_map.get(name)
            if tool is None:
                result = {"ok": False, "tool": name, "arguments": args, "error": {"code": "unsupported_tool", "message": "Tool is not allowlisted."}}
                validation_errors.append("unsupported_tool")
            else:
                result = tool.invoke(args)
                if not result.get("ok"):
                    validation_errors.append(result.get("error", {}).get("code", "tool_error"))
            record_ids = sorted(_collect_record_ids(result))
            tool_results.append(result)
            resolved_context.update(_context_from_tool_result(result))
            sources.extend(_collect_provenance_entries(result))
            elapsed_ms = round((time.perf_counter() - tool_started) * 1000, 2)
            _safe_log_event({"event": "tool_completed", "tool": name, "ok": result.get("ok"), "elapsed_ms": elapsed_ms, "record_ids": record_ids})
            events.append({"event": "tool_completed", "tool": name, "ok": result.get("ok"), "elapsed_ms": elapsed_ms, "record_ids": record_ids})
            outputs.append(ToolMessage(content=json.dumps(_model_observation(result), default=str), name=name, tool_call_id=call_id, status="success" if result.get("ok") else "error"))
        return {"messages": outputs, "tool_events": events, "sources": sources, "tool_results": tool_results, "resolved_context": resolved_context, "validation_errors": validation_errors}

    def respond_node(state: AskBeaconState) -> dict[str, Any]:
        result = (state.get("tool_results") or [])[-1]
        return {
            "messages": [AIMessage(content=_safe_answer_from_tool_result(result, state.get("messages", [])))],
            "tool_events": [{"event": "structured_response_completed", "response_type": result.get("response_type")}],
            "sources": [],
            "tool_results": [],
            "validation_errors": [],
        }

    def next_step(state: AskBeaconState) -> str:
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else END

    def after_tools(state: AskBeaconState) -> str:
        results = state.get("tool_results") or []
        if len(results) == 1 and results[-1].get("ok") and results[-1].get("response_type"):
            return "respond"
        return "chat"

    graph = StateGraph(AskBeaconState)
    graph.add_node("chat", chat_node)
    graph.add_node("tools", tool_node)
    graph.add_node("respond", respond_node)
    graph.add_edge(START, "chat")
    graph.add_conditional_edges("chat", next_step, {"tools": "tools", END: END})
    graph.add_conditional_edges("tools", after_tools, {"respond": "respond", "chat": "chat"})
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)


def build_default_adapter() -> ChatModelAdapter:
    provider = os.getenv("AI_PROVIDER", "ollama").strip().lower()
    if provider not in {"ollama", "local"}:
        raise ProviderUnavailable(f"Ask Beacon provider '{provider}' is not configured for this local runtime.")
    return OllamaChatAdapter()


def _recent_conversation_messages(messages: list[BaseMessage], max_items: int = 6) -> list[BaseMessage]:
    recent: list[BaseMessage] = []
    for message in reversed(messages):
        recent.append(message)
        if len(recent) >= max_items:
            break
    return list(reversed(recent))


def _tool_results_as_turn_messages(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"role": "tool", "content": json.dumps(result, default=str)} for result in results]


def _context_from_user_message(message: str, application_context: dict[str, Any]) -> dict[str, Any]:
    text = message.lower()
    context = {key: value for key, value in (application_context or {}).items() if value is not None}
    for source_key, active_key in (
        ("fund", "active_fund"),
        ("period", "active_period"),
        ("asset_class", "active_asset_class"),
        ("manager", "active_manager"),
        ("metric", "active_metric"),
    ):
        if context.get(source_key) is not None and context.get(active_key) is None:
            context[active_key] = context[source_key]
    fund = _fund_from_text(text)
    period = _period_from_text(text)
    asset_class = _asset_from_text(text)
    if fund and _is_comparison_followup(text, fund, context):
        context["comparison_fund"] = fund
    elif fund:
        context["fund"] = fund
        context["active_fund"] = fund
    if period:
        context["period"] = period
        context["active_period"] = period
    if asset_class:
        context["asset_class"] = asset_class
        context["active_asset_class"] = asset_class
    if "manager" in text or "mgr" in text:
        context["active_dimension"] = "manager"
    if any(term in text for term in ("allocation", "policy", "drift", "cash")):
        context["active_dimension"] = "allocation"
    if any(term in text for term in ("investigate", "stands out", "worry", "red flag", "focus", "important")):
        context["active_dimension"] = "research"
    if any(term in text for term in ("return", "benchmark", "perform", "outperform")):
        context["active_dimension"] = "performance"
    if any(term in text for term in ("actually", "i meant", "use ", "not ", "forget")):
        context["correction"] = True
    if "relative to benchmark" in text or "vs bm" in text or "against benchmark" in text:
        context["active_metric"] = "excess_return"
    if "absolute return" in text:
        context["active_metric"] = "absolute_return"
    if "consistency" in text or "consistent" in text:
        context["active_metric"] = "consistency"
    return context


def _pending_context_from_clarification(response: BaseMessage, state: AskBeaconState) -> dict[str, Any]:
    if getattr(response, "tool_calls", None):
        return {}
    answer = str(getattr(response, "content", "") or "")
    if not _looks_like_clarification(answer):
        return {}
    latest_user = _latest_human_text(state.get("messages", []))
    context = _merge_context(state.get("application_context", {}), state.get("resolved_context", {}))
    text = answer.lower()
    if "quarterly" in text and ("fund" in text or "manager" in text or "allocation" in text):
        return {
            "pending_clarification": {
                "original_intent": "quarterly_trend",
                "original_user_message": latest_user,
                "known_context": {
                    "fund": context.get("active_fund") or context.get("fund"),
                    "period": context.get("active_period") or context.get("period") or "FY2026",
                    "asset_class": context.get("active_asset_class") or context.get("asset_class"),
                    "manager": context.get("active_manager") or context.get("manager"),
                },
                "missing_dimension": "trend_type",
                "options": _quarterly_trend_options(),
            }
        }
    return {}


def _resolve_pending_clarification(
    text: str,
    pending: dict[str, Any],
    fund: str | None,
    period: str | None,
    previous_manager: dict[str, Any] | None,
    previous_asset: str | None,
    recent_context: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(pending, dict) or pending.get("original_intent") != "quarterly_trend":
        return None
    selection = _quarterly_trend_selection(text)
    if not selection:
        return None
    known = pending.get("known_context") or {}
    resolved_fund = fund or known.get("fund") or recent_context.get("active_fund") or recent_context.get("fund") or "BPT"
    resolved_period = period or known.get("period") or recent_context.get("active_period") or recent_context.get("period") or "FY2026"
    resolved_asset = previous_asset or known.get("asset_class") or recent_context.get("active_asset_class") or recent_context.get("asset_class")
    resolved_manager = previous_manager or (
        {"manager": known.get("manager"), "fund": resolved_fund}
        if known.get("manager")
        else None
    )
    if selection == "fund_performance":
        return {
            "type": "tool",
            "tool": "compare_periods",
            "arguments": {"entity": resolved_fund, "metric": "fund_return_pct", "period_a": "Q1", "period_b": "Q4", "fund": resolved_fund},
        }
    if selection == "manager_performance":
        if resolved_manager and resolved_manager.get("manager"):
            return {"type": "tool", "tool": "get_manager_history", "arguments": {"manager": resolved_manager["manager"], "fund": resolved_manager.get("fund") or resolved_fund}}
        return {"type": "tool", "tool": "rank_managers", "arguments": {"fund": resolved_fund, "period": resolved_period, "metric": "excess_return", "direction": "ascending", "limit": 5}}
    if selection == "allocation_history":
        if resolved_asset:
            return {"type": "tool", "tool": "get_allocation_history", "arguments": {"fund": resolved_fund, "asset_class": resolved_asset}}
        return {"type": "tool", "tool": "rank_asset_allocations", "arguments": {"fund": resolved_fund, "period": resolved_period, "direction": "largest_absolute", "limit": 5}}
    return None


def _context_from_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    context["pending_clarification"] = {}
    for key in ("fund", "period", "asset_class", "manager", "metric"):
        if result.get(key) is not None:
            context[key] = result[key]
    if result.get("fund") is not None:
        context["active_fund"] = result["fund"]
    if result.get("period") is not None:
        context["active_period"] = result["period"]
    if result.get("asset_class") is not None:
        context["active_asset_class"] = result["asset_class"]
    if result.get("manager") is not None:
        context["active_manager"] = result["manager"]
    if result.get("metric") is not None:
        context["active_metric"] = result["metric"]
    tool = result.get("tool")
    if tool:
        context["last_tool"] = tool
        context["last_response_type"] = _response_type_for_tool(tool)
    if tool == "get_research_signals":
        rows = result.get("rows") or []
        context["active_dimension"] = "research"
        signal_ids = [row.get("signal_id") or row.get("id") for row in rows if row.get("signal_id") or row.get("id")]
        context["last_research_signal_ids"] = signal_ids
        if rows:
            first = rows[0]
            signal_id = first.get("signal_id") or first.get("id")
            if signal_id:
                context["research_signal_id"] = signal_id
                context["primary_research_signal_id"] = signal_id
            if first.get("headline"):
                context["headline"] = first["headline"]
            source_record_ids = first.get("source_record_ids") or first.get("record_ids") or []
            if isinstance(source_record_ids, str):
                source_record_ids = [source_record_ids]
            if source_record_ids:
                context["source_record_ids"] = source_record_ids
                context["last_record_ids"] = source_record_ids
            for key in ("fund", "period", "asset_class", "manager"):
                if first.get(key) is not None:
                    context[key] = first[key]
                    context[f"active_{key}"] = first[key]
    elif tool in {"rank_managers", "get_manager_performance", "get_manager_history"}:
        context["active_dimension"] = "manager"
        rows = result.get("rows") or result.get("history") or []
        if rows:
            first = rows[0]
            for key in ("fund", "period", "asset_class", "manager"):
                if first.get(key) is not None:
                    context[key] = first[key]
                    context[f"active_{key}"] = first[key]
            if first.get("manager"):
                context["last_manager"] = first["manager"]
    elif tool in {"get_asset_allocation", "get_allocation_history", "rank_asset_allocations"}:
        context["active_dimension"] = "allocation"
        rows = result.get("rows") or result.get("history") or []
        if rows and isinstance(rows[0], dict):
            first = rows[0]
            for key in ("fund", "period", "asset_class"):
                if first.get(key) is not None:
                    context[key] = first[key]
                    context[f"active_{key}"] = first[key]
    elif tool in {"get_fund_performance", "rank_funds", "compare_funds", "compare_periods"}:
        context["active_dimension"] = "performance"
        rows = result.get("rows") or []
        funds = [row.get("fund") for row in rows if isinstance(row, dict) and row.get("fund")]
        if tool == "compare_funds" and funds:
            context["active_fund"] = context.get("active_fund") or funds[0]
            if len(funds) > 1:
                context["comparison_fund"] = funds[1]
    record_ids = sorted(_collect_record_ids(result))
    if record_ids:
        context.setdefault("last_record_ids", record_ids[:5])
    context["last_tool_result"] = {
        "tool": tool,
        "response_type": context.get("last_response_type"),
        "record_ids": record_ids[:5],
    }
    return context


def _model_observation(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("ok"):
        return {"ok": False, "tool": result.get("tool"), "arguments": result.get("arguments"), "error": result.get("error")}
    tool = result.get("tool")
    base = {"ok": True, "tool": tool, "arguments": result.get("arguments", {})}
    for key in ("fund", "period", "asset_class", "manager", "metric", "direction"):
        if result.get(key) is not None:
            base[key] = result[key]
    if tool == "get_fund_performance":
        return {**base, **_pick_metrics(result, "ending_aum", "fund_return_pct", "policy_benchmark_return_pct", "excess_return_pp", "net_cash_flow", "investment_gain_loss")}
    if tool == "get_asset_allocation":
        return {**base, **_pick_metrics(result, "market_value", "actual_allocation_pct", "policy_target_pct", "allocation_drift_pp", "dollar_variance_to_policy"), "status": result.get("status")}
    if tool == "get_allocation_history":
        return {**base, "history": [_compact_row(row, "period", "actual_allocation_pct", "policy_target_pct", "allocation_drift_pp") for row in result.get("history", [])]}
    if tool == "rank_asset_allocations":
        return {
            **base,
            "fund": result.get("fund"),
            "period": result.get("period"),
            "direction": result.get("direction"),
            "rows": [
                {
                    "fund": row.get("fund"),
                    "period": row.get("period"),
                    "asset_class": row.get("asset_class"),
                    "status": row.get("status"),
                    "metrics": {
                        key: _metric_for_model(value)
                        for key, value in (row.get("metrics") or {}).items()
                    },
                }
                for row in result.get("rows", [])[:5]
            ],
        }
    if tool in {"rank_funds", "rank_managers"}:
        return {**base, "rows": [_compact_rank(row) for row in result.get("rows", [])[:5]]}
    if tool == "get_manager_performance":
        return {**base, "rows": [_compact_row(row, "fund", "period", "asset_class", "manager", "manager_return_pct", "manager_benchmark_return_pct", "manager_excess_return_pp", "quarters_outperforming") for row in result.get("rows", [])[:5]]}
    if tool == "get_manager_history":
        return {**base, "history": [_compact_row(row, "fund", "period", "asset_class", "manager", "manager_return_pct", "manager_benchmark_return_pct", "manager_excess_return_pp", "quarters_outperforming") for row in result.get("history", [])]}
    if tool == "get_cash_flows":
        return {**base, "net_cash_flow": _metric_for_model(result.get("net_cash_flow")), "cash_flow_records": [_compact_row(row, "quarter", "flow_type", "amount") for row in result.get("cash_flow_records", [])[:8]]}
    if tool == "compare_funds":
        return {**base, "rows": [_compact_rank(row) for row in result.get("rows", [])], "comparison": result.get("comparison")}
    if tool == "compare_periods":
        return {
            **base,
            "entity": result.get("entity"),
            "metric": result.get("metric"),
            "period_a": result.get("period_a"),
            "period_b": result.get("period_b"),
            "fund": result.get("fund"),
            "rows": [_compact_row(row, "period", "metric") for row in result.get("rows", [])],
            "comparison": result.get("comparison"),
        }
    if tool == "get_research_signals":
        return {**base, "rows": [_research_signal_for_model(row) for row in result.get("rows", [])[:5]]}
    if tool == "validate_reconciliation":
        return {**base, **_pick_metrics(result, "reconciliation_variance", "allocation_validation_status")}
    if tool == "get_source_record":
        record = result.get("record", {})
        return {**base, "record": {key: record.get(key) for key in ("record_id", "source_record_id", "signal_id", "headline", "table", "fund", "period", "asset_class", "manager") if record.get(key) is not None}}
    return base


def _research_signal_for_model(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "signal_id",
            "id",
            "type",
            "horizon",
            "fund",
            "period",
            "asset_class",
            "manager",
            "headline",
            "primary_metric",
            "primary_value",
            "observation",
            "interpretation",
            "why_it_matters",
            "cio_question",
            "significance_score",
            "supporting_metrics",
            "limitations",
            "possible_explanations",
            "what_to_check_next",
            "source_record_ids",
            "related_analysis",
        )
        if row.get(key) is not None
    }


def _pick_metrics(result: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: _metric_for_model(result.get(key)) for key in keys if key in result}


def _metric_for_model(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: value.get(key) for key in ("metric_id", "value", "value_text", "unit", "support_status") if value.get(key) is not None}
    return value


def _compact_row(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: _metric_for_model(row.get(key)) for key in keys if row.get(key) is not None}


def _compact_rank(row: dict[str, Any]) -> dict[str, Any]:
    compact = {key: row.get(key) for key in ("rank", "fund", "period", "asset_class", "manager") if row.get(key) is not None}
    if row.get("metric") is not None:
        compact["metric"] = _metric_for_model(row["metric"])
    return compact


def _safe_log_event(event: dict[str, Any]) -> None:
    try:
        print(json.dumps({"component": "ask_beacon", **event}, default=str), flush=True)
    except Exception:
        pass


def _safe_log_runtime_error(exc: Exception, last_stage: str) -> None:
    try:
        print(
            f"[ASK BEACON RUNTIME ERROR]\nlast_stage={last_stage}\ntype={exc.__class__.__name__}\nmessage={exc}",
            flush=True,
        )
    except Exception:
        pass


def new_thread_id() -> str:
    return f"thread_{uuid.uuid4().hex}"


def system_prompt() -> str:
    return (
        "You are Beacon, an institutional portfolio intelligence assistant.\n\n"
        "Communicate naturally, like a concise investment analyst. Preserve context across turns. "
        "Use explicit user wording first, then recent conversation, then resolved context, then UI/application context.\n\n"
        "If Beacon has enough context and a trusted tool can answer, act. Do not ask whether the user wants you to retrieve, check, or analyze data. "
        "Ask one short clarification only when different interpretations materially change the answer.\n\n"
        "Do not pretend to have accessed portfolio data until a trusted Beacon data tool has actually been called. "
        "Use tools for authoritative financial values, rankings, comparisons, allocation drift, period logic, research signals, and source provenance. "
        "Never invent financial values, table numbers, or unsupported causes.\n\n"
        "Available funds are BPT and BLE. Supported periods are FY2026, H1 FY2026, H2 FY2026, and Q1-Q4. "
        "Interpret intent semantically rather than by exact phrasing. Treat investigation, focus, concern, unusual-pattern, or CIO-review requests as research-signal requests. "
        "Treat weak, struggled, lagged, benchmark-relative, or manager-ranking requests as manager performance/ranking requests. "
        "Treat off-policy, overweight, underweight, drift, or worsening-allocation requests as allocation/allocation-history requests. "
        "Treat return, performance, benchmark, outperform, or excess-return requests at fund level as fund performance requests. "
        "Treat brief follow-ups as referring to the most recent clear fund, manager, asset class, research signal, metric, or source record unless the user's wording overrides it. "
        "For fund performance or benchmark-relative fund questions, use get_fund_performance or compare_funds/rank_funds. "
        "For manager strength, weakness, consistency, deterioration, or rankings, use rank_managers and get_manager_history when needed. "
        "For allocation, policy target, overweight, underweight, drift, or trend questions, use get_asset_allocation, rank_asset_allocations, or get_allocation_history. "
        "For cash-flow questions, use get_cash_flows. "
        "For source or evidence follow-ups, use get_source_record against the previous record IDs. "
        "For broad investigation, risk, concern, what stands out, red flags, CIO focus, or where-to-dig questions, use get_research_signals and discuss the returned Beacon research/Insight objects.\n\n"
        "Use tables when comparisons are clearer: research signals, fund comparisons, manager rankings, allocation drift, and quarterly trends. "
        "Keep simple factual answers short.\n\n"
        "For every substantive answer, begin with a short first line that reflects the resolved question or topic, not vague words like 'that' or 'it'. "
        "Examples of style: 'On BPT's FY2026 return:', 'On what stands out in BPT:', 'On Q4 manager performance:', "
        "'On BLE in comparison:', or 'On the source behind that result:'. "
        "Use short paragraphs with a blank line after the main conclusion. Do not recreate numerical cards or tables in prose when a structured result is available.\n\n"
        "If the user asks why something happened, distinguish what the data shows from causes the dataset cannot establish. "
        "Close every answer as answered, continued with a tool, clarification required, or data limitation; never leave the user at 'would you like me to retrieve that?'\n\n"
        "Original normalized workbook-derived data and canonical deterministic Python calculations are the source of numerical truth. "
        "Research signals are shared Insight objects for interpretation and investigation, not a replacement for canonical metric calculations."
    )


def build_grounded_response(
    answer: str,
    user_message: str,
    application_context: dict[str, Any],
    turn_messages: list[dict[str, Any]],
    turn_tool_events: list[dict[str, Any]],
    turn_sources: list[dict[str, Any]],
) -> dict[str, Any]:
    observations = [_load_tool_observation(message) for message in turn_messages if message["role"] == "tool"]
    observations = [item for item in observations if item]
    structured_response = _structured_response_from_observations(observations)
    metrics = _extract_metric_payloads(observations)
    limitations: list[str] = []
    validation_errors: list[str] = []

    for result in observations:
        if not result.get("ok"):
            validation_errors.append(result.get("error", {}).get("code", "tool_error"))
            continue
        validation_errors.extend(_validate_tool_entity_match(result))

    if _expects_financial_grounding(observations, answer) and not turn_sources:
        validation_errors.append("missing_provenance")
        limitations.append("Source provenance was not available for the retrieved financial result.")

    unsupported_metric_errors = _unsupported_metric_errors(observations)
    validation_errors.extend(unsupported_metric_errors)
    if unsupported_metric_errors:
        limitations.append("The requested metric is not supported by the canonical Beacon metric layer.")

    unsupported_causality = _unsupported_causality_requested(user_message, answer)
    if unsupported_causality:
        validation_errors.append("unsupported_causality")
        limitations.append("The available Beacon data shows what happened, but does not establish holdings-level, market, or strategy causality.")

    unsupported_numbers = _unsupported_numbers_in_answer(answer, metrics) if metrics else []
    if unsupported_numbers:
        validation_errors.append("unsupported_numerical_value")
        limitations.append("The final wording contained a number that was not found in the retrieved canonical tool metrics.")

    final_answer = answer
    if unsupported_causality and "does not establish" not in final_answer.lower() and "cannot establish" not in final_answer.lower():
        final_answer = f"{final_answer} The available Beacon data shows what happened, but does not establish holdings-level, market, or strategy causality."
    if unsupported_numbers:
        fallback = _fallback_answer_from_observations(observations, turn_messages)
        final_answer = fallback or "I retrieved the Beacon data, but I cannot safely return the drafted numerical answer because it introduced a number that was not present in the canonical tool result."
    final_answer = _apply_conversational_response_style(final_answer, user_message, observations, application_context)

    if not structured_response and _looks_like_clarification(final_answer):
        structured_response = {
            "response_type": "clarification",
            "question": final_answer,
            "options": _suggest_clarification_options(final_answer, observations),
        }

    return {
        "answer": final_answer,
        "metrics": metrics,
        "sources": turn_sources,
        "limitations": _dedupe_strings(limitations),
        "activity_events": turn_tool_events,
        "validation_errors": _dedupe_strings(validation_errors),
        "application_context": application_context,
        "structured_response": structured_response,
        "response_type": structured_response.get("response_type") if structured_response else None,
        "followups": _suggest_followups(observations, application_context, final_answer),
        "clarification_options": _suggest_clarification_options(final_answer, observations),
    }


def _message_to_dict(message: BaseMessage) -> dict[str, Any]:
    role = "assistant"
    if isinstance(message, HumanMessage):
        role = "user"
    elif isinstance(message, SystemMessage):
        role = "system"
    elif isinstance(message, ToolMessage):
        role = "tool"
    return {"role": role, "content": message.content}


def _fallback_answer_from_observations(observations: list[dict[str, Any]], turn_messages: list[dict[str, Any]]) -> str | None:
    for result in reversed(observations):
        if result.get("ok") and _extract_metric_payloads(result):
            try:
                return _safe_answer_from_tool_result(result, _base_messages_from_dicts(turn_messages))
            except (KeyError, IndexError, TypeError, ValueError):
                return None
    return None


def _base_messages_from_dicts(messages: list[dict[str, Any]]) -> list[BaseMessage]:
    converted: list[BaseMessage] = []
    for message in messages:
        role = message.get("role")
        content = str(message.get("content") or "")
        if role == "user":
            converted.append(HumanMessage(content=content))
        elif role == "tool":
            converted.append(ToolMessage(content=content, tool_call_id="fallback"))
        elif role == "system":
            converted.append(SystemMessage(content=content))
        else:
            converted.append(AIMessage(content=content))
    return converted


def _apply_conversational_response_style(
    answer: str,
    user_message: str,
    observations: list[dict[str, Any]],
    context: dict[str, Any],
) -> str:
    text = str(answer or "").strip()
    if not text or _looks_like_clarification(text) or text.lower().startswith("on "):
        return text
    lead = _response_lead_in(user_message, observations, context)
    if not lead:
        return text
    if text.lstrip().startswith("|"):
        return f"{lead}.\n\n{text}"
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs:
        return f"{lead}."
    first = paragraphs[0]
    rest = paragraphs[1:]
    sentence_parts = re.split(r"(?<=[.!?])\s+", first, maxsplit=1)
    main = sentence_parts[0].strip()
    if len(sentence_parts) > 1 and sentence_parts[1].strip():
        rest.insert(0, sentence_parts[1].strip())
    styled = f"{lead}: {main}"
    continuation = _natural_continuation(observations, context)
    if rest:
        styled = f"{styled}\n\n" + "\n\n".join(rest)
    if continuation and continuation.lower() not in styled.lower():
        styled = f"{styled}\n\n{continuation}"
    return styled


def _response_lead_in(user_message: str, observations: list[dict[str, Any]], context: dict[str, Any]) -> str | None:
    latest = str(user_message or "").lower()
    result = next((item for item in reversed(observations) if item.get("ok")), None)
    if not result:
        return "On your Beacon question"
    tool = result.get("tool")
    fund = result.get("fund") or result.get("arguments", {}).get("fund") or context.get("fund")
    period = result.get("period") or result.get("arguments", {}).get("period") or context.get("period")
    asset = result.get("asset_class") or result.get("arguments", {}).get("asset_class") or context.get("asset_class")
    rows = result.get("rows") or result.get("history") or []
    first = rows[0] if rows and isinstance(rows[0], dict) else {}
    manager = result.get("manager") or first.get("manager") or context.get("manager")
    if tool == "get_source_record" or "source" in latest:
        return "On the source behind that result"
    if "relative to benchmark" in latest or "against benchmark" in latest:
        return "On benchmark-relative performance"
    if tool == "compare_funds" and ("and ble" in latest or fund == "BLE" and "compare" in latest):
        return "On BLE in comparison"
    if tool == "compare_funds" and ("and bpt" in latest or fund == "BPT" and "compare" in latest):
        return "On BPT in comparison"
    if "worst" in latest or "weakest" in latest:
        return "On the weakest manager in that ranking"
    if tool == "get_research_signals":
        return f"On what stands out in {fund or 'the selected portfolio'}"
    if tool in {"rank_managers", "get_manager_performance", "get_manager_history"}:
        if manager:
            return f"On {manager}'s performance"
        return f"On {period or 'the selected period'} manager performance"
    if tool in {"get_asset_allocation", "rank_asset_allocations"}:
        scope = " ".join(part for part in (fund, asset) if part)
        return f"On {scope or 'the selected'} allocation"
    if tool == "get_allocation_history":
        scope = " ".join(part for part in (fund, asset) if part)
        if "worsen" in latest or "worse" in latest:
            return f"On whether {scope or 'that allocation'} has worsened"
        return f"On {scope or 'that allocation'} over time"
    if tool in {"get_fund_performance", "rank_funds"}:
        if fund and period:
            return f"On {fund}'s {period} return"
        return "On fund performance"
    if tool == "compare_funds":
        row_funds = [row.get("fund") for row in rows if isinstance(row, dict) and row.get("fund")]
        if len(row_funds) >= 2 and period:
            return f"On comparing {row_funds[0]} with {row_funds[1]} for {period}"
        if asset:
            return f"On the {asset} comparison"
        return "On the fund comparison"
    if tool == "compare_periods":
        entity = result.get("entity") or context.get("active_fund") or context.get("fund") or "the selected item"
        metric = str(result.get("metric") or "")
        if metric == "fund_return_pct":
            return f"On {entity}'s quarterly fund performance"
        return f"On {entity}'s quarterly trend"
    if tool == "get_cash_flows":
        return f"On {fund or 'the fund'} cash flows"
    if tool == "validate_reconciliation":
        return f"On {fund or 'the fund'} reconciliation"
    return "On your Beacon question"


def _natural_continuation(observations: list[dict[str, Any]], context: dict[str, Any]) -> str | None:
    result = next((item for item in reversed(observations) if item.get("ok")), None)
    if not result:
        return None
    tool = result.get("tool")
    other_fund = "BLE" if context.get("fund") == "BPT" else "BPT" if context.get("fund") == "BLE" else None
    if tool == "get_fund_performance":
        return f"I can compare this with {other_fund} or look at allocation trends next." if other_fund else "I can compare this with the other fund or look at allocation trends next."
    if tool in {"rank_managers", "get_manager_performance", "get_manager_history"}:
        return "The manager history is the next place I would look."
    if tool in {"get_asset_allocation", "get_allocation_history", "rank_asset_allocations"}:
        return "I can trace this through Q1-Q4 to show whether the drift is persistent."
    if tool == "get_research_signals":
        return "I can open the numbers behind any of these signals next."
    if tool == "get_source_record":
        return None
    if tool == "compare_funds":
        return "I can switch that comparison to benchmark-relative return, allocation drift, or the source evidence."
    return "The underlying workbook evidence is available if you want the source."


def _structured_response_from_observations(observations: list[dict[str, Any]]) -> dict[str, Any]:
    for result in reversed(observations):
        if result.get("ok") and result.get("response_type"):
            response_type = result["response_type"]
            common = {
                "response_type": response_type,
                "tool": result.get("tool"),
                "arguments": result.get("arguments", {}),
                "provenance": result.get("provenance", []),
                "record_ids": result.get("record_ids", []),
            }
            if response_type == "research_signals":
                return {**common, "fund": result.get("arguments", {}).get("fund"), "period": result.get("arguments", {}).get("period"), "signals": result.get("rows", [])}
            if response_type == "manager_ranking":
                return {
                    **common,
                    "fund": result.get("arguments", {}).get("fund"),
                    "period": result.get("period"),
                    "dimension": _comparison_dimension(result.get("metric")),
                    "metric": result.get("metric"),
                    "rows": result.get("rows", []),
                    "interpretation": _manager_ranking_interpretation(result),
                }
            if response_type == "manager_performance":
                return {**common, "rows": result.get("rows", [])}
            if response_type == "allocation_drift":
                rows = result.get("rows")
                if rows is None:
                    rows = [result]
                return {
                    **common,
                    "fund": result.get("fund"),
                    "period": result.get("period"),
                    "asset_class": result.get("asset_class"),
                    "rows": rows,
                    "largest_overweight": _allocation_extreme(rows, "overweight"),
                    "largest_underweight": _allocation_extreme(rows, "underweight"),
                }
            if response_type in {"allocation_history", "quarterly_trend"}:
                return {**common, "fund": result.get("fund"), "asset_class": result.get("asset_class"), "manager": result.get("manager"), "rows": result.get("history", [])}
            if response_type == "fund_performance":
                metrics = {
                    key: result.get(key)
                    for key in ("ending_aum", "fund_return_pct", "policy_benchmark_return_pct", "excess_return_pp", "net_cash_flow", "investment_gain_loss")
                    if result.get(key) is not None
                }
                return {
                    **common,
                    "fund": result.get("fund"),
                    "period": result.get("period"),
                    "metrics": {
                        **metrics,
                        "return_pct": metrics.get("fund_return_pct"),
                        "benchmark_pct": metrics.get("policy_benchmark_return_pct"),
                    },
                    "interpretation": _fund_performance_interpretation(result),
                }
            if response_type == "fund_comparison":
                rows = result.get("rows", [])
                return {
                    **common,
                    "period": result.get("period"),
                    "dimension": _comparison_dimension(result.get("metric")),
                    "metric": result.get("metric"),
                    "asset_class": result.get("asset_class"),
                    "funds": [row.get("fund") for row in rows if row.get("fund")],
                    "rows": rows,
                    "comparison": result.get("comparison"),
                    "summary": _fund_comparison_summary(rows, result.get("comparison")),
                }
            if response_type == "cash_flow":
                return {
                    **common,
                    "fund": result.get("fund"),
                    "period": result.get("period"),
                    "metrics": {"net_cash_flow": result.get("net_cash_flow")},
                    "net_cash_flow": result.get("net_cash_flow"),
                    "rows": result.get("cash_flow_records", []),
                    "interpretation": _cash_flow_interpretation(result),
                    "limitations": ["The data establishes the size and direction of the flows, but not the underlying cause of distributions or contributions."],
                }
            if response_type == "source_evidence":
                record = result.get("record", {})
                return {**common, "for_response_id": result.get("arguments", {}).get("record_id"), "record": record, "sources": _collect_provenance_entries(record)}
            return {**common, "result": result}
    return {}


def _metric_value(row: dict[str, Any], key: str) -> float | None:
    metric = row.get(key)
    if not isinstance(metric, dict):
        return None
    value = metric.get("value")
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _comparison_dimension(metric: Any) -> str:
    metric_text = str(metric or "").lower()
    if "excess" in metric_text or "benchmark" in metric_text:
        return "benchmark_relative"
    if "allocation" in metric_text or "drift" in metric_text:
        return "allocation_policy"
    if "aum" in metric_text:
        return "aum"
    return "absolute_return"


def _fund_performance_interpretation(result: dict[str, Any]) -> str | None:
    excess = _metric_value(result, "excess_return_pp")
    if excess is None:
        return None
    if excess > 0:
        return "The fund outperformed its policy benchmark for the selected period."
    if excess < 0:
        return "The fund lagged its policy benchmark for the selected period."
    return "The fund matched its policy benchmark for the selected period."


def _fund_comparison_summary(rows: list[dict[str, Any]], comparison: dict[str, Any] | None) -> dict[str, Any]:
    values = [(row.get("fund"), _metric_value(row, "metric")) for row in rows]
    values = [(fund, value) for fund, value in values if fund and value is not None]
    stronger = max(values, key=lambda item: item[1])[0] if values else None
    interpretation = None
    if stronger:
        interpretation = f"{stronger} is higher on the selected comparison metric."
    return {
        "stronger_fund": stronger,
        "interpretation": interpretation,
        "difference": (comparison or {}).get("bpt_minus_ble"),
        "unit": (comparison or {}).get("unit"),
    }


def _allocation_extreme(rows: list[dict[str, Any]], direction: str) -> dict[str, Any] | None:
    candidates = []
    for row in rows:
        metrics = row.get("metrics") or row
        drift = _metric_value(metrics, "drift_pp")
        if drift is None:
            drift = _metric_value(metrics, "allocation_drift_pp")
        if drift is None:
            continue
        if direction == "overweight" and drift > 0:
            candidates.append((drift, row))
        if direction == "underweight" and drift < 0:
            candidates.append((drift, row))
    if not candidates:
        return None
    return (max(candidates, key=lambda item: item[0]) if direction == "overweight" else min(candidates, key=lambda item: item[0]))[1]


def _manager_ranking_interpretation(result: dict[str, Any]) -> str | None:
    rows = result.get("rows") or []
    if not rows:
        return None
    first = rows[0]
    manager = first.get("manager")
    metric = first.get("metric") or {}
    if not manager:
        return None
    value_text = metric.get("value_text") or metric.get("value")
    return f"{manager} is ranked first on the selected manager metric ({value_text})."


def _cash_flow_interpretation(result: dict[str, Any]) -> str | None:
    net = _metric_value(result, "net_cash_flow")
    if net is None:
        return None
    if net < 0:
        return "Net cash flow was negative, indicating a net outflow from the fund."
    if net > 0:
        return "Net cash flow was positive, indicating a net inflow to the fund."
    return "Net cash flow was flat for the selected period."


def _suggest_followups(observations: list[dict[str, Any]], context: dict[str, Any], answer: str) -> list[dict[str, str]]:
    if _looks_like_clarification(answer):
        return []
    tool = None
    for result in reversed(observations):
        if result.get("ok"):
            tool = result.get("tool")
            break
    other_fund = "BLE" if context.get("fund") == "BPT" else "BPT" if context.get("fund") == "BLE" else "BLE"
    suggestions: list[str]
    if tool == "compare_funds":
        metric = str((result or {}).get("metric") or context.get("active_metric") or context.get("metric") or "")
        suggestions = ["Show quarterly trend", "Compare allocation", "Source"] if "excess" in metric else ["Relative to benchmark", "Show quarterly trend", "Compare allocation", "Source"]
    elif tool in {"get_fund_performance", "rank_funds"}:
        metric = str((result or {}).get("metric") or context.get("active_metric") or context.get("metric") or "")
        suggestions = [f"Compare with {other_fund}", "Show quarterly performance", "Source"] if "excess" in metric else [f"Compare with {other_fund}", "Relative to benchmark", "Show quarterly performance", "Source"]
    elif tool in {"rank_managers", "get_manager_performance", "get_manager_history"}:
        suggestions = ["Show quarterly history", "Compare next worst", f"And {other_fund}?", "Source"]
    elif tool in {"get_asset_allocation", "get_allocation_history", "rank_asset_allocations"}:
        suggestions = ["Has this worsened?", "Show quarterly trend", f"Compare with {other_fund}", "Source"]
    elif tool == "get_research_signals":
        suggestions = ["Explain the top signal", "Show the numbers", "What about managers?", f"Compare with {other_fund}", "Source"]
    elif tool == "get_cash_flows":
        suggestions = [f"Compare with {other_fund}", "What changed in H2?", "Show source", "What should I investigate?"]
    else:
        suggestions = ["Show source", "What should I investigate next?"]
    return [{"label": item, "message": item} for item in suggestions[:5]]


def _suggest_clarification_options(answer: str, observations: list[dict[str, Any]]) -> list[dict[str, str]]:
    if observations or not _looks_like_clarification(answer):
        return []
    text = answer.lower()
    if "quarterly" in text and ("fund" in text or "manager" in text or "allocation" in text):
        return _quarterly_trend_options()
    if "absolute return" in text or "relative to benchmark" in text or "benchmark" in text or "consistent" in text:
        return [
            {"label": "Absolute return", "message": "Absolute return."},
            {"label": "Relative to benchmark", "message": "Relative to benchmark."},
            {"label": "Consistency over time", "message": "Consistency over time."},
        ]
    if "allocation" in text and "manager" in text:
        return [
            {"label": "Allocation", "message": "Allocation."},
            {"label": "Manager performance", "message": "Manager performance."},
        ]
    if "which allocation" in text or "asset allocation trend" in text:
        return [
            {"label": "Cash", "message": "Cash."},
            {"label": "Private Equity", "message": "Private Equity."},
            {"label": "Public Equity", "message": "Public Equity."},
            {"label": "Fixed Income", "message": "Fixed Income."},
        ]
    if "which result should i source" in text:
        return [
            {"label": "Latest result", "message": "Source the latest result."},
            {"label": "Performance", "message": "Source the performance result."},
            {"label": "Allocation", "message": "Source the allocation result."},
        ]
    return []


def _quarterly_trend_options() -> list[dict[str, str]]:
    return [
        {"label": "Fund performance", "message": "Fund performance."},
        {"label": "Manager performance", "message": "Manager performance."},
        {"label": "Asset allocation", "message": "Asset allocation."},
    ]


def _quarterly_trend_selection(text: str) -> str | None:
    cleaned = str(text or "").strip().lower().strip(".?!")
    if cleaned in {"fund", "fund performance", "fund return", "fund returns", "returns"}:
        return "fund_performance"
    if cleaned in {"manager", "manager performance", "manager returns", "managers"}:
        return "manager_performance"
    if cleaned in {"asset allocation", "allocation", "allocation history", "allocation trend"}:
        return "allocation_history"
    return None


def _looks_like_clarification(answer: str) -> bool:
    text = str(answer or "").strip().lower()
    return text.endswith("?") and any(term in text for term in ("do you mean", "which", "what", "should i", "absolute", "relative", "trend"))


def _load_tool_observation(message: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return json.loads(str(message["content"]))
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def _extract_metric_payloads(value: Any) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "metric_id" in value and ("value" in value or "value_text" in value):
            metrics.append(
                {
                    "metric_id": value.get("metric_id"),
                    "record_id": value.get("record_id"),
                    "value": value.get("value"),
                    "value_text": value.get("value_text"),
                    "unit": value.get("unit"),
                    "support_status": value.get("support_status"),
                    "provenance": value.get("provenance", []),
                    "calculation_owner": "Python",
                    "source": "canonical_metric_layer",
                }
            )
        for item in value.values():
            metrics.extend(_extract_metric_payloads(item))
    elif isinstance(value, list):
        for item in value:
            metrics.extend(_extract_metric_payloads(item))
    deduped: list[dict[str, Any]] = []
    seen = set()
    for metric in metrics:
        key = (metric.get("metric_id"), metric.get("record_id"), metric.get("value"), metric.get("value_text"))
        if key not in seen:
            seen.add(key)
            deduped.append(metric)
    return deduped


def _validate_tool_entity_match(result: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    args = result.get("arguments") or {}
    for field in ("fund", "period", "asset_class", "manager"):
        requested = args.get(field)
        if requested in (None, ""):
            continue
        actual_values = _entity_values(result, field)
        if actual_values and requested not in actual_values:
            errors.append(f"{field}_mismatch")
    return errors


def _entity_values(value: Any, field: str) -> set[Any]:
    values: set[Any] = set()
    if isinstance(value, dict):
        if field in value and value[field] not in (None, ""):
            values.add(value[field])
        for item in value.values():
            values.update(_entity_values(item, field))
    elif isinstance(value, list):
        for item in value:
            values.update(_entity_values(item, field))
    return values


def _expects_financial_grounding(observations: list[dict[str, Any]], answer: str) -> bool:
    if not observations:
        return False
    if any(result.get("ok") and _extract_metric_payloads(result) for result in observations):
        return True
    return bool(_answer_numbers(answer))


def _unsupported_metric_errors(observations: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for result in observations:
        error = result.get("error") or {}
        if error.get("code") == "unsupported_metric":
            errors.append("unsupported_metric")
    for metric in _extract_metric_payloads(observations):
        if metric.get("support_status") == "not_available" or not metric.get("metric_id"):
            errors.append("unsupported_metric")
    return errors


def _unsupported_causality_requested(user_message: str, answer: str) -> bool:
    text = user_message.lower()
    answer_text = answer.lower()
    causal_request = _has_any(text, "why", "caused", "because", "change strategy", "holdings caused", "what drove")
    causal_claim = _has_any(answer_text, "because", "due to", "caused by", "driven by", "resulted from")
    limitation_present = _has_any(answer_text, "cannot establish", "can't establish", "does not establish", "unavailable", "cannot answer")
    return causal_request and causal_claim and not limitation_present


def _unsupported_numbers_in_answer(answer: str, metrics: list[dict[str, Any]]) -> list[str]:
    numbers = _answer_numbers(answer)
    if not numbers:
        return []
    supported = _supported_number_strings(metrics)
    unsupported = []
    for number in numbers:
        normalized = _normalize_number(number)
        if _ignore_answer_number(normalized, answer):
            continue
        if normalized not in supported:
            unsupported.append(number)
    return unsupported


def _answer_numbers(answer: str) -> list[str]:
    return re.findall(r"(?<![A-Za-z0-9])[-+]?\d+(?:\.\d+)?", answer)


def _supported_number_strings(metrics: list[dict[str, Any]]) -> set[str]:
    supported: set[str] = set()
    for metric in metrics:
        value = metric.get("value")
        if isinstance(value, (int, float)):
            for candidate in (float(value), abs(float(value))):
                supported.update(
                    {
                        _normalize_number(f"{candidate}"),
                        _normalize_number(f"{candidate:.1f}"),
                        _normalize_number(f"{candidate:.2f}"),
                        _normalize_number(f"{candidate:.4f}"),
                        _normalize_number(f"{int(candidate)}") if float(candidate).is_integer() else "",
                    }
                )
        value_text = metric.get("value_text")
        if value_text:
            supported.update(_normalize_number(item) for item in _answer_numbers(str(value_text)))
    supported.discard("")
    return supported


def _normalize_number(value: str) -> str:
    try:
        number = float(value.replace("+", ""))
    except ValueError:
        return value
    if number == 0:
        number = 0.0
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _ignore_answer_number(number: str, answer: str) -> bool:
    lower = answer.lower()
    if number in {"1", "2", "3", "4", "2026"}:
        return True
    if f"q{number}" in lower:
        return True
    return False


def _dedupe_strings(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _tool_call_message(name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:10]}"}])


def _build_default_model() -> dict[str, Any]:
    return build_model(DEFAULT_DATA_DIR, DEFAULT_OUTPUT_DIR, DEFAULT_STORE_PATH)


def _clean_result(tool: str, arguments: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    record_ids = sorted(_collect_record_ids(payload))
    return {
        "ok": True,
        "tool": tool,
        "response_type": _response_type_for_tool(tool),
        "arguments": arguments,
        **payload,
        "record_ids": record_ids,
        "provenance": _collect_provenance_entries(payload),
    }


def _response_type_for_tool(tool: str) -> str | None:
    return {
        "get_fund_performance": "fund_performance",
        "rank_funds": "fund_comparison",
        "compare_funds": "fund_comparison",
        "get_asset_allocation": "allocation_drift",
        "rank_asset_allocations": "allocation_drift",
        "get_allocation_history": "allocation_history",
        "rank_managers": "manager_ranking",
        "get_manager_performance": "manager_performance",
        "get_manager_history": "quarterly_trend",
        "get_cash_flows": "cash_flow",
        "get_research_signals": "research_signals",
        "compare_periods": "period_comparison",
        "validate_reconciliation": "validation_status",
        "get_source_record": "source_evidence",
    }.get(tool)


def _metric_payload(metric: dict[str, Any]) -> dict[str, Any]:
    provenance = metric.get("provenance", {})
    return {
        "record_id": metric.get("record_id"),
        "metric_id": metric.get("metric_id"),
        "value": metric.get("value"),
        "value_text": metric.get("value_text"),
        "unit": metric.get("unit"),
        "support_status": metric.get("support_status"),
        "provenance": _compact_provenance(provenance),
    }


def _manager_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "fund": row["fund"],
        "period": row["period"],
        "asset_class": row["asset_class"],
        "manager": row["manager"],
        "manager_return_pct": _metric_payload(row["return"]),
        "manager_benchmark_return_pct": _metric_payload(row["benchmark"]),
        "manager_excess_return_pp": _metric_payload(row["excess_return"]),
        "quarters_outperforming": _metric_payload(row["consistency"]),
    }


def _manager_detail_payload(
    tools: BeaconBusinessTools,
    manager: str,
    fund: str | None,
    period: str | None,
    asset_class: str | None,
) -> dict[str, Any]:
    result = tools.get_manager_performance(manager=manager, fund=fund, period=period, asset_class=asset_class)
    if not result.get("ok") or not result.get("rows"):
        return {}
    return _manager_payload(result["rows"][0])


def _compact_provenance(provenance: dict[str, Any]) -> list[dict[str, Any]]:
    entries = []
    ids = provenance.get("source_record_ids") or []
    files = provenance.get("source_files") or []
    sheets = provenance.get("source_sheets") or []
    rows = provenance.get("source_rows") or []
    cells = provenance.get("source_cells") or []
    count = max(len(ids), len(files), len(sheets), len(rows), 1 if cells else 0)
    for index in range(count):
        entries.append(
            {
                "record_id": ids[index] if index < len(ids) else None,
                "source_file": files[index] if index < len(files) else None,
                "source_sheet": sheets[index] if index < len(sheets) else None,
                "source_row": rows[index] if index < len(rows) else None,
                "source_cells": cells if index == 0 else [],
            }
        )
    return [entry for entry in entries if any(value for value in entry.values())]


def _collect_record_ids(value: Any) -> set[str]:
    ids: set[str] = set()
    if isinstance(value, dict):
        record_id = value.get("record_id")
        if isinstance(record_id, str):
            ids.add(record_id)
        for key in ("record_ids", "source_record_ids"):
            items = value.get(key)
            if isinstance(items, list):
                ids.update(item for item in items if isinstance(item, str))
        for item in value.values():
            ids.update(_collect_record_ids(item))
    elif isinstance(value, list):
        for item in value:
            ids.update(_collect_record_ids(item))
    return ids


def _collect_provenance_entries(value: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if isinstance(value, dict):
        provenance = value.get("provenance")
        if isinstance(provenance, list):
            entries.extend(item for item in provenance if isinstance(item, dict))
        elif isinstance(provenance, dict):
            entries.extend(_compact_provenance(provenance))
        for item in value.values():
            entries.extend(_collect_provenance_entries(item))
    elif isinstance(value, list):
        for item in value:
            entries.extend(_collect_provenance_entries(item))
    deduped = []
    seen = set()
    for entry in entries:
        key = json.dumps(entry, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            deduped.append(entry)
    return deduped


def _safe_answer_from_tool_result(result: dict[str, Any], messages: list[BaseMessage] | None = None) -> str:
    try:
        return _answer_from_tool_result(result, messages)
    except (KeyError, IndexError, TypeError, ValueError):
        tool = result.get("tool")
        if tool in {"rank_asset_allocations", "get_asset_allocation", "get_allocation_history"}:
            return "I retrieved the allocation data, but could not format one of the allocation fields safely."
        if tool == "compare_funds" and str(result.get("metric") or "").startswith("allocation_"):
            return "I retrieved the allocation comparison, but could not format one of the comparison fields safely."
        return "I retrieved the Beacon data, but could not format one of the returned fields safely."


def _answer_from_tool_result(result: dict[str, Any], messages: list[BaseMessage] | None = None) -> str:
    if not result.get("ok"):
        error = result.get("error", {})
        return f"I could not answer that: {error.get('message', 'the Beacon tool returned an error')}"
    tool = result.get("tool")
    if tool == "get_fund_performance":
        return (
            f"{result['fund']} returned {result['fund_return_pct']['value']:.2f}% in {result['period']} "
            f"versus {result['policy_benchmark_return_pct']['value']:.2f}% for the policy benchmark, "
            f"for {result['excess_return_pp']['value']:+.2f}pp of excess return."
        )
    if tool == "get_asset_allocation":
        return (
            f"{result['fund']} {result['asset_class']} was {result['actual_allocation_pct']['value']:.2f}% "
            f"versus a {result['policy_target_pct']['value']:.2f}% policy target in {result['period']}, "
            f"a {result['allocation_drift_pp']['value']:+.2f}pp drift."
        )
    if tool == "get_allocation_history":
        history = result["history"]
        first = history[0]["allocation_drift_pp"]["value"]
        last = history[-1]["allocation_drift_pp"]["value"]
        return (
            f"{result['fund']} {result['asset_class']} drift moved from {first:+.2f}pp in Q1 "
            f"to {last:+.2f}pp in Q4. The Q1-Q4 history is sourced from canonical allocation records."
        )
    if tool == "rank_asset_allocations":
        top = result["rows"][0]
        drift = top["metrics"]["drift_pp"]["value"]
        actual = top["metrics"]["actual_allocation_pct"]["value"]
        target = top["metrics"]["policy_target_pct"]["value"]
        return (
            f"{top['asset_class']} is the main allocation item for {result['fund']} in {result['period']}: "
            f"actual allocation was {actual:.2f}% versus {target:.2f}% policy, a {drift:+.2f}pp drift."
        )
    if tool == "rank_funds":
        top = result["rows"][0]
        unit = top["metric"].get("unit")
        value = top["metric"]["value"]
        if result["metric"] == "ending_aum":
            value_text = f"{value:.2f} USD millions"
            label = "ending AUM"
        elif unit == "percentage points":
            value_text = f"{value:+.2f}pp"
            label = "benchmark-relative excess return"
        else:
            value_text = f"{value:.2f}%"
            label = "absolute return"
        return f"{top['fund']} ranked first for {label} in {result['period']}, at {value_text}."
    if tool == "rank_managers":
        top = result["rows"][0]
        return (
            f"{top['manager']} underperformed its benchmark most in {result['period']}, "
            f"with excess return of {top['metric']['value']:+.2f}pp."
        )
    if tool == "get_manager_performance":
        row = result["rows"][0]
        metric = _latest_rank_metric(messages or [])
        manager_return = row["manager_return_pct"]["value"]
        benchmark_return = row["manager_benchmark_return_pct"]["value"]
        excess_return = row["manager_excess_return_pp"]["value"]
        consistency = row["quarters_outperforming"]["value"]
        if _latest_human_contains(messages or [], "underperformed", "underperform", "weakest", "lowest"):
            return (
                f"{row['manager']} underperformed its benchmark most in {row['period']}. "
                f"It returned {manager_return:.2f}% against a benchmark return of {benchmark_return:.2f}%, "
                f"producing {excess_return:+.2f} percentage points of excess return."
            )
        if _latest_human_contains(messages or [], "consistent", "consistency"):
            return (
                f"{row['manager']} outperformed in {int(consistency)} observed quarter(s) for "
                f"{row['fund']} in {row['period']}. Its {row['period']} excess return was {excess_return:+.2f}pp."
            )
        if metric == "absolute_return":
            return (
                f"{row['manager']} had the highest absolute return for {row['fund']} in {row['period']}. "
                f"It returned {manager_return:.2f}% against a benchmark return of {benchmark_return:.2f}%, "
                f"producing {excess_return:+.2f} percentage points of excess return."
            )
        if metric == "excess_return":
            return (
                f"{row['manager']} had the strongest benchmark-relative performance for {row['fund']} in {row['period']}. "
                f"It returned {manager_return:.2f}% against a benchmark return of {benchmark_return:.2f}%, "
                f"producing {excess_return:+.2f} percentage points of excess return."
            )
        return (
            f"{row['manager']} returned {manager_return:.2f}% against a benchmark return of "
            f"{benchmark_return:.2f}% in {row['period']}, producing {excess_return:+.2f}pp of excess return."
        )
    if tool == "get_manager_history":
        history = result.get("history") or []
        if history:
            first = history[0]
            last = history[-1]
            first_excess = first["manager_excess_return_pp"]["value"]
            last_excess = last["manager_excess_return_pp"]["value"]
            direction = "worsened" if last_excess < first_excess else "improved" if last_excess > first_excess else "held steady"
            return (
                f"{last['manager']}'s benchmark-relative result {direction} from {first['period']} to {last['period']}: "
                f"excess return moved from {first_excess:+.2f}pp to {last_excess:+.2f}pp."
            )
    if tool == "get_cash_flows":
        return (
            f"{result['fund']} had net cash flow of {result['net_cash_flow']['value']:.2f} USD millions "
            f"in {result['period']}. That figure comes from the canonical cash-flow and fund summary records."
        )
    if tool == "compare_funds":
        rows = result.get("rows") or []
        if not rows:
            return "I retrieved the fund comparison, but there were no comparison rows to display."
        unit = (result.get("comparison") or {}).get("unit") or rows[0]["metric"].get("unit")
        first, second = rows[0], rows[1]
        if result.get("metric") == "allocation_drift_pp":
            asset = result.get("asset_class") or "allocation"
            return (
                f"For {asset} allocation drift in {result['period']}, {first['fund']} was "
                f"{first['metric']['value']:+.2f}pp and {second['fund']} was {second['metric']['value']:+.2f}pp."
            )
        metric = result.get("metric")
        latest = _latest_human_text(messages or []).lower()
        if metric in {"fund_return_pct", "fund_excess_return_pp"}:
            first_value = float(first["metric"]["value"])
            second_value = float(second["metric"]["value"])
            higher = first if first_value >= second_value else second
            lower = second if first_value >= second_value else first
            higher_value = float(higher["metric"]["value"])
            lower_value = float(lower["metric"]["value"])
            difference = higher_value - lower_value
            label = "FY2026 return" if metric == "fund_return_pct" else "benchmark-relative excess return"
            value_unit = "%" if metric == "fund_return_pct" else "pp"
            premise_fund = "BLE" if "ble" in latest else "BPT" if "bpt" in latest else None
            premise_weaker = _has_any(latest, "weaker", "worse", "behind", "underperform")
            premise_stronger = _has_any(latest, "stronger", "better", "ahead", "outperform")
            if premise_weaker and (not premise_fund or premise_fund == higher["fund"]):
                lead = f"Actually, {higher['fund']} is stronger, not weaker, on {label}"
            elif premise_stronger and premise_fund == lower["fund"]:
                lead = f"Actually, {lower['fund']} is weaker, not stronger, on {label}"
            else:
                lead = f"{higher['fund']} is stronger on {label}"
            return (
                f"{lead}: {higher['fund']} was {higher_value:.2f}{value_unit}, versus "
                f"{lower['fund']} at {lower_value:.2f}{value_unit}, a gap of {difference:.2f}{value_unit}."
            )
        return (
            f"For {result['metric']} in {result['period']}, {first['fund']} was {first['metric']['value']:.2f} "
            f"and {second['fund']} was {second['metric']['value']:.2f} {unit}."
        )
    if tool == "compare_periods":
        a, b = result["rows"]
        delta = result["comparison"]["period_b_minus_period_a"]
        return (
            f"{result['entity']} moved from {a['metric']['value']:.2f} in {result['period_a']} "
            f"to {b['metric']['value']:.2f} in {result['period_b']}, a change of {delta:+.2f} {result['comparison']['unit']}."
        )
    if tool == "get_research_signals":
        rows = result["rows"][:3]
        fund = result.get("arguments", {}).get("fund") or (rows[0].get("fund") if rows else "the selected portfolio")
        table = [
            "| Signal | Evidence | Why it matters |",
            "|---|---|---|",
        ]
        for row in rows:
            signal = row.get("headline") or row.get("signal_id") or "Research signal"
            evidence = row.get("primary_metric") or row.get("type") or row.get("signal_id") or "Beacon research signal"
            why = "Prioritize follow-up using the linked canonical Beacon evidence."
            if row.get("asset_class"):
                why = f"Focuses review on {row['asset_class']} positioning or performance."
            if row.get("manager"):
                why = f"Focuses review on {row['manager']} manager results."
            table.append(f"| {signal} | {evidence} | {why} |")
        summary_fund = fund or "the selected portfolio"
        return (
            f"{summary_fund} has {len(rows)} area{'s' if len(rows) != 1 else ''} worth investigating.\n\n"
            + "\n".join(table)
            + "\n\nInterpretation:\nThese are Beacon research signals, so they should guide investigation rather than replace canonical metric checks.\n\n"
            "CIO question:\nWhich of these signals is material enough to assign for deeper manager, allocation, or cash-flow review?"
        )
    if tool == "validate_reconciliation":
        return (
            f"{result['fund']} {result['period']} reconciliation variance is {result['reconciliation_variance']['value']:.4f}, "
            f"and allocation validation status is {result['allocation_validation_status']['value_text']}."
        )
    if tool == "get_source_record":
        record = result.get("record", {})
        provenance = record.get("provenance", {})
        files = provenance.get("source_files") or []
        sheets = provenance.get("source_sheets") or []
        rows = provenance.get("source_rows") or []
        return f"That traces to {files[0] if files else 'the Beacon source data'}, {sheets[0] if sheets else 'source sheet unavailable'}, row {rows[0] if rows else 'unavailable'}."
    return "I retrieved the requested Beacon tool result."


def _application_context_from_messages(messages: list[BaseMessage]) -> dict[str, Any]:
    for message in messages:
        if isinstance(message, SystemMessage) and str(message.content).startswith("Application context: "):
            try:
                return json.loads(str(message.content).split("Application context: ", 1)[1])
            except json.JSONDecodeError:
                return {}
    return {}


def _conversation_context_from_messages(messages: list[BaseMessage]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for prefix in ("Application context: ", "Resolved conversation context: "):
        for message in messages:
            if not isinstance(message, SystemMessage):
                continue
            content = str(message.content)
            if not content.startswith(prefix):
                continue
            try:
                context = _merge_context(context, json.loads(content.split(prefix, 1)[1]))
            except json.JSONDecodeError:
                continue
    return context


def _recent_conversation_context(messages: list[BaseMessage], fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    context = dict(fallback or {})
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        try:
            result = json.loads(str(message.content))
        except json.JSONDecodeError:
            continue
        for key in (
            "fund",
            "period",
            "asset_class",
            "manager",
            "metric",
            "active_fund",
            "active_period",
            "active_asset_class",
            "active_manager",
            "active_metric",
            "comparison_fund",
            "last_response_type",
            "research_signal_id",
            "primary_research_signal_id",
            "headline",
        ):
            if result.get(key):
                context[key] = result[key]
        for key in ("last_research_signal_ids", "last_record_ids", "source_record_ids"):
            if result.get(key):
                context[key] = result[key]
        tool = result.get("tool")
        if tool:
            context["last_tool"] = tool
        rows = result.get("rows") or result.get("history") or []
        if rows and isinstance(rows[0], dict):
            first = rows[0]
            if tool == "get_research_signals":
                signal_ids = [row.get("signal_id") or row.get("id") for row in rows if isinstance(row, dict) and (row.get("signal_id") or row.get("id"))]
                if signal_ids:
                    context["last_research_signal_ids"] = signal_ids
                    context["research_signal_id"] = signal_ids[0]
                    context["primary_research_signal_id"] = signal_ids[0]
                if first.get("headline"):
                    context["headline"] = first["headline"]
                source_record_ids = first.get("source_record_ids") or first.get("record_ids") or []
                if isinstance(source_record_ids, str):
                    source_record_ids = [source_record_ids]
                if source_record_ids:
                    context["source_record_ids"] = source_record_ids
                    context["last_record_ids"] = source_record_ids
            for key in ("fund", "period", "asset_class", "manager"):
                if first.get(key):
                    context[key] = first[key]
                    context[f"active_{key}"] = first[key]
            if first.get("signal_id") or first.get("id"):
                context["research_signal_id"] = first.get("signal_id") or first.get("id")
        break
    return context


def _latest_research_signal_id(messages: list[BaseMessage], context: dict[str, Any] | None = None) -> str | None:
    if context:
        for key in ("primary_research_signal_id", "research_signal_id"):
            if context.get(key):
                return str(context[key])
        values = context.get("last_research_signal_ids")
        if isinstance(values, list) and values:
            return str(values[0])
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        try:
            result = json.loads(str(message.content))
        except json.JSONDecodeError:
            continue
        rows = result.get("rows") or []
        for row in rows:
            if isinstance(row, dict) and (row.get("signal_id") or row.get("id")):
                return row.get("signal_id") or row.get("id")
    return None


def _latest_manager_from_tool_messages(messages: list[BaseMessage]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        try:
            result = json.loads(str(message.content))
        except json.JSONDecodeError:
            continue
        if result.get("tool") == "get_manager_performance" and result.get("ok") and result.get("rows"):
            row = result["rows"][0]
            return {"manager": row["manager"], "fund": row["fund"], "period": row["period"], "asset_class": row["asset_class"]}
        if result.get("tool") == "rank_managers" and result.get("ok") and result.get("rows"):
            row = result["rows"][0]
            return {"manager": row["manager"], "fund": row["fund"], "period": result["period"], "asset_class": row["asset_class"]}
    return None


def _latest_asset_from_tool_messages(messages: list[BaseMessage]) -> str | None:
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        try:
            result = json.loads(str(message.content))
        except json.JSONDecodeError:
            continue
        if result.get("asset_class"):
            return result["asset_class"]
        if result.get("rows"):
            for row in result["rows"]:
                if row.get("asset_class"):
                    return row["asset_class"]
    return None


def _latest_rank_metric(messages: list[BaseMessage]) -> str | None:
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        try:
            result = json.loads(str(message.content))
        except json.JSONDecodeError:
            continue
        if result.get("tool") == "rank_managers":
            return result.get("metric")
    return None


def _latest_human_contains(messages: list[BaseMessage], *terms: str) -> bool:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            text = str(message.content).lower()
            return any(term in text for term in terms)
    return False


def _latest_human_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _has_any(text: str, *terms: str) -> bool:
    return any(term in text for term in terms)


def _is_comparison_followup(text: str, explicit_fund: str | None, context: dict[str, Any] | None = None) -> bool:
    if not explicit_fund:
        return False
    active_fund = (context or {}).get("active_fund") or (context or {}).get("fund")
    if not active_fund or active_fund == explicit_fund:
        return False
    return _has_any(
        text,
        "compare",
        "with ",
        "and ",
        "what about",
        "versus",
        " vs ",
        "better",
        "worse",
    )


def _is_period_followup(text: str) -> bool:
    cleaned = text.strip().lower().strip(".?!")
    if cleaned in {"q1", "q2", "q3", "q4", "h1", "h2", "fy2026"}:
        return True
    return cleaned in {f"what about {period}" for period in ("q1", "q2", "q3", "q4", "h1", "h2", "fy2026")}


def _is_fund_followup(text: str, fund: str) -> bool:
    cleaned = text.strip().lower().strip(".?!")
    fund_text = fund.lower()
    return cleaned in {fund_text, f"and {fund_text}", f"what about {fund_text}"}


def _is_fund_only_request(text: str, fund: str) -> bool:
    cleaned = text.strip().lower().strip(".?!")
    fund_text = fund.lower()
    return cleaned in {f"{fund_text} only", f"only {fund_text}", f"just {fund_text}", f"{fund_text} alone"}


def _clean_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "n/a", "all funds", "all"}:
        return None
    return text


def _fund_from_text(text: str) -> str | None:
    if "ble" in text or "endowment" in text:
        return "BLE"
    if "bpt" in text or "pension" in text:
        return "BPT"
    return None


def _period_from_text(text: str) -> str | None:
    if "q1" in text:
        return "Q1"
    if "q2" in text or "middle of the year" in text:
        return "Q2"
    if "q3" in text:
        return "Q3"
    if "q4" in text or "last quarter" in text:
        return "Q4"
    if "second half" in text or "last six months" in text:
        return "H2 FY2026"
    if "h1" in text:
        return "H1 FY2026"
    if "fy2026" in text or "this year" in text or "year" in text:
        return "FY2026"
    return None


def _asset_from_text(text: str) -> str | None:
    if "private equity" in text or " pe " in f" {text} ":
        return "Private Equity"
    if "cash" in text:
        return "Cash"
    if "public equity" in text:
        return "Public Equity"
    if "fixed income" in text:
        return "Fixed Income"
    if "real assets" in text:
        return "Real Assets"
    if "hedge" in text:
        return "Hedge Funds"
    return None


def _manager_from_headline(headline: str, model: dict[str, Any]) -> str | None:
    text = headline.lower()
    for manager in model["dimensions"]["managers"]:
        if manager.lower() in text:
            return manager
    return None


def _quarter_for_validation(period: str) -> str:
    if period == "FY2026" or period == "H2 FY2026":
        return "Q4"
    if period == "H1 FY2026":
        return "Q2"
    return period


def _latest_record_id(messages: list[BaseMessage], context: dict[str, Any] | None = None) -> str | None:
    for key in ("last_record_ids", "source_record_ids", "record_ids"):
        values = (context or {}).get(key)
        if isinstance(values, list) and values:
            return str(values[0])
        if isinstance(values, str) and values:
            return values
    last_tool_result = (context or {}).get("last_tool_result")
    if isinstance(last_tool_result, dict):
        values = last_tool_result.get("record_ids")
        if isinstance(values, list) and values:
            return str(values[0])
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        try:
            result = json.loads(str(message.content))
        except json.JSONDecodeError:
            continue
        record_ids = result.get("record_ids") or []
        if record_ids:
            return record_ids[0]
    return None
