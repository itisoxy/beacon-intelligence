from __future__ import annotations

from typing import Any


PERIOD_QUARTERS = {
    "Q1": ["Q1"],
    "Q2": ["Q2"],
    "Q3": ["Q3"],
    "Q4": ["Q4"],
    "FY2026": ["Q1", "Q2", "Q3", "Q4"],
    "H1 FY2026": ["Q1", "Q2"],
    "H2 FY2026": ["Q3", "Q4"],
}


RANKING_METRICS = {
    "absolute return": "manager_return_pct",
    "return": "manager_return_pct",
    "excess return": "manager_excess_return_pp",
    "excess": "manager_excess_return_pp",
    "consistency": "manager_consistency",
}


TOOL_SCHEMAS = [
    {
        "name": "get_fund_summary",
        "description": "Return fund-level AUM, return, benchmark, excess return, cash flow and gain/loss for a period.",
        "parameters": {"fund": "BPT | BLE | All", "period": "Q1 | Q2 | Q3 | Q4 | FY2026 | H1 FY2026 | H2 FY2026"},
    },
    {
        "name": "get_asset_allocation",
        "description": "Return allocation metrics for one fund, period and asset class.",
        "parameters": {"fund": "BPT | BLE | All", "period": "Q1 | Q2 | Q3 | Q4 | FY2026 | H1 FY2026 | H2 FY2026", "asset_class": "dataset asset class"},
    },
    {
        "name": "get_allocation_history",
        "description": "Return Q1-Q4 actual, target and drift history for one fund and asset class.",
        "parameters": {"fund": "BPT | BLE | All", "asset_class": "dataset asset class"},
    },
    {
        "name": "get_manager_performance",
        "description": "Return manager performance rows filtered by manager, fund, period and/or asset class.",
        "parameters": {"manager": "optional manager name", "fund": "optional fund", "period": "optional period", "asset_class": "optional asset class"},
    },
    {
        "name": "rank_managers",
        "description": "Rank managers by absolute return, excess return, or consistency.",
        "parameters": {"period": "required period", "metric": "absolute return | excess return | consistency", "direction": "asc | desc", "fund": "optional fund", "asset_class": "optional asset class", "limit": "optional integer"},
    },
    {
        "name": "get_manager_history",
        "description": "Return Q1-Q4 manager return, benchmark and excess history.",
        "parameters": {"manager": "manager name", "fund": "optional fund"},
    },
    {
        "name": "get_cash_flows",
        "description": "Return cash-flow details and net cash flow for a fund and period.",
        "parameters": {"fund": "BPT | BLE | All", "period": "Q1 | Q2 | Q3 | Q4 | FY2026 | H1 FY2026 | H2 FY2026"},
    },
    {
        "name": "compare_funds",
        "description": "Compare BPT and BLE for a metric and period, optionally for an asset class.",
        "parameters": {"metric": "metric id or ranking metric alias", "period": "required period", "asset_class": "optional asset class"},
    },
    {
        "name": "compare_periods",
        "description": "Compare a metric between two periods for a fund, asset class, or manager entity.",
        "parameters": {"entity": "fund | asset_class | manager", "metric": "metric id or ranking metric alias", "period_a": "period", "period_b": "period", "fund": "optional fund"},
    },
    {
        "name": "get_research_signals",
        "description": "Return deterministic research signals filtered by fund, period, asset class, or manager.",
        "parameters": {"fund": "optional fund", "period": "optional period", "asset_class": "optional asset class", "manager": "optional manager"},
    },
    {
        "name": "validate_reconciliation",
        "description": "Return fund roll-forward and allocation validation status for a fund and period.",
        "parameters": {"fund": "BPT | BLE", "period": "Q1 | Q2 | Q3 | Q4"},
    },
    {
        "name": "get_source_record",
        "description": "Return compact lineage for a normalized record id or source record id.",
        "parameters": {"record_id": "canonical record_id, metric_value_id, signal id, or source_record_id"},
    },
]


def tool_schemas() -> list[dict[str, Any]]:
    return TOOL_SCHEMAS


