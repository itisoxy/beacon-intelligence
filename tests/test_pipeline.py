import json
from pathlib import Path

import duckdb
import pytest

from beacon_data import AskBeaconConversation, BeaconToolAdapter, ScriptedChatAdapter, ToolSelectingTestAdapter, build_grounded_response, build_model
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

def test_langgraph_conversation_thread_persists_messages(tmp_path):
    conversation = AskBeaconConversation(ScriptedChatAdapter(), tmp_path / "ask_beacon_checkpoints.sqlite")
    thread_id = "thread_test_001"

    first = conversation.ask(thread_id, "Who performed best?", {"fund": "BPT", "period": "FY2026"})
    second = conversation.ask(thread_id, "Relative to benchmark.")
    third = conversation.ask(thread_id, "What about consistency?")

    assert "highest absolute return" in first["answer"]
    assert "benchmark-relative performance" in second["answer"]
    assert "same BPT FY2026 manager question" in third["answer"]
    assert [message["content"] for message in third["messages"] if message["role"] == "user"] == [
        "Who performed best?",
        "Relative to benchmark.",
        "What about consistency?",
    ]
    assert len([message for message in third["messages"] if message["role"] == "assistant"]) == 3
    assert third["application_context"] == {"fund": "BPT", "period": "FY2026"}
    assert (tmp_path / "ask_beacon_checkpoints.sqlite").exists()
    assert all(event["event"] == "model_completed" for event in third["tool_events"])
    conversation.close()


def test_langgraph_separate_threads_are_isolated(tmp_path):
    conversation = AskBeaconConversation(ScriptedChatAdapter(), tmp_path / "ask_beacon_checkpoints.sqlite")

    conversation.ask("thread_test_001", "Who performed best?", {"fund": "BPT", "period": "FY2026"})
    conversation.ask("thread_test_001", "Relative to benchmark.")
    isolated = conversation.ask("thread_test_002", "What about consistency?", {"fund": "BLE", "period": "Q4"})

    assert "different fund or period" in isolated["answer"]
    assert "benchmark-relative" not in isolated["answer"]
    assert [message["content"] for message in isolated["messages"] if message["role"] == "user"] == ["What about consistency?"]
    assert isolated["application_context"] == {"fund": "BLE", "period": "Q4"}
    conversation.close()


def test_langgraph_financial_tools_answer_requested_questions(model, tmp_path):
    conversation = AskBeaconConversation(ToolSelectingTestAdapter(), tmp_path / "ask_beacon_tools.sqlite", model=model)
    cases = [
        ("What was BPT's FY2026 return?", "get_fund_performance", "fund_return_pct"),
        ("How far was BPT Cash from policy in Q4?", "get_asset_allocation", "allocation_drift_pp"),
        ("Which manager underperformed its benchmark most in Q4?", "rank_managers", "manager_excess_return_pp"),
        ("What was BLE Private Equity allocation versus policy in Q3?", "get_asset_allocation", "actual_allocation_pct"),
    ]

    for index, (question, tool_name, metric_id) in enumerate(cases, start=1):
        result = conversation.ask(f"thread_tool_test_{index}", question)
        tool_events = [event for event in result["turn_tool_events"] if event["event"] == "tool_completed"]
        matching_events = [event for event in tool_events if event["tool"] == tool_name]
        assert matching_events
        assert matching_events[-1]["ok"] is True
        assert matching_events[-1]["record_ids"]
        assert result["sources"]
        assert metric_id in str([message["content"] for message in result["messages"] if message["role"] == "tool"])
        assert result["answer"]
    conversation.close()


def test_langgraph_native_ambiguity_resolution_uses_same_thread(model, tmp_path):
    conversation = AskBeaconConversation(ToolSelectingTestAdapter(), tmp_path / "ask_beacon_ambiguity.sqlite", model=model)
    thread_id = "thread_native_ambiguity_001"

    first = conversation.ask(thread_id, "Who performed best?", {"fund": "BPT", "period": "FY2026"})
    second = conversation.ask(thread_id, "Relative to benchmark.")
    third = conversation.ask(thread_id, "How consistent were they?")

    assert "highest absolute return" in first["answer"]
    assert "strongest benchmark-relative performance" in second["answer"]
    selected = [event for event in second["turn_tool_events"] if event["event"] == "tool_selected"]
    assert any(event["tool"] == "rank_managers" and event["arguments"]["fund"] == "BPT" and event["arguments"]["period"] == "FY2026" and event["arguments"]["metric"] == "excess_return" for event in selected)
    assert any(event["tool"] == "get_manager_performance" for event in selected)
    assert second["sources"]
    assert "outperformed in" in third["answer"]
    assert any(event["event"] == "tool_selected" and event["tool"] == "get_manager_performance" for event in third["turn_tool_events"])
    user_messages = [message["content"] for message in third["messages"] if message["role"] == "user"]
    assert user_messages == ["Who performed best?", "Relative to benchmark.", "How consistent were they?"]
    conversation.close()


