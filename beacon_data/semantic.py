from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class AskBeaconContext:
    fund: str | None = None
    period: str | None = None
    asset_class: str | None = None
    manager: str | None = None
    source_page: str | None = None
    research_signal_id: str | None = None


FUND_ALIASES = {
    "bpt": "BPT",
    "beacon pension trust": "BPT",
    "pension": "BPT",
    "pension trust": "BPT",
    "ble": "BLE",
    "beacon legacy endowment": "BLE",
    "endowment": "BLE",
}


ASSET_ALIAS_SEEDS = {
    "Absolute Return": ["absolute return", "hedge fund", "hedge funds", "alternatives"],
    "Cash": ["cash", "liquidity", "liquid assets"],
    "Core": ["core", "core fixed income", "core bonds"],
    "Growth (High Yield)": ["growth", "high yield", "hy", "growth high yield"],
    "Private Credit": ["private credit", "pc", "direct lending"],
    "Private Equity": ["private equity", "pe", "buyout", "private markets"],
    "Public Equity": ["public equity", "public equities", "equity", "equities", "stocks", "stock"],
    "Real Assets": ["real assets", "infrastructure", "natural resources"],
    "Real Estate": ["real estate", "re", "property"],
}


PERIOD_ALIASES = {
    "full year": "FY2026",
    "fy2026": "FY2026",
    "fy 2026": "FY2026",
    "year": "FY2026",
    "h1": "H1 FY2026",
    "first half": "H1 FY2026",
    "first six months": "H1 FY2026",
    "h2": "H2 FY2026",
    "second half": "H2 FY2026",
    "last six months": "H2 FY2026",
    "last 6 months": "H2 FY2026",
    "q1": "Q1",
    "quarter 1": "Q1",
    "q2": "Q2",
    "quarter 2": "Q2",
    "q3": "Q3",
    "quarter 3": "Q3",
    "q4": "Q4",
    "quarter 4": "Q4",
}


METRIC_VOCABULARY = [
    {
        "terms": ["underperform", "underperformed", "underperforming", "below benchmark", "trailed benchmark", "lagged benchmark"],
        "metric_id": "manager_excess_return_pp",
        "operator": "<",
        "threshold": 0,
        "meaning": "manager/fund excess return below zero",
        "requires_domain": "performance_subject",
    },
    {
        "terms": ["outperform", "outperformed", "outperforming", "beat benchmark", "above benchmark", "ahead of benchmark"],
        "metric_id": "manager_excess_return_pp",
        "operator": ">",
        "threshold": 0,
        "meaning": "manager/fund excess return above zero",
        "requires_domain": "performance_subject",
    },
    {
        "terms": ["drift", "off target", "policy deviation", "policy drift"],
        "metric_id": "allocation_drift_pp",
        "operator": None,
        "threshold": None,
        "meaning": "actual allocation percentage minus policy target percentage",
    },
    {
        "terms": ["overweight", "over weight", "overallocated", "over allocated"],
        "metric_id": "allocation_drift_pp",
        "operator": ">",
        "threshold": 0,
        "meaning": "allocation drift above zero",
    },
    {
        "terms": ["underweight", "under weight", "underallocated", "under allocated"],
        "metric_id": "allocation_drift_pp",
        "operator": "<",
        "threshold": 0,
        "meaning": "allocation drift below zero",
    },
    {
        "terms": ["aum", "assets under management", "market value", "portfolio value"],
        "metric_id": "ending_aum",
        "operator": None,
        "threshold": None,
        "meaning": "ending AUM",
    },
    {
        "terms": ["cash flow", "net flow", "net cash flow"],
        "metric_id": "net_cash_flow",
        "operator": None,
        "threshold": None,
        "meaning": "net cash flow",
    },
]


CLARIFICATION_TERMS = ["best", "worst", "strongest", "weakest", "performed well"]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _contains(text: str, term: str) -> bool:
    normalized = _norm(text)
    return re.search(rf"(^| ){re.escape(_norm(term))}( |$)", normalized) is not None