class BeaconBusinessTools:
    def __init__(self, model: dict[str, Any]):
        self.model = model

    def get_fund_summary(self, fund: str, period: str) -> dict[str, Any]:
        error = self._validate_fund(fund) or self._validate_period(period)
        if error:
            return self._error("get_fund_summary", {"fund": fund, "period": period}, **error)
        metrics = {
            "aum": self._metric("ending_aum", fund_id=fund, period=period),
            "return": self._metric("fund_return_pct", fund_id=fund, period=period),
            "policy_benchmark": self._metric("policy_benchmark_return_pct", fund_id=fund, period=period),
            "excess_return": self._metric("fund_excess_return_pp", fund_id=fund, period=period),
            "net_cash_flow": self._metric("net_cash_flow", fund_id=fund, period=period),
            "gain_loss": self._metric("investment_gain_loss", fund_id=fund, period=period),
        }
        return self._result("get_fund_summary", {"fund": fund, "period": period}, metrics=metrics)

    def get_asset_allocation(self, fund: str, period: str, asset_class: str) -> dict[str, Any]:
        error = self._validate_fund(fund) or self._validate_period(period) or self._validate_asset(asset_class)
        if error:
            return self._error("get_asset_allocation", {"fund": fund, "period": period, "asset_class": asset_class}, **error)
        row = self._allocation_row(fund, period, asset_class)
        if row is None:
            return self._error("get_asset_allocation", {"fund": fund, "period": period, "asset_class": asset_class}, "no_data", "No allocation record matched the requested fund, period and asset class.")
        metrics = {
            "market_value": self._canonical_value(row, "ending_market_value", "USD millions"),
            "actual_allocation": self._metric("actual_allocation_pct", fund_id=fund, period=period, asset_class=asset_class),
            "policy_target": self._metric("policy_target_pct", fund_id=fund, period=period, asset_class=asset_class),
            "drift_pp": self._metric("allocation_drift_pp", fund_id=fund, period=period, asset_class=asset_class),
            "dollar_variance": self._metric("dollar_variance_to_policy", fund_id=fund, period=period, asset_class=asset_class),
            "allocation_validation": self._metric("allocation_validation_status", fund_id=fund, period=self._snapshot_period(period)),
        }
        drift = metrics["drift_pp"]["value"]
        status = "Near policy" if abs(float(drift or 0)) < 0.75 else "Overweight" if float(drift or 0) > 0 else "Underweight"
        return self._result("get_asset_allocation", {"fund": fund, "period": period, "asset_class": asset_class}, metrics=metrics, status=status)

    def get_allocation_history(self, fund: str, asset_class: str) -> dict[str, Any]:
        error = self._validate_fund(fund) or self._validate_asset(asset_class)
        if error:
            return self._error("get_allocation_history", {"fund": fund, "asset_class": asset_class}, **error)
        rows = []
        for period in ["Q1", "Q2", "Q3", "Q4"]:
            rows.append(
                {
                    "period": period,
                    "actual_allocation": self._metric("actual_allocation_pct", fund_id=fund, period=period, asset_class=asset_class),
                    "policy_target": self._metric("policy_target_pct", fund_id=fund, period=period, asset_class=asset_class),
                    "drift_pp": self._metric("allocation_drift_pp", fund_id=fund, period=period, asset_class=asset_class),
                }
            )
        return self._result("get_allocation_history", {"fund": fund, "asset_class": asset_class}, rows=rows)

    def get_manager_performance(
        self,
        manager: str | None = None,
        fund: str | None = None,
        period: str | None = None,
        asset_class: str | None = None,
    ) -> dict[str, Any]:
        error = self._validate_fund(fund) or self._validate_period(period, optional=True) or self._validate_asset(asset_class, optional=True) or self._validate_manager(manager, optional=True)
        if error:
            return self._error("get_manager_performance", {"manager": manager, "fund": fund, "period": period, "asset_class": asset_class}, **error)
        rows = self._manager_entities(manager=manager, fund=fund, period=period, asset_class=asset_class)
        if not rows:
            return self._error("get_manager_performance", {"manager": manager, "fund": fund, "period": period, "asset_class": asset_class}, "no_data", "No manager performance records matched the requested filters.")
        return self._result("get_manager_performance", {"manager": manager, "fund": fund, "period": period, "asset_class": asset_class}, rows=rows)

    def rank_managers(
        self,
        period: str,
        metric: str,
        direction: str,
        fund: str | None = None,
        asset_class: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        error = self._validate_period(period) or self._validate_fund(fund) or self._validate_asset(asset_class, optional=True)
        if error:
            return self._error("rank_managers", {"period": period, "metric": metric, "direction": direction, "fund": fund, "asset_class": asset_class, "limit": limit}, **error)
        try:
            metric_id = self._ranking_metric(metric)
        except ValueError as exc:
            return self._error("rank_managers", {"period": period, "metric": metric, "direction": direction, "fund": fund, "asset_class": asset_class, "limit": limit}, "unsupported_metric", str(exc), field="metric", value=metric)
        rows = self._find_metrics(metric_id, period=period, fund_id=fund, asset_class=asset_class)
        rows = [row for row in rows if row.get("manager_name")]
        if not rows:
            return self._error("rank_managers", {"period": period, "metric": metric, "direction": direction, "fund": fund, "asset_class": asset_class, "limit": limit}, "no_data", "No manager ranking records matched the requested filters.")
        reverse = direction.lower() in {"desc", "descending", "highest"}
        rows = sorted(rows, key=lambda row: float(row.get("value") or 0), reverse=reverse)
        if limit is not None:
            rows = rows[: int(limit)]
        ranked = [
            {
                "rank": idx + 1,
                "manager": row["manager_name"],
                "fund": row["fund_id"],
                "asset_class": row["asset_class"],
                "metric": self._metric_from_row(row),
            }
            for idx, row in enumerate(rows)
        ]
        return self._result("rank_managers", {"period": period, "metric": metric, "direction": direction, "fund": fund, "asset_class": asset_class, "limit": limit}, rows=ranked)

    def get_manager_history(self, manager: str, fund: str | None = None) -> dict[str, Any]:
        error = self._validate_manager(manager) or self._validate_fund(fund)
        if error:
            return self._error("get_manager_history", {"manager": manager, "fund": fund}, **error)
        rows = []
        for period in ["Q1", "Q2", "Q3", "Q4"]:
            for perf in self._manager_entities(manager=manager, fund=fund, period=period):
                rows.append(perf)
        return self._result("get_manager_history", {"manager": manager, "fund": fund}, rows=rows)

    def get_cash_flows(self, fund: str, period: str) -> dict[str, Any]:
        error = self._validate_fund(fund) or self._validate_period(period)
        if error:
            return self._error("get_cash_flows", {"fund": fund, "period": period}, **error)
        quarters = self._quarters(period)
        details = []
        for row in self.model["canonical"]["cash_flows"]:
            if row["fund_id"] == fund and row["quarter"] in quarters:
                details.append(
                    {
                        "record_id": row["record_id"],
                        "quarter": row["quarter"],
                        "flow_type": row["flow_type"],
                        "amount": self._canonical_value(row, "amount", "USD millions"),
                    }
                )
        return self._result(
            "get_cash_flows",
            {"fund": fund, "period": period},
            metrics={"net_cash_flow": self._metric("net_cash_flow", fund_id=fund, period=period)},
            rows=details,
        )

    def compare_funds(self, metric: str, period: str, asset_class: str | None = None) -> dict[str, Any]:
        metric_id = self._metric_alias(metric)
        error = self._validate_period(period) or self._validate_asset(asset_class, optional=True) or self._validate_metric(metric_id)
        if error:
            return self._error("compare_funds", {"metric": metric, "period": period, "asset_class": asset_class}, **error)
        rows = []
        for fund in ["BPT", "BLE"]:
            filters = {"fund_id": fund, "period": period}
            if asset_class:
                filters["asset_class"] = asset_class
            rows.append({"fund": fund, "metric": self._metric(metric_id, **filters)})
        delta = None
        if all(row["metric"]["value"] is not None for row in rows):
            delta = rows[0]["metric"]["value"] - rows[1]["metric"]["value"]
        elif all(row["metric"]["support_status"] == "not_available" for row in rows):
            return self._error("compare_funds", {"metric": metric, "period": period, "asset_class": asset_class}, "no_data", "No metric values matched the requested fund comparison.")
        return self._result("compare_funds", {"metric": metric, "period": period, "asset_class": asset_class}, rows=rows, comparison={"bpt_minus_ble": delta, "unit": rows[0]["metric"]["unit"]})

    def compare_periods(self, entity: str, metric: str, period_a: str, period_b: str, fund: str | None = None) -> dict[str, Any]:
        metric_id = self._metric_alias(metric)
        error = self._validate_period(period_a) or self._validate_period(period_b) or self._validate_fund(fund) or self._validate_metric(metric_id)
        if error:
            return self._error("compare_periods", {"entity": entity, "metric": metric, "period_a": period_a, "period_b": period_b, "fund": fund}, **error)
        entity_filters = self._entity_filters(entity)
        if entity_filters is None:
            return self._error("compare_periods", {"entity": entity, "metric": metric, "period_a": period_a, "period_b": period_b, "fund": fund}, "unknown_entity", "The comparison entity is not a known fund, asset class or manager.", field="entity", value=entity)
        filters = {"fund_id": fund} if fund else {}
        filters.update(entity_filters)
        a = self._metric(metric_id, period=period_a, **filters)
        b = self._metric(metric_id, period=period_b, **filters)
        if a["support_status"] == "not_available" or b["support_status"] == "not_available":
            return self._error("compare_periods", {"entity": entity, "metric": metric, "period_a": period_a, "period_b": period_b, "fund": fund}, "no_data", "No metric values matched one or both requested periods.")
        delta = None if a["value"] is None or b["value"] is None else b["value"] - a["value"]
        return self._result("compare_periods", {"entity": entity, "metric": metric, "period_a": period_a, "period_b": period_b, "fund": fund}, rows=[{"period": period_a, "metric": a}, {"period": period_b, "metric": b}], comparison={"period_b_minus_period_a": delta, "unit": a["unit"]})

    def get_research_signals(
        self,
        fund: str | None = None,
        period: str | None = None,
        asset_class: str | None = None,
        manager: str | None = None,
    ) -> dict[str, Any]:
        error = self._validate_fund(fund) or self._validate_period(period, optional=True) or self._validate_asset(asset_class, optional=True) or self._validate_manager(manager, optional=True)
        if error:
            return self._error("get_research_signals", {"fund": fund, "period": period, "asset_class": asset_class, "manager": manager}, **error)
        source = self.model["research"]["horizons"].get(period, self.model["research"]).get("candidates", [])
        rows = []
        for signal in source:
            if fund and signal.get("fund") not in {fund, "All"}:
                continue
            if asset_class and signal.get("asset_class") not in {asset_class, None}:
                continue
            if manager and signal.get("manager") != manager:
                continue
            rows.append(
                {
                    "signal_id": signal["id"],
                    "type": signal["type"],
                    "horizon": signal["horizon"],
                    "headline": signal["headline"],
                    "primary_metric": signal["primary_metric"],
                    "primary_value": signal["primary_value"],
                    "provenance": self._provenance(signal),
                }
            )
        if not rows:
            return self._error("get_research_signals", {"fund": fund, "period": period, "asset_class": asset_class, "manager": manager}, "no_data", "No research signals matched the requested filters.")
        return self._result("get_research_signals", {"fund": fund, "period": period, "asset_class": asset_class, "manager": manager}, rows=rows)

    def validate_reconciliation(self, fund: str, period: str) -> dict[str, Any]:
        error = self._validate_fund(fund, allow_all=False) or self._validate_period(period)
        if error:
            return self._error("validate_reconciliation", {"fund": fund, "period": period}, **error)
        validations = [
            row for row in self.model["audit"]["validations"]
            if row.get("fund") == fund and row.get("period") == period and row.get("type") in {"fund_roll_forward", "allocation_total"}
        ]
        if not validations:
            return self._error("validate_reconciliation", {"fund": fund, "period": period}, "no_data", "No reconciliation validation matched the requested fund and period.")
        return self._result(
            "validate_reconciliation",
            {"fund": fund, "period": period},
            metrics={
                "reconciliation_variance": self._metric("reconciliation_variance", fund_id=fund, period=period),
                "allocation_validation": self._metric("allocation_validation_status", fund_id=fund, period=period),
            },
            rows=validations,
        )

    def get_source_record(self, record_id: str) -> dict[str, Any]:
        for row in self.model["metric_values"]:
            if row["metric_value_id"] == record_id:
                return self._result("get_source_record", {"record_id": record_id}, record=self._metric_from_row(row))
        for table_name, rows in self.model["canonical"].items():
            for row in rows:
                if row.get("record_id") == record_id or row.get("source_record_id") == record_id:
                    return self._result(
                        "get_source_record",
                        {"record_id": record_id},
                        record={
                            "table": table_name,
                            "record_id": row.get("record_id"),
                            "source_record_id": row.get("source_record_id"),
                            "fund": row.get("fund_id"),
                            "period": row.get("reporting_period") or row.get("quarter"),
                            "asset_class": row.get("asset_class"),
                            "manager": row.get("manager_name"),
                            "provenance": self._provenance(row),
                        },
                    )
        for horizon in self.model["research"]["horizons"].values():
            for signal in horizon["candidates"]:
                if signal["id"] == record_id:
                    return self._result("get_source_record", {"record_id": record_id}, record={"signal_id": signal["id"], "headline": signal["headline"], "provenance": self._provenance(signal)})
        return self._error("get_source_record", {"record_id": record_id}, "no_data", "No source, metric, canonical or research record matched the requested ID.", field="record_id", value=record_id)

    def _result(self, tool: str, arguments: dict[str, Any], **payload: Any) -> dict[str, Any]:
        return {"ok": True, "tool": tool, "arguments": arguments, **payload}

    def _error(self, tool: str, arguments: dict[str, Any], code: str, message: str, field: str | None = None, value: Any = None) -> dict[str, Any]:
        return {
            "ok": False,
            "tool": tool,
            "arguments": arguments,
            "error": {"code": code, "message": message, "field": field, "value": value},
        }

    def _metric_alias(self, metric: str) -> str:
        return RANKING_METRICS.get(metric.lower(), metric)

    def _ranking_metric(self, metric: str) -> str:
        metric_id = self._metric_alias(metric)
        if metric_id not in {"manager_return_pct", "manager_excess_return_pp", "manager_consistency"}:
            raise ValueError("Ranking metric must be absolute return, excess return, or consistency.")
        return metric_id

    def _known_funds(self, allow_all: bool = True) -> set[str]:
        funds = {row["FundCode"] for row in self.model["dimensions"]["funds"]}
        if allow_all:
            funds.add("All")
        return funds

    def _known_metrics(self) -> set[str]:
        return {row["metric_id"] for row in self.model["metric_registry"]}

    def _validate_fund(self, fund: str | None, allow_all: bool = True) -> dict[str, Any] | None:
        if fund is None:
            return None
        if fund not in self._known_funds(allow_all=allow_all):
            return {"code": "unknown_entity", "message": "Unknown fund.", "field": "fund", "value": fund}
        return None

    def _validate_asset(self, asset_class: str | None, optional: bool = False) -> dict[str, Any] | None:
        if asset_class is None and optional:
            return None
        if asset_class not in set(self.model["dimensions"]["asset_classes"]):
            return {"code": "unknown_entity", "message": "Unknown asset class.", "field": "asset_class", "value": asset_class}
        return None

    def _validate_manager(self, manager: str | None, optional: bool = False) -> dict[str, Any] | None:
        if manager is None and optional:
            return None
        if manager not in set(self.model["dimensions"]["managers"]):
            return {"code": "unknown_entity", "message": "Unknown manager.", "field": "manager", "value": manager}
        return None

    def _validate_period(self, period: str | None, optional: bool = False) -> dict[str, Any] | None:
        if period is None and optional:
            return None
        if period in PERIOD_QUARTERS:
            return None
        if period and period.upper().startswith("Q"):
            return {"code": "invalid_period", "message": "Quarter period must be Q1, Q2, Q3 or Q4.", "field": "period", "value": period}
        return {"code": "no_data", "message": "No data is available outside FY2026 periods.", "field": "period", "value": period}

    def _validate_metric(self, metric_id: str) -> dict[str, Any] | None:
        if metric_id not in self._known_metrics():
            return {"code": "unsupported_metric", "message": "Unsupported metric for Beacon business tools.", "field": "metric", "value": metric_id}
        return None

    def _snapshot_period(self, period: str) -> str:
        if period == "FY2026" or period == "H2 FY2026":
            return "Q4"
        if period == "H1 FY2026":
            return "Q2"
        return period

    def _quarters(self, period: str) -> list[str]:
        if period not in PERIOD_QUARTERS:
            raise ValueError(f"Unsupported period: {period}")
        return PERIOD_QUARTERS[period]

    def _find_metrics(self, metric_id: str, **filters: Any) -> list[dict[str, Any]]:
        rows = []
        for row in self.model["metric_values"]:
            if row["metric_id"] != metric_id:
                continue
            if all(value is None or row.get(key) == value for key, value in filters.items()):
                rows.append(row)
        return rows

    def _metric(self, metric_id: str, **filters: Any) -> dict[str, Any]:
        rows = self._find_metrics(metric_id, **filters)
        if not rows:
            return {"record_id": None, "value": None, "unit": None, "provenance": {}, "support_status": "not_available"}
        return self._metric_from_row(rows[0])

    def _metric_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "record_id": row["metric_value_id"],
            "metric_id": row["metric_id"],
            "value": row.get("value"),
            "value_text": row.get("value_text"),
            "value_path": row.get("value_path"),
            "unit": row.get("unit"),
            "calculation_method": row.get("calculation_method"),
            "support_status": row.get("support_status"),
            "provenance": self._provenance(row),
        }

    def _provenance(self, row: dict[str, Any]) -> dict[str, Any]:
        source_record_ids = row.get("source_record_ids") or ([row["source_record_id"]] if row.get("source_record_id") else [])
        source_files = row.get("source_files") or ([row["source_file"]] if row.get("source_file") else [])
        source_sheets = row.get("source_sheets") or ([row["source_sheet"]] if row.get("source_sheet") else [])
        source_rows = row.get("source_rows") or ([row["source_row"]] if row.get("source_row") else [])
        cells = row.get("source_cells", [])
        source_cells = cells if isinstance(cells, list) else ([cells] if cells else [])
        return {
            "source_record_ids": source_record_ids,
            "source_file": row.get("source_file"),
            "source_files": source_files,
            "source_sheet": row.get("source_sheet"),
            "source_sheets": source_sheets,
            "source_row": row.get("source_row"),
            "source_rows": source_rows,
            "source_cells": source_cells,
        }

    def _canonical_value(self, row: dict[str, Any], field: str, unit: str) -> dict[str, Any]:
        return {
            "record_id": row.get("record_id"),
            "metric_id": field,
            "value": row.get(field),
            "unit": unit,
            "support_status": "supported",
            "provenance": self._provenance(row),
        }

    def _allocation_row(self, fund: str, period: str, asset_class: str) -> dict[str, Any] | None:
        snapshot = self._snapshot_period(period)
        for row in self.model["canonical"]["asset_allocations"]:
            if row["fund_id"] == fund and row["quarter"] == snapshot and row["asset_class"] == asset_class:
                return row
        return None

    def _manager_entities(
        self,
        manager: str | None = None,
        fund: str | None = None,
        period: str | None = None,
        asset_class: str | None = None,
    ) -> list[dict[str, Any]]:
        metric_period = period or "FY2026"
        metric_rows = self._find_metrics("manager_excess_return_pp", period=metric_period, fund_id=fund, asset_class=asset_class)
        if manager:
            metric_rows = [row for row in metric_rows if row.get("manager_name") == manager]
        rows = []
        for row in metric_rows:
            args = {
                "fund_id": row["fund_id"],
                "period": metric_period,
                "asset_class": row["asset_class"],
                "manager_name": row["manager_name"],
            }
            rows.append(
                {
                    "manager": row["manager_name"],
                    "fund": row["fund_id"],
                    "period": metric_period,
                    "asset_class": row["asset_class"],
                    "return": self._metric("manager_return_pct", **args),
                    "benchmark": self._metric("manager_benchmark_return_pct", **args),
                    "excess_return": self._metric_from_row(row),
                    "consistency": self._metric("manager_consistency", **args),
                }
            )
        return rows

    def _entity_filters(self, entity: str) -> dict[str, Any] | None:
        if entity in {"fund", "portfolio"}:
            return {}
        asset_classes = set(self.model["dimensions"]["asset_classes"])
        managers = set(self.model["dimensions"]["managers"])
        if entity in asset_classes:
            return {"asset_class": entity}
        if entity in managers:
            return {"manager_name": entity}
        if entity in {"BPT", "BLE", "All"}:
            return {"fund_id": entity}
        return None
