from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .business_tools import BeaconBusinessTools
from .semantic import interpret_query


@dataclass
class AskRequestState:
    request_id: str
    original_query: str
    context: dict[str, Any]
    resolved_context: dict[str, Any]
    intent: str
    status: str
    ambiguity: dict[str, Any] | None = None
    clarification: dict[str, Any] | None = None
    debug_log: list[dict[str, Any]] = field(default_factory=list)


class AskRequestStore:
    def __init__(self) -> None:
        self._requests: dict[str, AskRequestState] = {}

    def create(self, query: str, context: dict[str, Any], resolved_context: dict[str, Any], intent: str, ambiguity: dict[str, Any] | None) -> AskRequestState:
        request = AskRequestState(
            request_id=f"req_{uuid4().hex[:10]}",
            original_query=query,
            context=context,
            resolved_context=resolved_context,
            intent=intent,
            status="waiting_for_clarification" if ambiguity else "ready",
            ambiguity=ambiguity,
        )
        self._requests[request.request_id] = request
        return request

    def get(self, request_id: str) -> AskRequestState | None:
        return self._requests.get(request_id)


class AskBeaconService:
    def __init__(self, model: dict[str, Any], store: AskRequestStore | None = None):
        self.model = model
        self.store = store or AskRequestStore()
        self.tools = BeaconBusinessTools(model)

    def create_request(self, query: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = context or {}
        interpreted = interpret_query(query, self.model["semantic_layer"], context)
        intent, ambiguity = self._classify(query, interpreted)
        request = self.store.create(query, context, interpreted["interpretation"], intent, ambiguity)
        self._log(request, "received", original_query=query)
        self._log(request, "interpreting", resolved_context=interpreted["interpretation"], intent=intent, ambiguities=ambiguity)
        if ambiguity:
            self._log(request, "waiting_for_clarification", missing=ambiguity["field"])
            return self._clarification_response(request)
        request.status = "ready"
        return self._execute_ready(request)

    def clarify(self, request_id: str, selection: dict[str, Any]) -> dict[str, Any]:
        request = self.store.get(request_id)
        if not request:
            return self._error("unknown_request", "No preserved Ask Beacon request matched this request_id.", request_id=request_id)
        if request.status != "waiting_for_clarification":
            return self._error("invalid_request_state", "This request is not waiting for clarification.", request_id=request_id, status=request.status)
        field = selection.get("field")
        value = selection.get("value")
        if not request.ambiguity or field != request.ambiguity.get("field"):
            return self._error("invalid_clarification", "Clarification field does not match the pending ambiguity.", request_id=request_id, field=field)
        allowed = {option["value"] for option in self._options_for(request)}
        if value not in allowed:
            return self._error("invalid_clarification", "Clarification value is not one of the machine-readable options.", request_id=request_id, field=field, value=value)
        request.clarification = {"field": field, "value": value, "label": selection.get("label")}
        request.status = "ready"
        self._log(request, "clarification_received", **{field: value})
        self._log(request, "ready")
        return self._execute_ready(request)

    def _classify(self, query: str, interpreted: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        text = query.lower()
        if "manager" in text and ("best" in text or "performed best" in text or "strongest" in text):
            return "manager_ranking", {"field": "ranking_metric", "reason": "best_performance_metric"}
        if "performed best" in text or "who performed best" in text:
            return "manager_ranking", {"field": "ranking_metric", "reason": "best_performance_metric"}
        return "general", None

    def _clarification_response(self, request: AskRequestState) -> dict[str, Any]:
        return {
            "type": "clarification",
            "ok": False,
            "request_id": request.request_id,
            "status": request.status,
            "question": "How should I measure best performance?",
            "options": self._options_for(request),
            "debug_state": self._debug_state(request),
        }

    def _options_for(self, request: AskRequestState) -> list[dict[str, Any]]:
        if request.intent == "manager_ranking" and request.ambiguity and request.ambiguity["field"] == "ranking_metric":
            return [
                {"label": "Highest absolute return", "field": "ranking_metric", "value": "manager_return_pct"},
                {"label": "Highest return vs benchmark", "field": "ranking_metric", "value": "manager_excess_return_pp"},
                {"label": "Most consistent outperformer", "field": "ranking_metric", "value": "manager_consistency"},
            ]
        return []

    def _execute_ready(self, request: AskRequestState) -> dict[str, Any]:
        if request.intent != "manager_ranking":
            request.status = "unsupported"
            self._log(request, "validation_failed", validation_result="unsupported_intent")
            return self._error("unsupported_intent", "This request type is not implemented in the explicit resume service.", request_id=request.request_id)
        return self._execute_manager_ranking(request)

    def _execute_manager_ranking(self, request: AskRequestState) -> dict[str, Any]:
        metric = request.clarification["value"] if request.clarification else "manager_excess_return_pp"
        fund = request.resolved_context.get("fund") or request.context.get("fund") or "All"
        period = request.resolved_context.get("period") or request.context.get("period") or "FY2026"
        self._log(request, "tool_running", tool_selected="rank_managers", tool_arguments={"fund": fund, "period": period, "metric": metric, "direction": "desc", "limit": 1})
        ranked = self.tools.rank_managers(period=period, metric=metric, direction="desc", fund=fund, limit=1)
        self._log(request, "tool_complete", tool="rank_managers", ok=ranked.get("ok"), tool_result_record_ids=self._record_ids(ranked))
        if not ranked.get("ok") or not ranked.get("rows"):
            request.status = "failed"
            self._log(request, "validation_failed", validation_result=ranked.get("error"))
            return {"type": "error", "ok": False, "request_id": request.request_id, "error": ranked.get("error"), "debug_state": self._debug_state(request)}
        winner = ranked["rows"][0]
        manager = winner["manager"]
        asset_class = winner["asset_class"]
        self._log(request, "tool_running", tool_selected="get_manager_performance", tool_arguments={"manager": manager, "fund": fund, "period": period, "asset_class": asset_class})
        performance = self.tools.get_manager_performance(manager=manager, fund=fund, period=period, asset_class=asset_class)
        self._log(request, "tool_complete", tool="get_manager_performance", ok=performance.get("ok"), tool_result_record_ids=self._record_ids(performance))
        validation = self._validate_manager_answer(fund, period, ranked, performance)
        self._log(request, "validated", validation_result=validation)
        if not validation["ok"]:
            request.status = "failed"
            self._log(request, "validation_failed", validation_result=validation)
            return {"type": "error", "ok": False, "request_id": request.request_id, "error": validation, "debug_state": self._debug_state(request)}
        perf = performance["rows"][0]
        label = {
            "manager_return_pct": "highest absolute return",
            "manager_excess_return_pp": "strongest benchmark-relative performance",
            "manager_consistency": "most consistent outperformance",
        }[metric]
        answer = (
            f"{manager} had the {label} for {fund} in {period}. "
            f"It returned {perf['return']['value']:.2f}% against a benchmark of {perf['benchmark']['value']:.2f}%, "
            f"with excess return of {perf['excess_return']['value']:+.2f} percentage points."
        )
        request.status = "answered"
        self._log(request, "answered", final_response_status="answered")
        return {
            "type": "answer",
            "ok": True,
            "request_id": request.request_id,
            "answer": answer,
            "metrics": [
                self._metric_payload("Manager return", perf["return"]),
                self._metric_payload("Benchmark return", perf["benchmark"]),
                self._metric_payload("Excess return", perf["excess_return"]),
                self._metric_payload("Quarters outperforming", perf["consistency"]),
            ],
            "sources": [perf["excess_return"]["provenance"]],
            "activity_events": [
                f"Used {fund} / {period} context",
                "Queried manager performance",
                "Compared associated benchmarks",
                f"Ranked managers by {metric}",
                "Verified source record",
            ],
            "debug_state": self._debug_state(request),
        }

    def _validate_manager_answer(self, fund: str, period: str, ranked: dict[str, Any], performance: dict[str, Any]) -> dict[str, Any]:
        if not performance.get("ok") or not performance.get("rows"):
            return {"ok": False, "reason": "manager_performance_missing"}
        row = performance["rows"][0]
        checks = {
            "fund": row["fund"] == fund,
            "period": row["period"] == period,
            "manager_exists": bool(row["manager"]),
            "return_exists": row["return"]["value"] is not None,
            "benchmark_exists": row["benchmark"]["value"] is not None,
            "canonical_excess": row["excess_return"]["metric_id"] == "manager_excess_return_pp",
            "source_provenance": bool(row["excess_return"]["provenance"].get("source_record_ids")),
            "rank_record": bool(ranked["rows"][0]["metric"].get("record_id")),
        }
        return {"ok": all(checks.values()), "checks": checks}

    def _metric_payload(self, label: str, metric: dict[str, Any]) -> dict[str, Any]:
        return {
            "label": label,
            "record_id": metric.get("record_id"),
            "metric_id": metric.get("metric_id"),
            "value": metric.get("value"),
            "unit": metric.get("unit"),
            "provenance": metric.get("provenance"),
        }

    def _record_ids(self, value: Any) -> list[str]:
        ids: list[str] = []
        if isinstance(value, dict):
            record_id = value.get("record_id")
            if record_id:
                ids.append(record_id)
            for item in value.values():
                ids.extend(self._record_ids(item))
        elif isinstance(value, list):
            for item in value:
                ids.extend(self._record_ids(item))
        return sorted(set(ids))

    def _log(self, request: AskRequestState, status: str, **payload: Any) -> None:
        entry = {
            "request_id": request.request_id,
            "original_query": request.original_query,
            "status": status,
            "current_status": request.status,
            **payload,
        }
        request.debug_log.append(entry)

    def _debug_state(self, request: AskRequestState) -> dict[str, Any]:
        return {
            "request_id": request.request_id,
            "original_query": request.original_query,
            "current_status": request.status,
            "resolved_context": request.resolved_context,
            "intent": request.intent,
            "ambiguities": request.ambiguity,
            "clarification_selected": request.clarification,
            "events": request.debug_log,
        }

    def _error(self, code: str, message: str, **payload: Any) -> dict[str, Any]:
        return {"type": "error", "ok": False, "error": {"code": code, "message": message, **payload}}
