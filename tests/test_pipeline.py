from pathlib import Path

import duckdb
import pytest

from beacon_data import AskBeaconAgent, AskBeaconService, ModelResponse, ScriptedModelAdapter, ToolCall, build_model
from beacon_data.business_tools import BeaconBusinessTools, tool_schemas
from beacon_data.semantic import AskBeaconContext, interpret_query


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "Data"


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("beacon-output")
    return build_model(DATA_DIR, tmp, tmp / "beacon.duckdb")


def validations(model, validation_type):
    return [row for row in model["audit"]["validations"] if row["type"] == validation_type]


def test_readme_and_source_files_loaded(model):
    assert len(model["files"]) == 4
    for book in model["files"]:
        assert book["readme"]
        assert any("Ending MV = Beginning MV + Net Cash Flow + Investment Gain" in line for line in book["readme"])
        assert {sheet["name"] for sheet in book["sheets"]} >= {
            "ReadMe",
            "Fund_Summary",
            "Asset_Allocation",
            "Manager_Detail",
            "Cash_Flow_Detail",
            "Benchmarks_Reference",
            "RAW_Export_Extract",
        }


def test_aum_roll_forward_validates(model):
    rows = validations(model, "fund_roll_forward")
    assert len(rows) == 8
    assert all(row["status"] == "pass" for row in rows)


def test_allocation_totals_validate(model):
    rows = validations(model, "allocation_total")
    assert len(rows) == 8
    assert all(row["status"] == "pass" for row in rows)


def test_manager_rollups_validate(model):
    rows = validations(model, "manager_rollup")
    assert len(rows) == 72
    assert all(row["status"] == "pass" for row in rows)


def test_benchmark_mappings_validate(model):
    rows = validations(model, "benchmark_mapping")
    assert len(rows) == len(model["dimensions"]["asset_classes"])
    assert all(row["status"] == "pass" for row in rows)


def test_duplicates_nulls_are_recorded(model):
    rows = validations(model, "duplicates_nulls")
    assert {row["sheet"] for row in rows} >= {
        "Fund_Summary",
        "Asset_Allocation",
        "Manager_Detail",
        "Cash_Flow_Detail",
        "Benchmarks_Reference",
    }
    assert all(row["duplicate_count"] == 0 for row in rows)
    assert all(row["status"] in {"pass", "warn"} for row in rows)


def test_cross_quarter_continuity_validates(model):
    rows = validations(model, "cross_quarter_continuity")
    assert len(rows) == 6
    assert all(row["status"] == "pass" for row in rows)


def test_provenance_is_preserved(model):
    sample = model["records"]["manager_detail"][0]
    provenance = sample["_provenance"]
    assert provenance["source_file"].endswith("_FYTD.xlsx")
    assert provenance["source_sheet"] == "Manager_Detail"
    assert provenance["source_row"] >= 2
    assert "source_record_id" in sample


def test_duckdb_store_contains_analytical_views(model):
    store = Path(model["audit"]["duckdb_store"])
    assert store.exists()
    con = duckdb.connect(str(store), read_only=True)
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        assert {"fund_summary_view", "asset_allocation_view", "manager_performance_view"} <= tables
        assert {"metric_registry", "metric_values"} <= tables
        assert {
            "canonical_funds",
            "canonical_reporting_periods",
            "canonical_fund_performance",
            "canonical_asset_allocations",
            "canonical_managers",
            "canonical_manager_performance",
            "canonical_cash_flows",
            "canonical_benchmarks",
        } <= tables
        assert con.execute("SELECT count(*) FROM fund_summary_view").fetchone()[0] == 12
    finally:
        con.close()


def test_research_candidates_are_evidence_backed(model):
    candidates = model["research"]["candidates"]
    assert 8 <= len(candidates) <= 10
    assert len(model["research"]["final_signals"]) == 5
    for signal in candidates:
        assert signal["source_record_ids"]
        assert signal["source_files"]
        assert signal["source_sheets"]
        assert signal["limitations"]
        assert signal["observation"]
        assert signal["interpretation"]


def test_research_horizons_are_generated(model):
    horizons = model["research"]["horizons"]
    assert set(horizons) == {"FY2026", "H1 FY2026", "H2 FY2026", "Q1", "Q2", "Q3", "Q4"}
    for horizon, payload in horizons.items():
        assert len(payload["candidates"]) >= 8
        assert len(payload["final_signals"]) == 5
        assert payload["summary"]
        assert all(signal["horizon"] == horizon for signal in payload["candidates"])