def _asset_aliases(asset_classes: list[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for asset in asset_classes:
        aliases[_norm(asset)] = asset
        for alias in ASSET_ALIAS_SEEDS.get(asset, []):
            aliases[_norm(alias)] = asset
    return aliases


def build_entity_dictionary(asset_classes: list[str], managers: list[str] | None = None) -> dict[str, Any]:
    manager_aliases = {_norm(manager): manager for manager in managers or []}
    return {
        "funds": FUND_ALIASES,
        "asset_classes": _asset_aliases(asset_classes),
        "periods": PERIOD_ALIASES,
        "managers": manager_aliases,
    }


def build_semantic_layer(asset_classes: list[str], managers: list[str], metric_registry: list[dict[str, Any]]) -> dict[str, Any]:
    metric_ids = {row["metric_id"] for row in metric_registry}
    vocabulary = [row for row in METRIC_VOCABULARY if row["metric_id"] in metric_ids]
    return {
        "entity_dictionary": build_entity_dictionary(asset_classes, managers),
        "metric_vocabulary": vocabulary,
        "context_schema": [field for field in AskBeaconContext.__dataclass_fields__],
        "context_precedence_rules": [
            "Explicit user language overrides application context.",
            "Application context fills missing fund, period, asset_class, manager and research_signal_id only when the query referent is otherwise clear.",
            "If explicit user language conflicts with context, return the explicit interpretation and record the conflict.",
            "Recently uses the active UI period/context. Without period context, ask for clarification.",
            "Last six months maps to H2 FY2026 for the FY2026 workbook dataset.",
        ],
        "terms_requiring_clarification": CLARIFICATION_TERMS,
        "example_interpretations": [
            {"query": "Which PE managers underperformed BPT in the last six months?", "fund_id": "BPT", "asset_class": "Private Equity", "period": "H2 FY2026", "metric_id": "manager_excess_return_pp", "operator": "<", "threshold": 0},
            {"query": "Why did this move?", "uses_context": ["fund", "period", "asset_class", "research_signal_id"], "clarification": "required only if context has no clear referent"},
            {"query": "Compare this with BLE.", "uses_context": ["fund", "period", "asset_class", "manager"], "compare_to_fund_id": "BLE"},
            {"query": "Has this got worse recently?", "uses_context": ["period"], "clarification": "required when no active period exists"},
        ],
    }


def _resolve_alias(text: str, aliases: dict[str, str]) -> tuple[str | None, str | None]:
    matches = [(alias, target) for alias, target in aliases.items() if _contains(text, alias)]
    if not matches:
        return None, None
    alias, target = sorted(matches, key=lambda item: len(item[0]), reverse=True)[0]
    return target, alias


def _metric_intent(text: str) -> dict[str, Any] | None:
    for vocabulary in METRIC_VOCABULARY:
        for term in vocabulary["terms"]:
            if _contains(text, term):
                return {
                    "metric_id": vocabulary["metric_id"],
                    "operator": vocabulary.get("operator"),
                    "threshold": vocabulary.get("threshold"),
                    "matched_term": term,
                    "meaning": vocabulary["meaning"],
                }
    return None


def _explicit_entities(query: str, entity_dictionary: dict[str, Any]) -> dict[str, Any]:
    fund, fund_alias = _resolve_alias(query, entity_dictionary["funds"])
    asset, asset_alias = _resolve_alias(query, entity_dictionary["asset_classes"])
    period, period_alias = _resolve_alias(query, entity_dictionary["periods"])
    manager, manager_alias = _resolve_alias(query, entity_dictionary["managers"])
    return {
        "fund": fund,
        "fund_alias": fund_alias,
        "asset_class": asset,
        "asset_class_alias": asset_alias,
        "period": period,
        "period_alias": period_alias,
        "manager": manager,
        "manager_alias": manager_alias,
    }


def interpret_query(query: str, semantic_layer: dict[str, Any], context: AskBeaconContext | dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = context if isinstance(context, AskBeaconContext) else AskBeaconContext(**(context or {}))
    entities = _explicit_entities(query, semantic_layer["entity_dictionary"])
    metric = _metric_intent(query)
    normalized_query = _norm(query)
    conflicts: list[dict[str, Any]] = []
    clarifications: list[str] = []
    is_comparison = _contains(query, "compare")
    compare_to_fund = entities["fund"] if is_comparison and entities["fund"] else None

    resolved = {
        "fund": entities["fund"] or ctx.fund,
        "period": entities["period"] or ctx.period,
        "asset_class": entities["asset_class"] or ctx.asset_class,
        "manager": entities["manager"] or ctx.manager,
        "source_page": ctx.source_page,
        "research_signal_id": ctx.research_signal_id,
    }
    if compare_to_fund and ctx.fund and compare_to_fund != ctx.fund:
        resolved["fund"] = ctx.fund
    for field in ("fund", "period", "asset_class", "manager"):
        explicit = entities[field]
        contextual = getattr(ctx, field)
        if field == "fund" and compare_to_fund and contextual and compare_to_fund != contextual:
            continue
        if explicit and contextual and explicit != contextual:
            conflicts.append({"field": field, "explicit": explicit, "context": contextual, "resolution": "explicit"})
            resolved[field] = explicit

    if "recently" in normalized_query and not entities["period"]:
        if not ctx.period:
            clarifications.append("recently requires an active period/context in this dataset")
        else:
            resolved["period"] = ctx.period

    if any(_contains(query, term) for term in CLARIFICATION_TERMS):
        clarifications.append("best/worst/strongest/weakest/performed well require a metric definition or ranking basis")

    contextual_referent = any(_contains(query, term) for term in ["this", "that", "it", "move"])
    if contextual_referent and not any([ctx.fund, ctx.asset_class, ctx.manager, ctx.research_signal_id, entities["fund"], entities["asset_class"], entities["manager"]]):
        clarifications.append("contextual request needs fund, asset class, manager, or research signal context")

    if is_comparison and entities["fund"]:
        if ctx.fund and ctx.fund == compare_to_fund:
            clarifications.append("comparison needs a different fund than the active context")

    interpreted_metric_id = metric["metric_id"] if metric else None
    if interpreted_metric_id == "manager_excess_return_pp" and not (resolved["manager"] or "manager" in normalized_query or "managers" in normalized_query):
        interpreted_metric_id = "fund_excess_return_pp"

    return {
        "query": query,
        "resolved_context": asdict(ctx),
        "explicit_entities": {key: value for key, value in entities.items() if value},
        "interpretation": {
            **resolved,
            "metric_id": interpreted_metric_id,
            "operator": metric.get("operator") if metric else None,
            "threshold": metric.get("threshold") if metric else None,
            "compare_to_fund": compare_to_fund,
        },
        "metric_intent": metric,
        "context_used": {
            field: getattr(ctx, field)
            for field in ("fund", "period", "asset_class", "manager", "research_signal_id")
            if getattr(ctx, field) and not entities.get(field)
        },
        "conflicts": conflicts,
        "clarifications": clarifications,
        "ready_for_tool_loop": not clarifications,
    }
