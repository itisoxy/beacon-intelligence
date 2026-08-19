from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .business_tools import BeaconBusinessTools, tool_schemas
from .semantic import AskBeaconContext, interpret_query


MAX_STEPS = 8
SAFE_EVENT_TYPES = {
    "context_resolved",
    "ambiguity_evaluated",
    "clarification_requested",
    "tool_selected",
    "tool_completed",
    "calculation_completed",
    "source_verified",
    "validation_failed",
    "answer_completed",
    "out_of_scope",
}


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str | None = None


@dataclass(frozen=True)
class ModelResponse:
    tool_calls: list[ToolCall] = field(default_factory=list)
    final_answer: str | None = None
    clarification: str | None = None
    out_of_scope: str | None = None


class ModelAdapter(Protocol):
    provider_name: str
    model_name: str

    def call(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        ...


class ProviderUnavailable(RuntimeError):
    pass


class OpenAIModelAdapter:
    provider_name = "openai"

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        if not os.getenv("OPENAI_API_KEY"):
            raise ProviderUnavailable("OPENAI_API_KEY is not configured.")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderUnavailable("The openai package is not installed.") from exc
        self.client = OpenAI()

    def call(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        response = self.client.responses.create(
            model=self.model_name,
            input=messages,
            tools=_openai_tool_schemas(tools),
        )
        tool_calls: list[ToolCall] = []
        final_parts: list[str] = []
        for item in getattr(response, "output", []) or []:
            item_type = getattr(item, "type", None)
            if item_type == "function_call":
                args = getattr(item, "arguments", {}) or {}
                if isinstance(args, str):
                    args = json.loads(args or "{}")
                tool_calls.append(ToolCall(name=getattr(item, "name"), arguments=args, call_id=getattr(item, "call_id", None)))
            if item_type == "message":
                for content in getattr(item, "content", []) or []:
                    text = getattr(content, "text", None)
                    if text:
                        final_parts.append(text)
        return ModelResponse(tool_calls=tool_calls, final_answer="\n".join(final_parts).strip() or None)


class ScriptedModelAdapter:
    provider_name = "scripted"
    model_name = "scripted-tool-model"

    def __init__(self, responses: list[ModelResponse]):
        self.responses = list(responses)
        self.calls = 0

    def call(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        del messages, tools
        self.calls += 1
        if not self.responses:
            return ModelResponse(final_answer="No scripted response was provided.")
        return self.responses.pop(0)


class AskBeaconAgent:
    def __init__(self, model: dict[str, Any], adapter: ModelAdapter, max_steps: int = MAX_STEPS):
        self.model = model
        self.adapter = adapter
        self.max_steps = max_steps
        self.tools = BeaconBusinessTools(model)
        self.allowed_tools = {schema["name"] for schema in tool_schemas()}

    def answer(self, query: str, context: AskBeaconContext | dict[str, Any] | None = None) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        interpretation = interpret_query(query, self.model["semantic_layer"], context)
        self._event(events, "context_resolved", query=query, interpretation=interpretation["interpretation"], context_used=interpretation["context_used"], conflicts=interpretation["conflicts"])
        preflight = self._preflight(query, interpretation, events)
        if preflight:
            return preflight
        if interpretation["clarifications"]:
            clarification = "Please clarify: " + "; ".join(interpretation["clarifications"])
            self._event(events, "clarification_requested", reasons=interpretation["clarifications"])
            return {"ok": False, "status": "needs_clarification", "outcome": "clarify", "answer": clarification, "events": events, "tool_observations": []}

        messages = [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": query},
            {"role": "context", "content": json.dumps({"interpretation": interpretation["interpretation"]})},
        ]
        observations: list[dict[str, Any]] = []
        for step in range(self.max_steps):
            model_response = self.adapter.call(messages, tool_schemas())
            if model_response.clarification:
                self._event(events, "clarification_requested", reasons=[model_response.clarification])
                return {"ok": False, "status": "needs_clarification", "outcome": "clarify", "answer": model_response.clarification, "events": events, "tool_observations": observations}
            if model_response.out_of_scope:
                self._event(events, "out_of_scope", reason=model_response.out_of_scope)
                return {"ok": False, "status": "out_of_scope", "outcome": "out_of_scope", "answer": model_response.out_of_scope, "events": events, "tool_observations": observations}
            if model_response.tool_calls:
                for call in model_response.tool_calls:
                    observation = self._execute_tool(call, events)
                    observations.append(observation)
                    messages.append({"role": "assistant", "tool_call": asdict(call)})
                    messages.append({"role": "tool", "name": call.name, "content": json.dumps(observation)})
                continue
            if model_response.final_answer:
                validation = self._validate_final(model_response.final_answer, observations)
                if validation:
                    self._event(events, "validation_failed", reason=validation)
                    tool_error_answer = _tool_error_answer(observations)
                    return {"ok": False, "status": "validation_failed", "outcome": "out_of_scope", "answer": tool_error_answer or validation, "events": events, "tool_observations": observations}
                self._event(events, "answer_completed", tool_steps=len(observations), model=self.adapter.model_name)
                return {"ok": True, "status": "answered", "outcome": "answer", "answer": model_response.final_answer, "events": events, "tool_observations": observations}
            self._event(events, "validation_failed", reason=f"Model returned no tool call or final answer at step {step + 1}.")
            return {"ok": False, "status": "validation_failed", "outcome": "out_of_scope", "answer": "I could not complete the request because the model returned no action.", "events": events, "tool_observations": observations}

        self._event(events, "validation_failed", reason="Maximum tool steps exceeded.")
        return {"ok": False, "status": "max_steps_exceeded", "outcome": "out_of_scope", "answer": "I could not complete the request within the tool-step limit.", "events": events, "tool_observations": observations}

    def _preflight(self, query: str, interpretation: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
        text = _norm_text(query)
        interpreted = interpretation["interpretation"]
        self._event(events, "ambiguity_evaluated", materially_ambiguous=bool(interpretation["clarifications"]))
        if _is_strategy_question(text):
            answer = (
                "The supplied Beacon dataset cannot establish why an investment strategy changed. "
                "It contains performance, benchmark, allocation, cash-flow and research-signal data, not manager strategy-change records.\n\n"
                "I can instead:\n"
                "- analyse the manager's performance\n"
                "- compare the manager with its benchmark\n"
                "- show the quarterly trend"
            )
            self._event(events, "out_of_scope", reason="strategy_data_unavailable")
            return {"ok": False, "status": "out_of_scope", "outcome": "out_of_scope", "answer": answer, "events": events, "tool_observations": []}
        if _asks_manager_best(text):
            answer = "How should I define best performance?\n\n- Highest absolute return\n- Highest excess return vs benchmark\n- Most consistent outperformer"
            self._event(events, "clarification_requested", reasons=["manager_best_metric_ambiguous"])
            return {"ok": False, "status": "needs_clarification", "outcome": "clarify", "answer": answer, "events": events, "tool_observations": []}
        if _asks_asset_how_did_do(text) and interpreted.get("asset_class") and not interpreted.get("metric_id"):
            answer = "What would you like to review for this asset class?\n\n- Performance vs benchmark\n- Allocation vs policy\n- Underlying managers\n- Full review"
            self._event(events, "clarification_requested", reasons=["asset_review_dimension_ambiguous"], asset_class=interpreted.get("asset_class"))
            return {"ok": False, "status": "needs_clarification", "outcome": "clarify", "answer": answer, "events": events, "tool_observations": []}
        if _asks_unsupported_underperformance_cause(text) and interpreted.get("manager"):
            return self._answer_unsupported_causality(query, interpreted, events)
        return None

    def _answer_unsupported_causality(self, query: str, interpreted: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
        manager = interpreted["manager"]
        fund = interpreted.get("fund")
        period = interpreted.get("period") or "FY2026"
        observations = [
            self._execute_tool(ToolCall("get_manager_performance", {"manager": manager, "fund": fund, "period": period}), events),
            self._execute_tool(ToolCall("get_manager_history", {"manager": manager, "fund": fund}), events),
        ]
        perf = observations[0]
        if not perf.get("ok"):
            return {"ok": False, "status": "out_of_scope", "outcome": "out_of_scope", "answer": "I cannot answer that from the supplied dataset because the manager was not found in Beacon's normalized FY2026 records.", "events": events, "tool_observations": observations}
        row = perf["rows"][0]
        excess = row["excess_return"]["value"]
        consistency = row["consistency"]["value"]
        source_files = row["excess_return"]["provenance"].get("source_files", [])
        answer = (
            f"{manager} had an excess return of {excess:+.2f}pp for {period} and outperformed in {consistency} observed quarter(s) for that horizon. "
            "The supplied dataset can show what happened versus benchmark and how consistently it happened, but holdings-level attribution is unavailable, so it cannot establish why the manager underperformed. "
            f"Source files: {', '.join(source_files) if source_files else 'available in tool provenance'}."
        )
        self._event(events, "answer_completed", tool_steps=len(observations), model="deterministic_preflight")
        return {"ok": True, "status": "unsupported_causality", "outcome": "unsupported_causality", "answer": answer, "events": events, "tool_observations": observations}

    def _execute_tool(self, call: ToolCall, events: list[dict[str, Any]]) -> dict[str, Any]:
        self._event(events, "tool_selected", tool=call.name, arguments=call.arguments)
        if call.name not in self.allowed_tools:
            result = {"ok": False, "tool": call.name, "arguments": call.arguments, "error": {"code": "unsupported_tool", "message": "Tool is not in the Ask Beacon allowlist.", "field": "tool", "value": call.name}}
            self._event(events, "validation_failed", tool=call.name, error=result["error"])
            return result
        method = getattr(self.tools, call.name)
        try:
            result = method(**call.arguments)
        except TypeError as exc:
            result = {"ok": False, "tool": call.name, "arguments": call.arguments, "error": {"code": "invalid_arguments", "message": str(exc), "field": None, "value": call.arguments}}
        self._event(events, "tool_completed", tool=call.name, ok=result.get("ok"), error=result.get("error"))
        if result.get("ok"):
            self._event(events, "calculation_completed", tool=call.name)
            if _has_provenance(result):
                self._event(events, "source_verified", tool=call.name)
        else:
            self._event(events, "validation_failed", tool=call.name, error=result.get("error"))
        return result

    def _validate_final(self, answer: str, observations: list[dict[str, Any]]) -> str | None:
        if not answer.strip():
            return "Final response was empty."
        successful_tools = [item for item in observations if item.get("ok")]
        if not successful_tools:
            return "Financial answers require at least one successful deterministic Beacon tool observation."
        if not any(_has_provenance(item) for item in successful_tools):
            return "Financial answers require source provenance."
        return None

    def _event(self, events: list[dict[str, Any]], event_type: str, **payload: Any) -> None:
        if event_type not in SAFE_EVENT_TYPES:
            raise ValueError(f"Unsupported safe event type: {event_type}")
        events.append({"event": event_type, **payload})


def build_default_adapter() -> ModelAdapter:
    return OpenAIModelAdapter()


def _has_provenance(value: Any) -> bool:
    if isinstance(value, dict):
        provenance = value.get("provenance")
        if isinstance(provenance, dict) and (provenance.get("source_record_ids") or provenance.get("source_files")):
            return True
        return any(_has_provenance(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_provenance(item) for item in value)
    return False


def _tool_error_answer(observations: list[dict[str, Any]]) -> str | None:
    for observation in observations:
        error = observation.get("error")
        if not error:
            continue
        code = error.get("code")
        field = error.get("field")
        value = error.get("value")
        message = error.get("message")
        if code == "invalid_period":
            return f"I can't run that request because {value} is not a valid Beacon period. Use Q1, Q2, Q3, Q4, H1 FY2026, H2 FY2026, or FY2026."
        if code == "unknown_entity":
            return f"I couldn't identify the requested {field or 'entity'}: {value}. {message}"
        if code == "no_data":
            return f"The supplied FY2026 Beacon dataset does not contain data for {value}. {message}"
        if code == "unsupported_metric":
            return f"That metric is not supported by the Beacon tool layer: {value}. {message}"
        return message
    return None


def _norm_text(text: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in text).split())


def _is_strategy_question(text: str) -> bool:
    return "strategy" in text or "investment strategy" in text


def _asks_manager_best(text: str) -> bool:
    return "manager" in text and ("best" in text or "strongest" in text or "performed best" in text)


def _asks_asset_how_did_do(text: str) -> bool:
    return ("how did" in text or "how has" in text) and (" do" in text or " done" in text)


def _asks_unsupported_underperformance_cause(text: str) -> bool:
    return text.startswith("why") and any(term in text for term in ["underperform", "underperformed", "underperforming", "below benchmark", "lagged benchmark"])


def _openai_tool_schemas(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": schema["name"],
            "description": schema["description"],
            "parameters": {
                "type": "object",
                "properties": {name: {"type": "string", "description": str(description)} for name, description in schema["parameters"].items()},
                "additionalProperties": True,
            },
        }
        for schema in schemas
    ]


def _system_prompt() -> str:
    return (
        "You are Ask Beacon. Use only the provided Beacon business tools for financial facts. "
        "Never invent a portfolio value. Distinguish sourced facts from inference. "
        "Ask for clarification when context is ambiguous. Do not claim unsupported causality. "
        "Keep answers concise and include source workbook/sheet/row references when available."
    )