def test_h2_returns_are_linked_from_qtd_not_fytd_subtracted(model):
    q3 = next(row for row in model["analytics"]["fund_summary_view"] if row["FundCode"] == "BPT" and row["Quarter"] == "Q3")
    q4 = next(row for row in model["analytics"]["fund_summary_view"] if row["FundCode"] == "BPT" and row["Quarter"] == "Q4")
    h2 = next(row for row in model["analytics"]["fund_horizon_h2_fy2026"] if row["FundCode"] == "BPT")
    linked = ((1 + q3["QTDReturnPct"] / 100) * (1 + q4["QTDReturnPct"] / 100) - 1) * 100
    invalid_subtraction = q4["FYTDReturnPct"] - q3["FYTDReturnPct"]
    assert abs(h2["HorizonReturnPct"] - linked) < 1e-9
    assert abs(h2["HorizonReturnPct"] - invalid_subtraction) > 0.01


def test_h1_returns_are_linked_from_qtd_not_fytd_subtracted(model):
    q1 = next(row for row in model["analytics"]["fund_summary_view"] if row["FundCode"] == "BPT" and row["Quarter"] == "Q1")
    q2 = next(row for row in model["analytics"]["fund_summary_view"] if row["FundCode"] == "BPT" and row["Quarter"] == "Q2")
    h1 = next(row for row in model["analytics"]["fund_horizon_h1_fy2026"] if row["FundCode"] == "BPT")
    linked = ((1 + q1["QTDReturnPct"] / 100) * (1 + q2["QTDReturnPct"] / 100) - 1) * 100
    invalid_subtraction = q2["FYTDReturnPct"] - q1["FYTDReturnPct"]
    assert abs(h1["HorizonReturnPct"] - linked) < 1e-9
    assert abs(h1["HorizonReturnPct"] - invalid_subtraction) > 0.01


def test_horizon_comparisons_are_available(model):
    comparisons = model["research"]["comparisons"]
    assert comparisons["q4_vs_q3"]["allocation"]
    assert comparisons["q4_vs_q3"]["manager"]
    assert comparisons["h2_vs_h1"]["fund"]
    assert comparisons["h2_vs_h1"]["manager"]
    assert comparisons["q1_to_q4_trajectory"]["allocation"]
    assert comparisons["q1_to_q4_trajectory"]["manager"]


def test_research_uses_reporting_values_for_relative_performance(model):
    fund_view = {
        (row["FundCode"], row["Quarter"]): row
        for row in model["analytics"]["fund_summary_view"]
    }
    for signal in model["research"]["candidates"]:
        if signal["type"] != "relative_performance":
            continue
        row = fund_view[(signal["fund"], "Q4")]
        assert signal["supporting_metrics"]["fund_return_pct"] == row["FYTDReturnPct"]
        assert signal["supporting_metrics"]["policy_benchmark_pct"] == row["PolicyBenchmarkFYTDReturnPct"]
        expected = row["FYTDReturnPct"] - row["PolicyBenchmarkFYTDReturnPct"]
        assert abs(signal["primary_value"] - expected) < 1e-9


def test_quarter_research_uses_qtd_reporting_values(model):
    q4_signals = model["research"]["horizons"]["Q4"]["candidates"]
    fund_view = {
        (row["FundCode"], row["Quarter"]): row
        for row in model["analytics"]["fund_summary_view"]
    }
    for signal in q4_signals:
        if signal["type"] != "relative_performance":
            continue
        row = fund_view[(signal["fund"], "Q4")]
        assert signal["supporting_metrics"]["fund_return_pct"] == row["QTDReturnPct"]
        assert signal["supporting_metrics"]["policy_benchmark_pct"] == row["PolicyBenchmarkQTDReturnPct"]


def test_research_uses_reporting_values_for_policy_drift(model):
    allocation = {
        (row["FundCode"], row["Quarter"], row["AssetClassLevel1"]): row
        for row in model["analytics"]["asset_allocation_view"]
    }
    for signal in model["research"]["candidates"]:
        if signal["type"] != "policy_drift":
            continue
        row = allocation[(signal["fund"], "Q4", signal["asset_class"])]
        assert signal["supporting_metrics"]["q4_actual_pct"] == row["PctOfFundTotal"]
        assert signal["supporting_metrics"]["policy_target_pct"] == row["PolicyTargetPct"]
        assert signal["primary_value"] == row["VarianceToTargetPct"]


def test_research_uses_reporting_values_for_manager_excess(model):
    manager_view = {
        (row["FundCode"], row["Quarter"], row["ManagerName"], row["AssetClassLevel1"]): row
        for row in model["analytics"]["manager_performance_view"]
    }
    for signal in model["research"]["candidates"]:
        if signal["type"] not in {"manager_consistency", "emerging_signal"} or not signal["manager"]:
            continue
        row = manager_view[(signal["fund"], "Q4", signal["manager"], signal["asset_class"])]
        if "fy_excess_pp" in signal["supporting_metrics"]:
            assert signal["supporting_metrics"]["fy_excess_pp"] == row["ExcessFYTDReturnPp"]