def test_langgraph_absolute_return_resolution_uses_absolute_metric(model, tmp_path):
    conversation = AskBeaconConversation(ToolSelectingTestAdapter(), tmp_path / "ask_beacon_absolute.sqlite", model=model)
    thread_id = "thread_native_ambiguity_002"

    conversation.ask(thread_id, "Who performed best?", {"fund": "BPT", "period": "FY2026"})
    result = conversation.ask(thread_id, "Absolute return.")

    selected = [event for event in result["turn_tool_events"] if event["event"] == "tool_selected"]
    assert any(event["tool"] == "rank_managers" and event["arguments"]["metric"] == "absolute_return" for event in selected)
    assert "highest absolute return" in result["answer"]
    assert result["sources"]
    conversation.close()


def test_grounded_response_validates_valid_numerical_answer(model, tmp_path):
    conversation = AskBeaconConversation(ToolSelectingTestAdapter(), tmp_path / "ask_beacon_grounded.sqlite", model=model)
    result = conversation.ask("thread_grounded_valid", "What was BLE Private Equity allocation versus policy in Q3?")

    grounded = result["grounded_response"]
    assert grounded["answer"] == result["answer"]
    assert grounded["metrics"]
    assert grounded["sources"]
    assert grounded["activity_events"]
    assert grounded["validation_errors"] == []
    assert any(metric["metric_id"] == "actual_allocation_pct" for metric in grounded["metrics"])
    assert all(metric["calculation_owner"] == "Python" for metric in grounded["metrics"])
    conversation.close()


def test_grounded_response_reports_invalid_period(model, tmp_path):
    conversation = AskBeaconConversation(ToolSelectingTestAdapter(), tmp_path / "ask_beacon_grounded_errors.sqlite", model=model)
    result = conversation.ask("thread_grounded_invalid_period", "Show BPT Q8.")

    assert "invalid_period" in result["validation_errors"]
    assert result["grounded_response"]["validation_errors"] == ["invalid_period"]
    assert result["grounded_response"]["metrics"] == []
    conversation.close()


def test_research_top_signal_followup_uses_previous_structured_result(model, tmp_path):
    conversation = AskBeaconConversation(ToolSelectingTestAdapter(), tmp_path / "ask_beacon_research_followup.sqlite", model=model)
    thread_id = "thread_research_top_signal"

    first = conversation.ask(thread_id, "What should I investigate about BPT?", {"fund": "BPT", "period": "FY2026"})
    top_signal_id = first["resolved_context"]["primary_research_signal_id"]

    second = conversation.ask(thread_id, "Explain the top signal")

    assert second["grounded_response"]["response_type"] == "contextual_signal_explanation"
    assert second["resolved_context"]["active_fund"] == "BPT"
    assert second["resolved_context"]["active_period"] == "FY2026"
    assert second["resolved_context"]["primary_research_signal_id"] == top_signal_id
    assert "clarify" not in second["answer"].lower()
    assert "| Signal | Evidence | Why it matters |" not in second["answer"]
    assert "These are Beacon research signals" not in second["answer"]
    assert "CIO question" not in second["answer"]
    assert any(event.get("tool") == "get_research_signals" for event in second["turn_tool_events"])
    assert any(event.get("arguments", {}).get("signal_id") == top_signal_id for event in second["turn_tool_events"])
    conversation.close()