def test_canonical_domain_tables_exist(model):
    canonical = model["canonical"]
    assert set(canonical) == {
        "funds",
        "reporting_periods",
        "fund_performance",
        "asset_allocations",
        "managers",
        "manager_performance",
        "cash_flows",
        "benchmarks",
    }
    assert len(canonical["funds"]) == 2
    assert len(canonical["reporting_periods"]) == 6
    assert len(canonical["fund_performance"]) == 12
    assert len(canonical["asset_allocations"]) == 108
    assert len(canonical["manager_performance"]) == 136
    assert len(canonical["cash_flows"]) == 40
    assert len(canonical["benchmarks"]) == 9


def test_canonical_provenance_and_fytd_classification(model):
    alloc = next(row for row in model["canonical"]["asset_allocations"] if row["fund_id"] == "BPT" and row["quarter"] == "Q4" and row["asset_class"] == "Cash")
    assert alloc["record_id"]
    assert alloc["source_file"] == "20260630_FYTD.xlsx"
    assert alloc["source_sheet"] == "Asset_Allocation"
    assert isinstance(alloc["source_row"], int)
    assert alloc["source_cells"].startswith("A")
    assert alloc["source_cells"].endswith(f"S{alloc['source_row']}")
    assert alloc["fiscal_year"] == "FY2026"
    assert alloc["snapshot_measure"] is True
    assert alloc["fytd_cumulative_return"] is True
    assert alloc["quarter_only_return_source_supported"] is True


def test_canonical_fund_performance_matches_reporting_analytics(model):
    reporting = {
        (row["FundCode"], row["Quarter"]): row
        for row in model["analytics"]["fund_summary_view"]
    }
    canonical = {
        (row["fund_id"], row["quarter"]): row
        for row in model["canonical"]["fund_performance"]
    }
    key = ("BPT", "Q4")
    assert canonical[key]["ending_aum"] == reporting[key]["EndingMarketValue"]
    assert canonical[key]["fund_return_pct"] == reporting[key]["FYTDReturnPct"]
    assert canonical[key]["policy_benchmark_return_pct"] == reporting[key]["PolicyBenchmarkFYTDReturnPct"]
    assert canonical[key]["net_cash_flow"] == reporting[key]["NetCashFlow"]
    assert canonical[key]["investment_gain_loss"] == reporting[key]["InvestmentGainLoss"]


def test_canonical_allocation_matches_reporting_analytics(model):
    reporting = {
        (row["FundCode"], row["Quarter"], row["AssetClassLevel1"]): row
        for row in model["analytics"]["asset_allocation_view"]
    }
    canonical = {
        (row["fund_id"], row["quarter"], row["asset_class"]): row
        for row in model["canonical"]["asset_allocations"]
    }
    key = ("BPT", "Q4", "Cash")
    assert canonical[key]["actual_allocation_pct"] == reporting[key]["PctOfFundTotal"]
    assert canonical[key]["policy_target_pct"] == reporting[key]["PolicyTargetPct"]
    assert canonical[key]["allocation_drift_pp"] == reporting[key]["VarianceToTargetPct"]
    assert canonical[key]["dollar_variance"] == reporting[key]["DollarVariance"]


def test_canonical_manager_performance_matches_reporting_analytics(model):
    reporting = {
        (row["FundCode"], row["Quarter"], row["ManagerName"], row["AssetClassLevel1"]): row
        for row in model["analytics"]["manager_performance_view"]
    }
    canonical = {
        (row["fund_id"], row["quarter"], row["manager_name"], row["asset_class"]): row
        for row in model["canonical"]["manager_performance"]
    }
    key = ("BPT", "Q4", "Compass Infrastructure Partners", "Real Assets")
    assert canonical[key]["manager_return_pct"] == reporting[key]["FYTDReturnPct"]
    assert canonical[key]["manager_benchmark_return_pct"] == reporting[key]["BenchmarkReturnPct"]
    assert canonical[key]["manager_excess_return_pp"] == reporting[key]["ExcessFYTDReturnPp"]


def metric(model, metric_id, **filters):
    for row in model["metric_values"]:
        if row["metric_id"] != metric_id:
            continue
        if all(row.get(key) == value for key, value in filters.items()):
            return row
    raise AssertionError(f"Metric not found: {metric_id} {filters}")


def test_metric_registry_contains_beacon_core_metrics(model):
    registry = {row["metric_id"]: row for row in model["metric_registry"]}
    expected = {
        "ending_aum",
        "aum_change_amount",
        "aum_change_pct",
        "fund_return_pct",
        "policy_benchmark_return_pct",
        "fund_excess_return_pp",
        "net_cash_flow",
        "investment_gain_loss",
        "actual_allocation_pct",
        "policy_target_pct",
        "allocation_drift_pp",
        "dollar_variance_to_policy",
        "manager_return_pct",
        "manager_benchmark_return_pct",
        "manager_excess_return_pp",
        "manager_consistency",
        "best_relative_manager",
        "worst_relative_manager",
        "reconciliation_variance",
        "allocation_validation_status",
    }
    assert set(registry) == expected
    assert all(row["calculation_owner"] == "Python" for row in registry.values())
    assert registry["allocation_drift_pp"]["unit"] == "percentage points"
    assert "positive = overweight" in registry["allocation_drift_pp"]["interpretation"]


def test_metric_layer_supports_required_periods(model):
    assert set(model["dimensions"]["metric_periods"]) == {"Q1", "Q2", "Q3", "Q4", "FY2026", "H1 FY2026", "H2 FY2026", "Q4 vs Q3", "Q1 -> Q4"}
    periods = {row["period"] for row in model["metric_values"]}
    assert {"Q1", "Q2", "Q3", "Q4", "FY2026", "H1 FY2026", "H2 FY2026", "Q4 vs Q3", "Q1 -> Q4"} <= periods


def test_portfolio_insights_and_metric_values_match_for_fund_excess(model):
    reporting = next(row for row in model["analytics"]["fund_summary_view"] if row["FundCode"] == "BPT" and row["Quarter"] == "Q4")
    insight = next(row for row in model["research"]["candidates"] if row["type"] == "relative_performance" and row["fund"] == "BPT")
    canonical_metric = metric(model, "fund_excess_return_pp", fund_id="BPT", period="FY2026")
    expected = reporting["FYTDReturnPct"] - reporting["PolicyBenchmarkFYTDReturnPct"]
    assert abs(canonical_metric["value"] - expected) < 1e-9
    assert abs(insight["primary_value"] - canonical_metric["value"]) < 1e-9


def test_portfolio_insights_and_metric_values_match_for_allocation_drift(model):
    reporting = next(row for row in model["analytics"]["asset_allocation_view"] if row["FundCode"] == "BPT" and row["Quarter"] == "Q4" and row["AssetClassLevel1"] == "Cash")
    insight = next(row for row in model["research"]["candidates"] if row["type"] == "policy_drift" and row["fund"] == "BPT" and row["asset_class"] == "Cash")
    canonical_metric = metric(model, "allocation_drift_pp", fund_id="BPT", period="FY2026", asset_class="Cash")
    assert canonical_metric["value"] == reporting["VarianceToTargetPct"]
    assert insight["primary_value"] == canonical_metric["value"]


def test_metric_values_preserve_provenance(model):
    row = metric(model, "manager_excess_return_pp", fund_id="BPT", period="FY2026", manager_name="Compass Infrastructure Partners", asset_class="Real Assets")
    assert row["source_record_ids"]
    assert row["source_files"] == ["20260630_FYTD.xlsx"]
    assert row["source_sheets"] == ["Manager_Detail"]
    assert row["source_rows"]
    assert row["source_cells"]


def test_metric_comparison_values_are_deterministic(model):
    q3 = next(row for row in model["analytics"]["manager_performance_view"] if row["FundCode"] == "BPT" and row["Quarter"] == "Q3" and row["ManagerName"] == "Compass Infrastructure Partners" and row["AssetClassLevel1"] == "Real Assets")
    q4 = next(row for row in model["analytics"]["manager_performance_view"] if row["FundCode"] == "BPT" and row["Quarter"] == "Q4" and row["ManagerName"] == "Compass Infrastructure Partners" and row["AssetClassLevel1"] == "Real Assets")
    comparison = metric(model, "manager_excess_return_pp", fund_id="BPT", period="Q4 vs Q3", manager_name="Compass Infrastructure Partners", asset_class="Real Assets")
    trajectory = metric(model, "manager_excess_return_pp", fund_id="BPT", period="Q1 -> Q4", manager_name="Compass Infrastructure Partners", asset_class="Real Assets")
    assert abs(comparison["value"] - (q4["ExcessQTDReturnPp"] - q3["ExcessQTDReturnPp"])) < 1e-9
    assert len(trajectory["value_path"]) == 4


def test_semantic_layer_uses_dataset_asset_classes(model):
    layer = model["semantic_layer"]
    assets = layer["entity_dictionary"]["asset_classes"]
    assert assets["pe"] == "Private Equity"
    assert assets["private equity"] == "Private Equity"
    assert assets["public equities"] == "Public Equity"
    assert set(assets.values()) <= set(model["dimensions"]["asset_classes"])


def test_semantic_layer_resolves_funds_and_metric_language(model):
    result = interpret_query("Did the pension trust underperform in the last six months?", model["semantic_layer"])
    assert result["interpretation"]["fund"] == "BPT"
    assert result["interpretation"]["period"] == "H2 FY2026"
    assert result["interpretation"]["metric_id"] == "fund_excess_return_pp"
    assert result["interpretation"]["operator"] == "<"
    assert result["interpretation"]["threshold"] == 0
    assert result["ready_for_tool_loop"] is True


def test_semantic_layer_resolves_asset_abbreviation_and_manager_excess(model):
    result = interpret_query("Which PE managers beat benchmark for BLE in Q4?", model["semantic_layer"])
    assert result["interpretation"]["fund"] == "BLE"
    assert result["interpretation"]["asset_class"] == "Private Equity"
    assert result["interpretation"]["period"] == "Q4"
    assert result["interpretation"]["metric_id"] == "manager_excess_return_pp"
    assert result["interpretation"]["operator"] == ">"