def test_research_why_followup_retains_same_signal(model, tmp_path):
    conversation = AskBeaconConversation(ToolSelectingTestAdapter(), tmp_path / "ask_beacon_research_why.sqlite", model=model)
    thread_id = "thread_research_why"

    first = conversation.ask(thread_id, "What should I investigate about BPT?", {"fund": "BPT", "period": "FY2026"})
    top_signal_id = first["resolved_context"]["primary_research_signal_id"]
    conversation.ask(thread_id, "Explain the top signal")
    third = conversation.ask(thread_id, "Why does this matter?")

    assert third["grounded_response"]["response_type"] == "contextual_signal_explanation"
    assert third["resolved_context"]["primary_research_signal_id"] == top_signal_id
    assert "On why" in third["answer"]
    assert "CIO question" not in third["answer"]
    assert any(event.get("arguments", {}).get("signal_id") == top_signal_id for event in third["turn_tool_events"])
    conversation.close()


def test_research_source_followup_uses_top_signal_evidence(model, tmp_path):
    conversation = AskBeaconConversation(ToolSelectingTestAdapter(), tmp_path / "ask_beacon_research_source.sqlite", model=model)
    thread_id = "thread_research_source"

    first = conversation.ask(thread_id, "What should I investigate about BPT?", {"fund": "BPT", "period": "FY2026"})
    expected_record_id = first["resolved_context"]["source_record_ids"][0]
    second = conversation.ask(thread_id, "Show the evidence")

    assert second["grounded_response"]["response_type"] == "source_evidence"
    assert any(event.get("tool") == "get_source_record" for event in second["turn_tool_events"])
    assert any(event.get("arguments", {}).get("record_id") == expected_record_id for event in second["turn_tool_events"])
    conversation.close()


def test_research_next_steps_followup_is_direct_not_generic_card(model, tmp_path):
    conversation = AskBeaconConversation(ToolSelectingTestAdapter(), tmp_path / "ask_beacon_research_next.sqlite", model=model)
    thread_id = "thread_research_next"

    conversation.ask(thread_id, "What should I investigate about BPT?", {"fund": "BPT", "period": "FY2026"})
    result = conversation.ask(thread_id, "What should I check next?")

    assert result["grounded_response"]["response_type"] == "contextual_signal_explanation"
    assert "On what to check next" in result["answer"]
    assert "| Signal | Evidence | Why it matters |" not in result["answer"]
    assert "CIO question" not in result["answer"]
    conversation.close()


def test_top_signal_followup_clarifies_without_prior_signal(model, tmp_path):
    conversation = AskBeaconConversation(ToolSelectingTestAdapter(), tmp_path / "ask_beacon_research_isolated.sqlite", model=model)

    result = conversation.ask("thread_research_isolated", "Explain the top signal")

    assert "which research signal" in result["answer"].lower()
    assert not any(event.get("tool") == "get_research_signals" for event in result["turn_tool_events"])
    conversation.close()


def test_grounded_response_reports_unknown_fund(model, tmp_path):
    adapter = BeaconToolAdapter(model)
    observation = adapter.get_fund_performance("XYZ", "Q4")
    grounded = build_grounded_response(
        answer="I could not answer that: Unknown fund.",
        user_message="How did Fund XYZ do in Q4?",
        application_context={},
        turn_messages=[{"role": "tool", "content": json.dumps(observation)}],
        turn_tool_events=[{"event": "tool_completed", "tool": "get_fund_performance", "ok": False, "record_ids": []}],
        turn_sources=[],
    )

    assert grounded["validation_errors"] == ["unknown_entity"]
    assert grounded["metrics"] == []


def test_grounded_response_reports_unknown_manager(model):
    adapter = BeaconToolAdapter(model)
    observation = adapter.get_manager_history("Unknown Manager")
    grounded = build_grounded_response(
        answer="I could not answer that: Unknown manager.",
        user_message="How did Unknown Manager do?",
        application_context={},
        turn_messages=[{"role": "tool", "content": json.dumps(observation)}],
        turn_tool_events=[{"event": "tool_completed", "tool": "get_manager_history", "ok": False, "record_ids": []}],
        turn_sources=[],
    )

    assert grounded["validation_errors"] == ["unknown_entity"]