def test_recently_requires_context_when_period_is_missing(model):
    result = interpret_query("Has this got worse recently?", model["semantic_layer"])
    assert result["ready_for_tool_loop"] is False
    assert any("recently requires" in item for item in result["clarifications"])
    assert any("contextual request needs" in item for item in result["clarifications"])


def test_recently_uses_active_context_when_available(model):
    context = AskBeaconContext(fund="BPT", period="Q4", asset_class="Private Equity", source_page="insights", research_signal_id="SIG_002")
    result = interpret_query("Has this got worse recently?", model["semantic_layer"], context)
    assert result["interpretation"]["fund"] == "BPT"
    assert result["interpretation"]["period"] == "Q4"
    assert result["interpretation"]["asset_class"] == "Private Equity"
    assert result["interpretation"]["research_signal_id"] == "SIG_002"
    assert result["ready_for_tool_loop"] is True


def test_contextual_comparison_uses_explicit_fund_as_comparison_target(model):
    context = {"fund": "BPT", "period": "FY2026", "asset_class": "Private Equity", "source_page": "portfolio"}
    result = interpret_query("Compare this with BLE.", model["semantic_layer"], context)
    assert result["interpretation"]["fund"] == "BPT"
    assert result["interpretation"]["compare_to_fund"] == "BLE"
    assert result["conflicts"] == []
    assert result["context_used"]["asset_class"] == "Private Equity"


def test_explicit_language_overrides_conflicting_context(model):
    context = {"fund": "BPT", "period": "FY2026", "asset_class": "Private Equity"}
    result = interpret_query("Show endowment cash drift in Q3.", model["semantic_layer"], context)
    assert result["interpretation"]["fund"] == "BLE"
    assert result["interpretation"]["asset_class"] == "Cash"
    assert result["interpretation"]["period"] == "Q3"
    assert {item["field"] for item in result["conflicts"]} == {"fund", "period", "asset_class"}


def test_ambiguous_performance_words_require_clarification(model):
    result = interpret_query("Who was the best performer?", model["semantic_layer"], {"period": "FY2026"})
    assert result["ready_for_tool_loop"] is False
    assert any("require a metric definition" in item for item in result["clarifications"])


@pytest.fixture(scope="module")
def tools(model):
    return BeaconBusinessTools(model)


def test_business_tool_schemas_are_allowlisted(model):
    names = {schema["name"] for schema in tool_schemas()}
    assert names == {
        "get_fund_summary",
        "get_asset_allocation",
        "get_allocation_history",
        "get_manager_performance",
        "rank_managers",
        "get_manager_history",
        "get_cash_flows",
        "compare_funds",
        "compare_periods",
        "get_research_signals",
        "validate_reconciliation",
        "get_source_record",
    }
    assert {schema["name"] for schema in model["business_tools"]["schemas"]} == names


def test_tool_question_ble_private_equity_allocation_q3(tools):
    result = tools.get_asset_allocation("BLE", "Q3", "Private Equity")
    assert result["tool"] == "get_asset_allocation"
    assert result["metrics"]["actual_allocation"]["metric_id"] == "actual_allocation_pct"
    assert result["metrics"]["drift_pp"]["metric_id"] == "allocation_drift_pp"
    assert result["metrics"]["actual_allocation"]["value"] is not None
    assert result["metrics"]["actual_allocation"]["provenance"]["source_files"]
    assert result["metrics"]["market_value"]["provenance"]["source_record_ids"]


def test_tool_question_lowest_excess_return_in_q4(tools):
    result = tools.rank_managers("Q4", "excess return", "asc", limit=1)
    assert result["tool"] == "rank_managers"
    assert len(result["rows"]) == 1
    assert result["rows"][0]["metric"]["metric_id"] == "manager_excess_return_pp"
    assert result["rows"][0]["metric"]["value"] == min(row["metric"]["value"] for row in tools.rank_managers("Q4", "excess return", "asc")["rows"])


def test_tool_question_compare_bpt_ble_private_equity(tools):
    result = tools.compare_funds("allocation_drift_pp", "FY2026", asset_class="Private Equity")
    assert result["tool"] == "compare_funds"
    assert [row["fund"] for row in result["rows"]] == ["BPT", "BLE"]
    assert all(row["metric"]["metric_id"] == "allocation_drift_pp" for row in result["rows"])
    assert result["comparison"]["unit"] == "percentage points"


def test_tool_question_bpt_cash_change_q3_to_q4(tools):
    result = tools.compare_periods("Cash", "allocation_drift_pp", "Q3", "Q4", fund="BPT")
    assert result["tool"] == "compare_periods"
    assert result["rows"][0]["metric"]["metric_id"] == "allocation_drift_pp"
    assert result["comparison"]["period_b_minus_period_a"] == result["rows"][1]["metric"]["value"] - result["rows"][0]["metric"]["value"]