def test_grounded_response_detects_missing_provenance():
    observation = {
        "ok": True,
        "tool": "get_fund_performance",
        "arguments": {"fund": "BPT", "period": "FY2026"},
        "fund": "BPT",
        "period": "FY2026",
        "fund_return_pct": {
            "metric_id": "fund_return_pct",
            "record_id": "TEST_NO_PROVENANCE",
            "value": 4.25,
            "unit": "percent",
            "support_status": "supported",
            "provenance": [],
        },
    }
    grounded = build_grounded_response(
        answer="BPT returned 4.25% in FY2026.",
        user_message="What did BPT return in FY2026?",
        application_context={},
        turn_messages=[{"role": "tool", "content": json.dumps(observation)}],
        turn_tool_events=[{"event": "tool_completed", "tool": "get_fund_performance", "ok": True, "record_ids": ["TEST_NO_PROVENANCE"]}],
        turn_sources=[],
    )

    assert "missing_provenance" in grounded["validation_errors"]
    assert grounded["limitations"]


def test_grounded_response_detects_unsupported_metric():
    observation = {
        "ok": False,
        "tool": "compare_funds",
        "arguments": {"metric": "sharpe_ratio", "period": "Q4"},
        "error": {"code": "unsupported_metric", "message": "Unsupported metric for Beacon business tools."},
    }
    grounded = build_grounded_response(
        answer="I could not answer that: Unsupported metric for Beacon business tools.",
        user_message="Compare Sharpe ratios in Q4.",
        application_context={},
        turn_messages=[{"role": "tool", "content": json.dumps(observation)}],
        turn_tool_events=[{"event": "tool_completed", "tool": "compare_funds", "ok": False, "record_ids": []}],
        turn_sources=[],
    )

    assert "unsupported_metric" in grounded["validation_errors"]
    assert "not supported" in grounded["limitations"][0].lower()


def test_grounded_response_qualifies_unsupported_causality():
    observation = {
        "ok": True,
        "tool": "get_fund_performance",
        "arguments": {"fund": "BPT", "period": "FY2026"},
        "fund": "BPT",
        "period": "FY2026",
        "excess_return_pp": {
            "metric_id": "fund_excess_return_pp",
            "record_id": "TEST_CAUSALITY",
            "value": -1.25,
            "unit": "percentage points",
            "support_status": "supported",
            "provenance": [{"record_id": "TEST_CAUSALITY", "source_file": "test.xlsx", "source_sheet": "Fund", "source_row": 2, "source_cells": ["A2"]}],
        },
    }
    grounded = build_grounded_response(
        answer="BPT underperformed by -1.25pp because manager positioning caused it.",
        user_message="Why did BPT underperform?",
        application_context={},
        turn_messages=[{"role": "tool", "content": json.dumps(observation)}],
        turn_tool_events=[{"event": "tool_completed", "tool": "get_fund_performance", "ok": True, "record_ids": ["TEST_CAUSALITY"]}],
        turn_sources=[{"record_id": "TEST_CAUSALITY", "source_file": "test.xlsx", "source_sheet": "Fund", "source_row": 2, "source_cells": ["A2"]}],
    )

    assert "unsupported_causality" in grounded["validation_errors"]
    assert "does not establish" in grounded["answer"]


def test_grounded_response_rejects_unsupported_numerical_values():
    observation = {
        "ok": True,
        "tool": "get_asset_allocation",
        "arguments": {"fund": "BPT", "period": "Q4", "asset_class": "Cash"},
        "fund": "BPT",
        "period": "Q4",
        "asset_class": "Cash",
        "allocation_drift_pp": {
            "metric_id": "allocation_drift_pp",
            "record_id": "TEST_NUMERIC",
            "value": -2.5,
            "unit": "percentage points",
            "support_status": "supported",
            "provenance": [{"record_id": "TEST_NUMERIC", "source_file": "test.xlsx", "source_sheet": "Allocation", "source_row": 3, "source_cells": ["C3"]}],
        },
    }
    grounded = build_grounded_response(
        answer="BPT Cash was -2.50pp from policy, and the exposure was 99.99% of the fund.",
        user_message="How far was BPT Cash from policy in Q4?",
        application_context={},
        turn_messages=[{"role": "tool", "content": json.dumps(observation)}],
        turn_tool_events=[{"event": "tool_completed", "tool": "get_asset_allocation", "ok": True, "record_ids": ["TEST_NUMERIC"]}],
        turn_sources=[{"record_id": "TEST_NUMERIC", "source_file": "test.xlsx", "source_sheet": "Allocation", "source_row": 3, "source_cells": ["C3"]}],
    )

    assert grounded["validation_errors"] == ["unsupported_numerical_value"]
    assert "cannot safely return" in grounded["answer"]