def test_tool_question_research_signals_for_bpt(tools):
    result = tools.get_research_signals(fund="BPT")
    assert result["tool"] == "get_research_signals"
    assert result["rows"]
    assert all(row["provenance"]["source_record_ids"] for row in result["rows"])


def test_fund_summary_uses_canonical_metric_values(tools):
    result = tools.get_fund_summary("BPT", "FY2026")
    assert result["metrics"]["aum"]["metric_id"] == "ending_aum"
    assert result["metrics"]["return"]["metric_id"] == "fund_return_pct"
    assert result["metrics"]["policy_benchmark"]["metric_id"] == "policy_benchmark_return_pct"
    assert result["metrics"]["excess_return"]["provenance"]["source_files"] == ["20260630_FYTD.xlsx"]


def test_manager_performance_and_history_tools(tools):
    rows = tools.get_manager_performance(manager="Compass Infrastructure Partners", fund="BPT", period="Q4")["rows"]
    assert rows
    assert all(row["manager"] == "Compass Infrastructure Partners" for row in rows)
    history = tools.get_manager_history("Compass Infrastructure Partners", fund="BPT")
    assert {row["period"] for row in history["rows"]} == {"Q1", "Q2", "Q3", "Q4"}


def test_cash_flow_and_reconciliation_tools(tools):
    cash = tools.get_cash_flows("BPT", "H2 FY2026")
    assert cash["metrics"]["net_cash_flow"]["metric_id"] == "net_cash_flow"
    assert {row["quarter"] for row in cash["rows"]} == {"Q3", "Q4"}
    validation = tools.validate_reconciliation("BPT", "Q4")
    assert abs(validation["metrics"]["reconciliation_variance"]["value"]) <= 0.05
    assert validation["metrics"]["allocation_validation"]["value_text"] == "pass"
    assert all(row["status"] == "pass" for row in validation["rows"])


def test_get_source_record_returns_compact_provenance(tools):
    summary = tools.get_fund_summary("BPT", "FY2026")
    record_id = summary["metrics"]["excess_return"]["record_id"]
    source = tools.get_source_record(record_id)
    assert source["record"]["record_id"] == record_id
    assert source["record"]["provenance"]["source_files"] == ["20260630_FYTD.xlsx"]


def test_business_tools_return_structured_errors_for_invalid_inputs(tools):
    cases = [
        (tools.get_fund_summary("BPT", "Q8"), "invalid_period"),
        (tools.get_fund_summary("XYZ", "Q4"), "unknown_entity"),
        (tools.get_manager_history("Unknown Manager"), "unknown_entity"),
        (tools.get_fund_summary("BPT", "FY2027"), "no_data"),
        (tools.compare_funds("sharpe_ratio", "Q4"), "unsupported_metric"),
        (tools.get_source_record("NOT_A_RECORD"), "no_data"),
    ]
    for result, code in cases:
        assert result["ok"] is False
        assert result["error"]["code"] == code
        assert result["error"]["message"]


def event_names(result):
    return [row["event"] for row in result["events"]]


def test_agent_simple_tool_execution(model):
    adapter = ScriptedModelAdapter(
        [
            ModelResponse(tool_calls=[ToolCall("get_asset_allocation", {"fund": "BLE", "period": "Q3", "asset_class": "Private Equity"})]),
            ModelResponse(final_answer="BLE Private Equity was 20.8% versus a 20.0% policy target in Q3, a +0.80pp drift. Source: 20260331_FYTD.xlsx, Asset_Allocation row 50."),
        ]
    )
    result = AskBeaconAgent(model, adapter).answer("What was BLE's Private Equity allocation versus target in Q3?")
    assert result["ok"] is True
    assert "20.8%" in result["answer"]
    assert "source_verified" in event_names(result)
    assert result["tool_observations"][0]["tool"] == "get_asset_allocation"


def test_agent_multi_step_investigation_execution(model):
    adapter = ScriptedModelAdapter(
        [
            ModelResponse(tool_calls=[ToolCall("get_fund_summary", {"fund": "BPT", "period": "FY2026"})]),
            ModelResponse(tool_calls=[ToolCall("get_research_signals", {"fund": "BPT", "period": "FY2026"})]),
            ModelResponse(tool_calls=[ToolCall("rank_managers", {"period": "FY2026", "metric": "excess return", "direction": "asc", "fund": "BPT", "limit": 3})]),
            ModelResponse(tool_calls=[ToolCall("get_cash_flows", {"fund": "BPT", "period": "FY2026"})]),
            ModelResponse(final_answer="Investigate BPT's largest sourced policy drift, weakest benchmark-relative managers, and FY2026 cash-flow pressure. These are sourced observations, not a causal claim."),
        ]
    )
    result = AskBeaconAgent(model, adapter).answer("What should I investigate about BPT this year?")
    assert result["ok"] is True
    assert len(result["tool_observations"]) == 4
    assert event_names(result).count("tool_completed") == 4
    assert "not a causal claim" in result["answer"]


def test_agent_contextual_comparison_execution(model):
    adapter = ScriptedModelAdapter(
        [
            ModelResponse(tool_calls=[ToolCall("compare_funds", {"metric": "allocation_drift_pp", "period": "FY2026", "asset_class": "Private Equity"})]),
            ModelResponse(final_answer="Against BLE, BPT Private Equity drift was +0.97pp versus BLE at +0.94pp in FY2026. Source: 20260630_FYTD.xlsx Asset_Allocation rows 59 and 68."),
        ]
    )
    context = {"fund": "BPT", "period": "FY2026", "asset_class": "Private Equity", "source_page": "portfolio"}
    result = AskBeaconAgent(model, adapter).answer("Compare this with BLE.", context)
    assert result["ok"] is True
    assert result["events"][0]["interpretation"]["compare_to_fund"] == "BLE"
    assert result["tool_observations"][0]["tool"] == "compare_funds"


def test_agent_requests_clarification_before_model_when_semantic_layer_flags_ambiguity(model):
    adapter = ScriptedModelAdapter([])
    result = AskBeaconAgent(model, adapter).answer("Who was the best performer?")
    assert result["ok"] is False
    assert result["status"] == "needs_clarification"
    assert adapter.calls == 0
    assert "clarification_requested" in event_names(result)


def test_agent_handles_model_out_of_scope(model):
    adapter = ScriptedModelAdapter([ModelResponse(out_of_scope="That question is outside Beacon's FY2026 portfolio dataset.")])
    result = AskBeaconAgent(model, adapter).answer("What is the weather tomorrow?", {"fund": "BPT", "period": "Q4"})
    assert result["ok"] is False
    assert result["status"] == "out_of_scope"
    assert "out_of_scope" in event_names(result)


def test_agent_records_tool_validation_failure(model):
    adapter = ScriptedModelAdapter(
        [
            ModelResponse(tool_calls=[ToolCall("get_fund_summary", {"fund": "BPT", "period": "Q8"})]),
            ModelResponse(final_answer="I cannot answer because Q8 is not a supported Beacon period."),
        ]
    )
    result = AskBeaconAgent(model, adapter).answer("Show BPT in Q8.", {"fund": "BPT", "period": "Q4"})
    assert result["ok"] is False
    assert result["status"] == "validation_failed"
    assert result["tool_observations"][0]["error"]["code"] == "invalid_period"
    assert "validation_failed" in event_names(result)


def test_agent_rejects_final_financial_answer_without_tools(model):
    adapter = ScriptedModelAdapter([ModelResponse(final_answer="BPT returned 9.9%.")])
    result = AskBeaconAgent(model, adapter).answer("What did BPT return in Q4?", {"fund": "BPT", "period": "Q4"})
    assert result["ok"] is False
    assert result["status"] == "validation_failed"
    assert "require at least one successful deterministic Beacon tool observation" in result["answer"]


def test_agent_max_steps_guard(model):
    adapter = ScriptedModelAdapter([ModelResponse(tool_calls=[ToolCall("get_fund_summary", {"fund": "BPT", "period": "Q4"})]) for _ in range(3)])
    result = AskBeaconAgent(model, adapter, max_steps=2).answer("Keep looking at BPT.", {"fund": "BPT", "period": "Q4"})
    assert result["ok"] is False
    assert result["status"] == "max_steps_exceeded"
    assert len(result["tool_observations"]) == 2


def test_agent_clarifies_best_manager_metric_before_model(model):
    adapter = ScriptedModelAdapter([])
    result = AskBeaconAgent(model, adapter).answer("Which manager performed best?")
    assert result["ok"] is False
    assert result["outcome"] == "clarify"
    assert adapter.calls == 0
    assert "Highest absolute return" in result["answer"]
    assert "Highest excess return vs benchmark" in result["answer"]
    assert "Most consistent outperformer" in result["answer"]


def test_agent_clarifies_asset_review_dimension_before_model(model):
    adapter = ScriptedModelAdapter([])
    result = AskBeaconAgent(model, adapter).answer("How did Private Equity do?")
    assert result["ok"] is False
    assert result["outcome"] == "clarify"
    assert adapter.calls == 0
    assert "Performance vs benchmark" in result["answer"]
    assert "Allocation vs policy" in result["answer"]
    assert "Underlying managers" in result["answer"]
    assert "Full review" in result["answer"]


def test_agent_uses_context_for_why_did_this_move(model):
    adapter = ScriptedModelAdapter(
        [
            ModelResponse(tool_calls=[ToolCall("get_allocation_history", {"fund": "BPT", "asset_class": "Private Equity"})]),
            ModelResponse(final_answer="BPT Private Equity moved based on observed allocation drift over FY2026. This describes the allocation trend, not causality. Sources are in the tool provenance."),
        ]
    )
    context = {"fund": "BPT", "period": "FY2026", "asset_class": "Private Equity", "source_page": "insights", "research_signal_id": "SIG_002"}
    result = AskBeaconAgent(model, adapter).answer("Why did this move?", context)
    assert result["ok"] is True
    assert result["outcome"] == "answer"
    assert adapter.calls == 2
    assert result["tool_observations"][0]["tool"] == "get_allocation_history"


def test_agent_marks_strategy_data_out_of_scope_before_model(model):
    adapter = ScriptedModelAdapter([])
    result = AskBeaconAgent(model, adapter).answer("Why did Manager XYZ change investment strategy?")
    assert result["ok"] is False
    assert result["outcome"] == "out_of_scope"
    assert adapter.calls == 0
    assert "cannot establish why an investment strategy changed" in result["answer"]
    assert "analyse the manager's performance" in result["answer"]
    assert "compare the manager with its benchmark" in result["answer"]
    assert "show the quarterly trend" in result["answer"]


def test_agent_handles_unsupported_manager_underperformance_causality(model):
    adapter = ScriptedModelAdapter([])
    result = AskBeaconAgent(model, adapter).answer("Why did Compass Infrastructure Partners underperform?", {"fund": "BPT", "period": "FY2026"})
    assert result["ok"] is True
    assert result["outcome"] == "unsupported_causality"
    assert adapter.calls == 0
    assert len(result["tool_observations"]) == 2
    assert "holdings-level attribution is unavailable" in result["answer"]
    assert "cannot establish why" in result["answer"]


def test_ask_service_returns_machine_readable_clarification(model):
    service = AskBeaconService(model)
    result = service.create_request("Who performed best?", {"fund": "BPT", "period": "FY2026"})
    assert result["type"] == "clarification"
    assert result["status"] == "waiting_for_clarification"
    assert result["request_id"].startswith("req_")
    assert result["question"] == "How should I measure best performance?"
    assert result["options"] == [
        {"label": "Highest absolute return", "field": "ranking_metric", "value": "manager_return_pct"},
        {"label": "Highest return vs benchmark", "field": "ranking_metric", "value": "manager_excess_return_pp"},
        {"label": "Most consistent outperformer", "field": "ranking_metric", "value": "manager_consistency"},
    ]
    debug = result["debug_state"]
    assert debug["original_query"] == "Who performed best?"
    assert debug["intent"] == "manager_ranking"
    assert debug["ambiguities"]["field"] == "ranking_metric"


def test_ask_service_clarification_resumes_same_request_and_answers(model):
    service = AskBeaconService(model)
    first = service.create_request("Who performed best?", {"fund": "BPT", "period": "FY2026"})
    request_id = first["request_id"]
    result = service.clarify(request_id, {"field": "ranking_metric", "value": "manager_excess_return_pp", "label": "Highest return vs benchmark"})
    assert result["type"] == "answer"
    assert result["ok"] is True
    assert result["request_id"] == request_id
    assert "BPT" in result["answer"]
    assert "FY2026" in result["answer"]
    assert result["metrics"][2]["metric_id"] == "manager_excess_return_pp"
    assert result["metrics"][2]["provenance"]["source_record_ids"]
    events = [event["status"] for event in result["debug_state"]["events"]]
    assert "waiting_for_clarification" in events
    assert "clarification_received" in events
    assert "ready" in events
    assert "tool_running" in events
    assert "tool_complete" in events
    assert "validated" in events
    assert "answered" in events
    assert result["debug_state"]["current_status"] == "answered"
    assert result["debug_state"]["clarification_selected"]["value"] == "manager_excess_return_pp"


def test_ask_service_all_best_performance_choices_resume_to_canonical_tools(model):
    expected = {
        "manager_return_pct": "Manager return",
        "manager_excess_return_pp": "Excess return",
        "manager_consistency": "Quarters outperforming",
    }
    for value, metric_label in expected.items():
        service = AskBeaconService(model)
        first = service.create_request("Who performed best?", {"fund": "BPT", "period": "FY2026"})
        result = service.clarify(first["request_id"], {"field": "ranking_metric", "value": value})
        assert result["type"] == "answer"
        assert result["request_id"] == first["request_id"]
        labels = [metric["label"] for metric in result["metrics"]]
        assert metric_label in labels
        assert any(event["status"] == "tool_running" and event.get("tool_selected") == "rank_managers" for event in result["debug_state"]["events"])
        assert any(event["status"] == "validated" and event["validation_result"]["ok"] for event in result["debug_state"]["events"])


def test_ask_service_rejects_invalid_clarification_without_new_request(model):
    service = AskBeaconService(model)
    first = service.create_request("Who performed best?", {"fund": "BPT", "period": "FY2026"})
    result = service.clarify(first["request_id"], {"field": "ranking_metric", "value": "display text only"})
    assert result["type"] == "error"
    assert result["error"]["code"] == "invalid_clarification"
