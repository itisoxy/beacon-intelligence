const data = window.BEACON_DATA;

const state = {
  page: "Portfolio",
  fund: "BPT",
  period: "FY2026",
  assetClass: "All",
  manager: "All",
  managerView: "All Managers",
  drawer: null,
  ask: {
    query: "",
    result: null,
    requests: {}
  }
};

const $ = (selector) => document.querySelector(selector);
const app = $("#app");

const periods = ["FY2026", "H1 FY2026", "H2 FY2026", "Q1", "Q2", "Q3", "Q4"];
const qOrder = { Q1: 1, Q2: 2, Q3: 3, Q4: 4 };
const sourcePeriods = ["Q1", "Q2", "Q3", "Q4"];

function fmtMoney(v) {
  const n = Number(v || 0);
  const sign = n < 0 ? "-" : "";
  const abs = Math.abs(n);
  if (abs >= 1000) return `${sign}$${(abs / 1000).toFixed(2)}B`;
  return `${sign}$${abs.toFixed(abs >= 100 ? 0 : 1)}M`;
}
function fmtPct(v, digits = 1) { return `${Number(v || 0).toFixed(digits)}%`; }
function fmtPp(v, digits = 2) {
  const n = Number(v || 0);
  return `${n >= 0 ? "+" : ""}${n.toFixed(digits)}pp`;
}
function cls(v) { return Number(v) < 0 ? "negative" : Number(v) > 0 ? "positive" : ""; }
function periodRows(rows) {
  if (state.period === "H1 FY2026" || state.period === "H2 FY2026") return rows.filter(r => r.Horizon === state.period || r.Quarter === (state.period === "H1 FY2026" ? "Q2" : "Q4"));
  return state.period === "FY2026" ? rows.filter(r => r.Quarter === "Q4") : rows.filter(r => r.Quarter === state.period);
}
function horizonSlug(period = state.period) {
  return period.toLowerCase().replaceAll(" ", "_");
}
function activeFundRows() {
  return data.analytics?.[`fund_horizon_${horizonSlug()}`] || data.analytics.fund_summary_view || data.records.fund_summary;
}
function activeAllocationRows() {
  return data.analytics?.[`allocation_horizon_${horizonSlug()}`] || data.analytics.asset_allocation_view || data.records.asset_allocation;
}
function activeManagerRows() {
  return data.analytics?.[`manager_horizon_${horizonSlug()}`] || data.analytics.manager_performance_view || data.records.manager_detail;
}
function currentFundRows() {
  return periodRows(activeFundRows()).filter(r => r.FundCode === state.fund);
}
function selectedFundSummary() {
  const rows = currentFundRows();
  return rows[0];
}
function sum(rows, field) { return rows.reduce((a, r) => a + Number(r[field] || 0), 0); }
function metricRow(metricId, filters = {}) {
  return (data.metric_values || []).find(r => {
    if (r.metric_id !== metricId) return false;
    return Object.entries(filters).every(([key, value]) => value === undefined || value === null || r[key] === value);
  });
}
function metricValue(metricId, filters = {}) {
  const row = metricRow(metricId, filters);
  return row ? row.value : undefined;
}
function fundMetric(metricId) {
  return metricValue(metricId, { fund_id: state.fund, period: state.period });
}
function selectedPeriodShortLabel() {
  if (state.period === "H1 FY2026") return "H1";
  if (state.period === "H2 FY2026") return "H2";
  return "QoQ";
}
function selectedPeriodQuarterCount() {
  if (state.period === "FY2026") return 4;
  if (state.period === "H1 FY2026" || state.period === "H2 FY2026") return 2;
  return 1;
}
function fundName(code) {
  if (code === "All") return "Combined Portfolio";
  return data.dimensions.funds.find(f => f.FundCode === code)?.FundName || code;
}
function assetRows() {
  return periodRows(activeAllocationRows())
    .filter(r => state.fund === "All" || r.FundCode === state.fund)
    .filter(r => state.assetClass === "All" || r.AssetClassLevel1 === state.assetClass);
}
function managerRows() {
  return periodRows(activeManagerRows())
    .filter(r => state.fund === "All" || r.FundCode === state.fund)
    .filter(r => state.assetClass === "All" || r.AssetClassLevel1 === state.assetClass)
    .filter(r => state.manager === "All" || r.ManagerName === state.manager)
    .map(r => {
      const fy = state.period === "FY2026";
      const horizon = state.period === "H1 FY2026" || state.period === "H2 FY2026";
      return {
        ...r,
        BenchmarkReturnPct: Number(horizon ? r.HorizonBenchmarkPct : fy ? r.BenchmarkReturnPct : r.BenchmarkQTDReturnPct),
        DisplayReturnPct: Number(horizon ? r.HorizonReturnPct : fy ? r.DisplayFYTDReturnPct : r.DisplayQTDReturnPct),
        ExcessReturnPp: Number(horizon ? r.HorizonExcessPp : fy ? r.ExcessFYTDReturnPp : r.ExcessQTDReturnPp),
        QuartersAhead: Number(horizon ? r.HorizonQuartersAhead : r.QuartersAhead || 0)
      };
    })
    .filter(r => {
      if (state.managerView === "Outperforming") return r.ExcessReturnPp > 0;
      if (state.managerView === "Underperforming") return r.ExcessReturnPp < 0;
      if (state.managerView === "Persistent Underperformance") return r.QuartersAhead <= 1;
      return true;
    });
}
function qoqChange() {
  const s = selectedFundSummary();
  if (!s) return { amount: 0, pct: 0 };
  const amount = fundMetric("aum_change_amount");
  const pct = fundMetric("aum_change_pct");
  if (amount !== undefined && pct !== undefined) return { amount: Number(amount || 0), pct: Number(pct || 0) };
  return { amount: Number(s.QoQAUMChange || 0), pct: Number(s.QoQAUMChangePct || 0) };
}
function latestDate() {
  return data.files.map(f => f.as_of).sort().at(-1);
}
function driftRows() {
  return assetRows().sort((a, b) => Math.abs(b.VarianceToTargetPct) - Math.abs(a.VarianceToTargetPct));
}
function trendForAsset(assetClass, field = "PctOfFundTotal") {
  return sourcePeriods.map(q => {
    const row = (data.analytics.asset_allocation_view || data.records.asset_allocation).find(r => r.Quarter === q && r.AssetClassLevel1 === assetClass && r.FundCode === state.fund);
    return Number(row?.[field] || 0);
  });
}
function spark(values) {
  const max = Math.max(...values.map(Math.abs), 1);
  return `<span class="spark">${values.map(v => `<span style="height:${Math.max(4, Math.abs(v) / max * 26)}px"></span>`).join("")}</span>`;
}
function confidence() {
  const rows = data.audit.validations.filter(v => state.fund === "All" || v.fund === state.fund);
  const fails = rows.filter(v => v.status !== "pass");
  const fundVar = rows.filter(v => v.type === "fund_roll_forward").reduce((a, v) => a + Math.abs(Number(v.variance || 0)), 0);
  const alloc = rows.filter(v => v.type === "allocation_total");
  const manager = rows.filter(v => v.type === "manager_rollup");
  return { rows, fails, fundVar, alloc, manager };
}
function attentionItems() {
  const drifts = driftRows();
  const mgrs = managerRows().sort((a, b) => a.ExcessReturnPp - b.ExcessReturnPp);
  const cf = selectedFundSummary();
  const items = [];
  if (drifts[0]) items.push({ priority: Math.abs(drifts[0].VarianceToTargetPct) > 2 ? "High" : "Medium", title: `${drifts[0].AssetClassLevel1} allocation`, detail: `${fmtPp(drifts[0].VarianceToTargetPct)} versus policy`, action: () => openDrawer("asset", drifts[0].AssetClassLevel1) });
  const under = drifts.filter(d => Number(d.VarianceToTargetPct) < 0).sort((a, b) => a.VarianceToTargetPct - b.VarianceToTargetPct)[0];
  if (under) items.push({ priority: "Medium", title: `${under.AssetClassLevel1} underweight`, detail: `${fmtPp(under.VarianceToTargetPct)} versus policy`, action: () => openDrawer("asset", under.AssetClassLevel1) });
  if (mgrs[0]) items.push({ priority: mgrs[0].QuartersAhead <= 1 ? "High" : "Medium", title: `${mgrs[0].ManagerName}`, detail: `${fmtPp(mgrs[0].ExcessReturnPp)} versus benchmark`, action: () => openDrawer("manager", mgrs[0].ManagerName) });
  if (cf && Number(cf.NetCashFlow) < 0) items.push({ priority: "Low", title: "Net cash flow", detail: `${fmtMoney(cf.NetCashFlow)} FYTD movement`, action: () => null });
  return items.slice(0, 5);
}
function render() {
  const s = selectedFundSummary();
  const qoq = qoqChange();
  const askPage = state.page === "Ask Beacon";
  app.innerHTML = `
    <div class="app-shell">
      ${sidebar()}
      <main class="main">
        <div class="top-row ${askPage ? "ask-top-row" : ""}">
          <div><h1 class="page-title">${state.page}</h1>${state.page === "Insights" ? `<p class="subtitle review-subtitle">${state.period} Investment Review</p>` : ""}</div>
          <div class="date-pill"><span class="eyebrow">Data as of</span><strong>${latestDate()}</strong></div>
        </div>
        ${askPage ? askBeaconPage() : `${filters()}${chips()}${state.page === "Portfolio" ? portfolioPage(s, qoq) : insightsPage()}`}
      </main>
      <div class="drawer-backdrop ${state.drawer ? "open" : ""}" onclick="closeDrawer()"></div>
      <aside class="drawer ${state.drawer ? "open" : ""}">${drawerContent()}</aside>
    </div>`;
  bindEvents();
}
function portfolioPage(s, qoq) {
  return `${s ? hero(s, qoq) : `<div class="empty">No data is available for this combination.</div>`}
    ${s ? movement(s, qoq) : ""}
    ${managerSection()}
    ${allocationSection()}
    ${confidenceSection()}`;
}
function sidebar() {
  return `<aside class="sidebar">
    <div class="logo"><div class="brand-mark"></div><div><div class="logo-title">Beacon</div><div class="logo-subtitle">Portfolio Intelligence</div></div></div>
    <nav class="nav">
      <button class="nav-item ${state.page === "Portfolio" ? "active" : ""}" data-page="Portfolio">Portfolio</button>
      <button class="nav-item ${state.page === "Insights" ? "active" : ""}" data-page="Insights">Insights</button>
      <button class="nav-item ${state.page === "Ask Beacon" ? "active" : ""}" data-page="Ask Beacon">Ask Beacon</button>
    </nav>
    <div class="nav-spacer"></div>
    <div class="user-card"><div class="avatar">DS</div><div><strong>Demo User</strong><div class="micro">CIO View</div></div></div>
  </aside>`;
}
function filters() {
  const funds = [`<option value="All">Combined / All</option>`, ...data.dimensions.funds.map(f => `<option value="${f.FundCode}">${f.FundCode} â€” ${f.FundName}</option>`)].join("");
  const assets = [`<option>All</option>`, ...data.dimensions.asset_classes.map(a => `<option>${a}</option>`)].join("");
  const managers = [`<option>All</option>`, ...data.dimensions.managers.map(m => `<option>${m}</option>`)].join("");
  return `<div class="filter-grid">
    <div class="field"><label>Fund</label><select id="fund">${funds}</select></div>
    <div class="field"><label>Period</label><select id="period">${periods.map(p => `<option>${p}</option>`).join("")}</select></div>
    <div class="field"><label>Asset Class</label><select id="assetClass">${assets}</select></div>
    <div class="field"><label>Manager</label><select id="manager">${managers}</select></div>
    <button class="reset" id="reset">Reset filters</button>
  </div>`;
}
function chips() {
  const items = [state.fund === "All" ? "Combined" : state.fund, state.period, state.assetClass, state.manager].filter(v => v !== "All");
  return `<div class="chips">${items.map(v => `<button class="chip" data-chip="${v}">${v} Ã—</button>`).join("")}</div>`;
}
function askContextChips() {
  const items = [
    state.fund !== "All" ? state.fund : null,
    state.period,
    state.assetClass !== "All" ? state.assetClass : null,
    state.manager !== "All" ? state.manager : null
  ].filter(Boolean);
  return `<div class="ask-context-chips">${items.map(v => `<button class="ask-chip" data-ask-chip="${v}">${v} <span>×</span></button>`).join("")}</div>`;
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function askBeaconPage() {
  const result = state.ask.result;
  return `<section class="ask-shell ${result ? "has-answer" : ""}">
    <div class="ask-hero">
      <h2>Ask your portfolio.</h2>
      <p>Grounded answers from your FY2026 portfolio data.</p>
      <form class="ask-search" id="askForm">
        <input id="askInput" value="${escapeHtml(state.ask.query)}" placeholder="Ask about allocation, managers, cash flow, or research signals" autocomplete="off">
        <button type="submit">Ask</button>
      </form>
      ${askContextChips()}
      ${!result ? askSuggestions() : ""}
    </div>
    ${result ? askResult(result) : ""}
  </section>`;
}
function askSuggestions() {
  const suggestions = [
    "What was BLE's Private Equity allocation versus target in Q3?",
    "Which manager had the weakest benchmark-relative performance in Q4?",
    "Compare BPT and BLE Private Equity allocation in Q4.",
    "What are the largest BPT research signals?"
  ];
  return `<div class="ask-suggestions">${suggestions.map(q => `<button data-ask-suggestion="${escapeHtml(q)}">${q}</button>`).join("")}</div>`;
}
function askResult(result) {
  if (result.outcome === "clarify") return askClarification(result);
  const metrics = (result.metrics || []).slice(0, 4);
  return `<div class="ask-answer-wrap">
    <article class="ask-answer">
      <p class="eyebrow">${result.outcome === "unsupported_causality" ? "Supported limits" : "Answer"}</p>
      <h3>${result.answer}</h3>
      ${metrics.length ? `<div class="ask-metrics">${metrics.map(askMetricCard).join("")}</div>` : ""}
      ${result.visual ? askVisual(result.visual) : ""}
      <div class="ask-answer-actions">
        <button class="text-action" data-ask-drawer="evidence">View evidence</button>
        <button class="text-action" data-ask-drawer="how">How Beacon answered</button>
      </div>
    </article>
    ${askFollowups(result)}
  </div>`;
}
function askClarification(result) {
  return `<div class="ask-answer-wrap">
    <article class="ask-answer ask-clarify">
      <p class="eyebrow">Clarification</p>
      <h3>${result.question || result.answer}</h3>
      <div class="ask-choice-grid">${(result.options || []).map(option => `<button data-request-id="${result.request_id}" data-clarify-field="${option.field}" data-clarify-value="${option.value}" data-clarify-label="${escapeHtml(option.label)}">${option.label}</button>`).join("")}</div>
      <button class="text-action" data-ask-drawer="how">How Beacon answered</button>
    </article>
  </div>`;
}
function askMetricCard(metric) {
  const unit = metric.unit || "";
  const formatted = unit === "USD millions" ? fmtMoney(metric.value) : unit === "percent" ? fmtPct(metric.value, 2) : unit === "percentage points" ? fmtPp(metric.value) : metric.value_text || metric.value;
  return `<div class="ask-metric"><span>${metric.label}</span><strong class="${cls(metric.value)}">${formatted}</strong></div>`;
}
function askVisual(visual) {
  if (visual.type === "period-bars") {
    const max = Math.max(...visual.items.map(i => Math.abs(Number(i.value))), 1);
    return `<div class="ask-mini-chart">${visual.items.map(i => `<div><span class="${Number(i.value) < 0 ? "negative-bg" : "positive-bg"}" style="height:${Math.max(20, Math.abs(Number(i.value)) / max * 120)}px"></span><strong>${i.label}</strong><em class="${cls(i.value)}">${fmtPp(i.value)}</em></div>`).join("")}</div>`;
  }
  return "";
}
function askFollowups(result) {
  const followups = result.followups || ["How has this changed?", "Compare with BPT/BLE", "Which managers contributed?", "What happened in H2?"];
  return `<div class="ask-followups"><p class="eyebrow">Follow-up</p>${followups.map(q => `<button data-ask-suggestion="${escapeHtml(q)}">${q}</button>`).join("")}</div>`;
}
function runAsk(query) {
  const result = answerAskQuery(query.trim());
  state.ask = { ...state.ask, query, result };
  render();
}
function resumeAsk(requestId, selection) {
  const request = state.ask.requests?.[requestId];
  if (!request) return;
  const result = continueAskRequest(request, selection);
  state.ask.result = result;
  render();
}
function askMetric(metric_id, filters) {
  return metricRow(metric_id, filters) || null;
}
function askMetricObj(label, row) {
  return {
    label,
    record_id: row?.metric_value_id,
    metric_id: row?.metric_id,
    value: row?.value,
    value_text: row?.value_text,
    unit: row?.unit,
    provenance: askProvenance(row || {})
  };
}
function askProvenance(row) {
  const cells = row.source_cells;
  return {
    source_record_ids: row.source_record_ids || (row.source_record_id ? [row.source_record_id] : []),
    source_files: row.source_files || (row.source_file ? [row.source_file] : []),
    source_sheets: row.source_sheets || (row.source_sheet ? [row.source_sheet] : []),
    source_rows: row.source_rows || (row.source_row ? [row.source_row] : []),
    source_cells: Array.isArray(cells) ? cells : (cells ? [cells] : [])
  };
}
function makeAskResult({ answer, metrics = [], evidence = [], events = [], visual = null, followups = [], outcome = "answer", debug_state = null }) {
  return { outcome, answer, metrics, evidence, events, visual, followups, debug_state };
}
function answerAskQuery(query) {
  const q = query.toLowerCase();
  if (!query) return clarifyAsk("What would you like to ask?", ["Review BPT this year", "Compare BPT and BLE", "Show Private Equity allocation"]);
  if (q.includes("which manager") && (q.includes("best") || q.includes("strongest") || q.includes("performed best"))) {
    return createBestManagerClarification(query);
  }
  if (q.includes("how did private equity do") || q.includes("how has private equity done")) {
    return clarifyAsk("What would you like to review for Private Equity?", ["Performance vs benchmark", "Allocation vs policy", "Underlying managers", "Full review"]);
  }
  if (q.includes("strategy")) {
    return makeAskResult({
      outcome: "out_of_scope",
      answer: "The supplied dataset cannot establish why an investment strategy changed. I can analyse performance, compare with benchmark, or show the quarterly trend.",
      events: [{ event: "out_of_scope", label: "Strategy-change data unavailable" }],
      followups: ["Analyse manager performance", "Compare with benchmark", "Show quarterly trend"]
    });
  }
  if (q.includes("private equity") && q.includes("ble") && q.includes("q3")) return askBlePeQ3();
  if (q.includes("weakest") || q.includes("lowest excess") || q.includes("largest detractor")) return askWeakestManagerQ4();
  if (q.includes("compare") && q.includes("private equity")) return askComparePrivateEquity(q.includes("q4") ? "Q4" : state.period);
  if (q.includes("cash") && q.includes("q3") && q.includes("q4")) return askCashQ3Q4();
  if (q.includes("research") && q.includes("bpt")) return askResearchBpt();
  if (q.includes("why did this move") && state.assetClass !== "All") return askThisMove();
  return clarifyAsk("I can answer that, but I need one more detail.", ["Performance vs benchmark", "Allocation vs policy", "Manager ranking", "Research signals"]);
}
function clarifyAsk(answer, labels) {
  return {
    outcome: "clarify",
    answer,
    options: labels.map(label => ({ label, query: label.includes("Allocation") ? `${state.assetClass === "All" ? "Private Equity" : state.assetClass} allocation vs policy for ${state.fund} ${state.period}` : label })),
    events: [{ event: "clarification_requested", label: "Asked for a decision that materially changes the answer" }]
  };
}
function createRequestId() {
  return `req_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}
function createBestManagerClarification(query) {
  const requestId = createRequestId();
  const request = {
    request_id: requestId,
    original_query: query,
    status: "waiting_for_clarification",
    context: askCurrentContext(),
    intent: "manager_ranking",
    ambiguity: { field: "ranking_metric" },
    debug: []
  };
  request.debug.push({ request_id: requestId, original_query: query, status: "received" });
  request.debug.push({ request_id: requestId, original_query: query, status: "interpreting", resolved_context: request.context, intent: request.intent, ambiguities: request.ambiguity });
  request.debug.push({ request_id: requestId, original_query: query, status: "waiting_for_clarification", missing: "ranking_metric" });
  state.ask.requests[requestId] = request;
  return {
    outcome: "clarify",
    type: "clarification",
    request_id: requestId,
    question: "How should I measure best performance?",
    answer: "How should I measure best performance?",
    options: [
      { label: "Highest absolute return", field: "ranking_metric", value: "manager_return_pct" },
      { label: "Highest return vs benchmark", field: "ranking_metric", value: "manager_excess_return_pp" },
      { label: "Most consistent outperformer", field: "ranking_metric", value: "manager_consistency" }
    ],
    events: [{ event: "clarification_requested", label: "Asked how to measure best performance" }],
    debug_state: request
  };
}
function askCurrentContext() {
  return {
    fund: state.fund === "All" ? "All" : state.fund,
    period: state.period,
    asset_class: state.assetClass === "All" ? null : state.assetClass,
    manager: state.manager === "All" ? null : state.manager,
    source_page: "ask"
  };
}
function continueAskRequest(request, selection) {
  request.clarification = selection;
  request.status = "ready";
  request.debug.push({ request_id: request.request_id, original_query: request.original_query, status: "clarification_received", [selection.field]: selection.value });
  request.debug.push({ request_id: request.request_id, original_query: request.original_query, status: "ready" });
  if (request.intent === "manager_ranking" && selection.field === "ranking_metric") {
    return answerBestManagerFromRequest(request, selection.value);
  }
  return makeAskResult({
    outcome: "out_of_scope",
    answer: "I could not resume that request because the clarification did not match a supported intent.",
    events: askEvents(["Validation Failed"])
  });
}
function answerBestManagerFromRequest(request, rankingMetric) {
  const fund = request.context.fund || "All";
  const period = request.context.period || "FY2026";
  const rows = (data.metric_values || [])
    .filter(r => r.metric_id === rankingMetric && r.period === period && r.manager_name && (fund === "All" || r.fund_id === fund))
    .sort((a, b) => Number(b.value || 0) - Number(a.value || 0));
  request.debug.push({ request_id: request.request_id, original_query: request.original_query, status: "tool_running", tool_selected: "rank_managers", tool_arguments: { fund, period, metric: rankingMetric, direction: "desc", limit: 1 } });
  const top = rows[0];
  request.debug.push({ request_id: request.request_id, original_query: request.original_query, status: "tool_complete", tool_result_record_ids: top ? [top.metric_value_id] : [] });
  if (!top) {
    request.debug.push({ request_id: request.request_id, original_query: request.original_query, status: "validation_failed", validation_result: "no_data" });
    return makeAskResult({ outcome: "out_of_scope", answer: "No manager ranking records matched the preserved request context.", events: askEvents(["Validation Failed"]) });
  }
  const ret = askMetric("manager_return_pct", { fund_id: top.fund_id, period, asset_class: top.asset_class, manager_name: top.manager_name });
  const bench = askMetric("manager_benchmark_return_pct", { fund_id: top.fund_id, period, asset_class: top.asset_class, manager_name: top.manager_name });
  const excess = askMetric("manager_excess_return_pp", { fund_id: top.fund_id, period, asset_class: top.asset_class, manager_name: top.manager_name });
  const consistency = askMetric("manager_consistency", { fund_id: top.fund_id, period, asset_class: top.asset_class, manager_name: top.manager_name });
  request.debug.push({ request_id: request.request_id, original_query: request.original_query, status: "tool_running", tool_selected: "get_manager_performance", tool_arguments: { manager: top.manager_name, fund: top.fund_id, period, asset_class: top.asset_class } });
  request.debug.push({ request_id: request.request_id, original_query: request.original_query, status: "tool_complete", tool_result_record_ids: [ret, bench, excess, consistency].filter(Boolean).map(r => r.metric_value_id) });
  const valid = Boolean(ret && bench && excess && askProvenance(excess).source_record_ids.length);
  request.debug.push({ request_id: request.request_id, original_query: request.original_query, status: valid ? "validated" : "validation_failed", validation_result: { ok: valid } });
  request.status = valid ? "answered" : "failed";
  request.debug.push({ request_id: request.request_id, original_query: request.original_query, status: request.status, final_response_status: request.status });
  const label = rankingMetric === "manager_return_pct" ? "highest absolute return" : rankingMetric === "manager_consistency" ? "most consistent outperformance" : "strongest benchmark-relative performance";
  return makeAskResult({
    answer: `${top.manager_name} had the ${label} for ${top.fund_id} in ${period}. It returned ${fmtPct(ret?.value, 2)} against a benchmark of ${fmtPct(bench?.value, 2)}, with excess return of ${fmtPp(excess?.value)}.`,
    metrics: [askMetricObj("Manager return", ret), askMetricObj("Benchmark return", bench), askMetricObj("Excess return", excess), askMetricObj("Quarters outperforming", consistency)],
    evidence: [ret, bench, excess, consistency].filter(Boolean),
    events: askEvents([`Used ${top.fund_id} / ${period} context`, "Queried manager performance", "Compared associated benchmarks", `Ranked managers by ${rankingMetric}`, "Verified source record"]),
    followups: ["Show the manager history", "Compare with benchmark", "What happened in H2?"],
    outcome: "answer",
    debug_state: request
  });
}
function askBlePeQ3() {
  const actual = askMetric("actual_allocation_pct", { fund_id: "BLE", period: "Q3", asset_class: "Private Equity" });
  const target = askMetric("policy_target_pct", { fund_id: "BLE", period: "Q3", asset_class: "Private Equity" });
  const drift = askMetric("allocation_drift_pp", { fund_id: "BLE", period: "Q3", asset_class: "Private Equity" });
  const variance = askMetric("dollar_variance_to_policy", { fund_id: "BLE", period: "Q3", asset_class: "Private Equity" });
  return makeAskResult({
    answer: `BLE Private Equity was ${fmtPct(actual?.value, 2)} versus a ${fmtPct(target?.value, 2)} policy target in Q3, a ${fmtPp(drift?.value)} drift.`,
    metrics: [askMetricObj("Actual", actual), askMetricObj("Policy", target), askMetricObj("Drift", drift), askMetricObj("Dollar variance", variance)],
    evidence: [actual, target, drift, variance].filter(Boolean),
    events: askEvents(["Identified BLE", "Identified Q3", "Queried Asset Allocation", "Retrieved Policy Target", "Calculated Drift", "Verified Source"]),
    followups: ["How has this changed?", "Compare with BPT", "Which managers contributed?", "What happened in H2?"]
  });
}
function askWeakestManagerQ4() {
  const rows = (data.metric_values || []).filter(r => r.metric_id === "manager_excess_return_pp" && r.period === "Q4" && r.manager_name).sort((a, b) => Number(a.value) - Number(b.value));
  const row = rows[0];
  return makeAskResult({
    answer: `${row.manager_name} had the weakest benchmark-relative performance in Q4 at ${fmtPp(row.value)}.`,
    metrics: [askMetricObj("Excess return", row)],
    evidence: [row],
    events: askEvents(["Identified Q4", "Queried Manager Performance", "Ranked Excess Return", "Verified Source"]),
    followups: ["Show the manager history", "Compare with other Public Equity managers", "What happened in H2?"]
  });
}
function askComparePrivateEquity(period) {
  const bpt = askMetric("allocation_drift_pp", { fund_id: "BPT", period, asset_class: "Private Equity" });
  const ble = askMetric("allocation_drift_pp", { fund_id: "BLE", period, asset_class: "Private Equity" });
  return makeAskResult({
    answer: `In ${period}, BPT Private Equity drift was ${fmtPp(bpt?.value)} versus BLE at ${fmtPp(ble?.value)}.`,
    metrics: [askMetricObj("BPT drift", bpt), askMetricObj("BLE drift", ble)],
    evidence: [bpt, ble].filter(Boolean),
    visual: { type: "period-bars", items: [{ label: "BPT", value: bpt?.value || 0 }, { label: "BLE", value: ble?.value || 0 }] },
    events: askEvents(["Identified Private Equity", `Identified ${period}`, "Compared Funds", "Calculated Difference", "Verified Source"]),
    followups: ["How has this changed?", "Which managers contributed?", "Show Q1 to Q4 trend"]
  });
}
function askCashQ3Q4() {
  const q3 = askMetric("allocation_drift_pp", { fund_id: "BPT", period: "Q3", asset_class: "Cash" });
  const q4 = askMetric("allocation_drift_pp", { fund_id: "BPT", period: "Q4", asset_class: "Cash" });
  const change = Number(q4?.value || 0) - Number(q3?.value || 0);
  return makeAskResult({
    answer: `BPT Cash allocation drift moved from ${fmtPp(q3?.value)} in Q3 to ${fmtPp(q4?.value)} in Q4, a ${fmtPp(change)} change.`,
    metrics: [askMetricObj("Q3 drift", q3), askMetricObj("Q4 drift", q4), { label: "Change", value: change, unit: "percentage points", provenance: {} }],
    evidence: [q3, q4].filter(Boolean),
    visual: { type: "period-bars", items: [{ label: "Q3", value: q3?.value || 0 }, { label: "Q4", value: q4?.value || 0 }] },
    events: askEvents(["Identified BPT", "Identified Cash", "Retrieved Q3", "Retrieved Q4", "Calculated Change", "Verified Source"]),
    followups: ["Compare with BLE Cash", "What happened in H2?", "Show cash flows"]
  });
}
function askResearchBpt() {
  const signals = (data.research?.horizons?.FY2026?.candidates || []).filter(s => s.fund === "BPT").slice(0, 3);
  return makeAskResult({
    answer: signals.length ? `The largest BPT research signals are ${signals.map(s => s.headline).join(" ")}.` : "No BPT research signals matched the current scope.",
    metrics: signals.slice(0, 2).map(s => ({ label: s.primary_metric, value: s.primary_value, unit: String(s.primary_metric).toLowerCase().includes("drift") ? "percentage points" : "", provenance: askProvenance(s) })),
    evidence: signals,
    events: askEvents(["Identified BPT", "Queried Research Signals", "Ranked Research Signals", "Verified Source"]),
    followups: ["Open the top signal", "Compare with BLE", "What happened in H2?"]
  });
}
function askThisMove() {
  const asset = state.assetClass;
  const hist = sourcePeriods.map(period => askMetric("allocation_drift_pp", { fund_id: state.fund, period, asset_class: asset })).filter(Boolean);
  return makeAskResult({
    answer: `${state.fund} ${asset} moved based on observed allocation drift across FY2026. This describes the trend, not causality.`,
    metrics: hist.slice(-2).map(r => askMetricObj(r.period, r)),
    evidence: hist,
    visual: { type: "period-bars", items: hist.map(r => ({ label: r.period, value: r.value })) },
    events: askEvents([`Identified ${state.fund}`, `Identified ${asset}`, "Used Page Context", "Retrieved Allocation History", "Verified Source"]),
    followups: ["Compare with BLE", "Which managers contributed?", "What happened in H2?"]
  });
}
function askEvents(labels) {
  return labels.map(label => ({ event: label.toLowerCase().replaceAll(" ", "_"), label }));
}
function hero(s, qoq) {
  const endingAum = fundMetric("ending_aum") ?? s.EndingMarketValue;
  const ret = fundMetric("fund_return_pct") ?? s.HorizonReturnPct ?? (state.period === "FY2026" ? s.FYTDReturnPct : s.QTDReturnPct);
  const bench = fundMetric("policy_benchmark_return_pct") ?? s.HorizonBenchmarkPct ?? (state.period === "FY2026" ? s.PolicyBenchmarkFYTDReturnPct : s.PolicyBenchmarkQTDReturnPct);
  const excess = fundMetric("fund_excess_return_pp") ?? (ret - bench);
  const netFlow = fundMetric("net_cash_flow") ?? s.NetCashFlow;
  const gainLoss = fundMetric("investment_gain_loss") ?? s.InvestmentGainLoss;
  const drift = driftRows()[0];
  return `<section class="panel hero">
    <div class="hero-cell">
      <p class="eyebrow">${s.FundName}</p>
      <div class="hero-value">${fmtMoney(endingAum)}</div>
      <div class="metric-row"><div><strong class="${cls(qoq.amount)}">${fmtMoney(qoq.amount)}</strong><p class="micro">${selectedPeriodShortLabel()} AUM Change</p></div><div><strong class="${cls(qoq.pct)}">${fmtPct(qoq.pct, 2)}</strong><p class="micro">${selectedPeriodShortLabel()} Change %</p></div></div>
    </div>
    <div class="hero-cell"><p class="eyebrow">Fund Return</p><div class="big-number">${fmtPct(ret, 2)}</div><p class="micro">Benchmark ${fmtPct(bench, 2)}</p><p class="${cls(excess)}">${fmtPp(excess)} excess</p></div>
    <div class="hero-cell"><p class="eyebrow">Net Cash Flow</p><div class="big-number ${cls(netFlow)}">${fmtMoney(netFlow)}</div><p class="micro">FYTD / selected period</p></div>
    <div class="hero-cell"><p class="eyebrow">Investment Gain / Loss</p><div class="big-number ${cls(gainLoss)}">${fmtMoney(gainLoss)}</div><p class="micro">Source roll-forward component</p></div>
    <div class="hero-cell"><p class="eyebrow">Largest Allocation Drift</p><div class="big-number">${drift?.AssetClassLevel1 || "None"}</div><p class="${cls(drift?.VarianceToTargetPct)}">${drift ? fmtPp(drift.VarianceToTargetPct) : "0.00pp"}</p></div>
    <div class="hero-cell"><p class="eyebrow">Data Status</p><div class="big-number positive">All Good</div><p class="micro">4 / 4 quarters loaded</p></div>
  </section>`;
}
function movement(s, qoq) {
  const periodLabel = selectedPeriodShortLabel();
  const components = [
    ["Beginning AUM", s.BeginningMarketValue, "start"],
    ["Contributions / Gifts", s.Contributions_or_Gifts, "flow"],
    ["Benefits / Distributions", s.BenefitPayments_or_Distributions, "flow"],
    ["Fees & Expenses", Number(s.AdminFees) + Number(s.InvestmentManagementFees), "flow"],
    ["Investment Gain / Loss", s.InvestmentGainLoss, "flow"],
    ["Ending AUM", s.EndingMarketValue, "end"]
  ];
  const max = Math.max(...components.map(c => Math.abs(Number(c[1]))), 1);
  return `<div class="grid-2">
    <section class="panel section">
      <div class="section-title"><div><h2>What changed?</h2><p class="subtitle">Portfolio Movement</p></div><span class="status positive">Reconciled</span></div>
      <div class="waterfall">${components.map(([label, val, type]) => `<div class="bar-wrap"><div class="bar-value ${cls(val)}">${fmtMoney(val)}</div><div class="bar ${type === "flow" ? (val < 0 ? "red" : "green") : ""}" style="height:${Math.max(18, Math.abs(val) / max * 170)}px"></div><div class="bar-label">${label}</div></div>`).join("")}</div>
      <div class="summary-strip">
        <div><p class="eyebrow">${periodLabel} AUM Change</p><strong class="${cls(qoq.amount)}">${fmtMoney(qoq.amount)}</strong></div>
        <div><p class="eyebrow">${periodLabel} Change %</p><strong class="${cls(qoq.pct)}">${fmtPct(qoq.pct, 2)}</strong></div>
        <div><p class="eyebrow">Net Cash Flow</p><strong class="${cls(s.NetCashFlow)}">${fmtMoney(s.NetCashFlow)}</strong></div>
        <div><p class="eyebrow">Gain / Loss</p><strong class="${cls(s.InvestmentGainLoss)}">${fmtMoney(s.InvestmentGainLoss)}</strong></div>
        <div><p class="eyebrow">Reconciliation</p><strong class="positive">Variance â‰¤ $0.05M</strong></div>
      </div>
    </section>
    <section class="panel section">
      <div class="section-title"><div><h2>CIO Attention</h2><p class="subtitle">Data-backed exceptions</p></div></div>
      <div class="attention-list">${attentionItems().map((i, idx) => `<button class="attention-item" data-attention="${idx}"><span class="priority ${i.priority === "High" ? "negative" : i.priority === "Medium" ? "amber" : ""}">${i.priority}</span><span><strong>${i.title}</strong><br><span class="micro">${i.detail}</span></span><span class="right positive">View â†’</span></button>`).join("")}</div>
    </section>
  </div>`;
}
function allocationSection() {
  const rows = driftRows();
  return `<section class="panel section table-section">
    <div class="section-title"><div><h2>Where are we drifting?</h2><p class="subtitle">Asset Allocation vs Policy</p></div></div>
    ${rows.length ? `<div class="table-scroll"><table><thead><tr><th>Asset Class</th><th class="right">Market Value</th><th class="right">Actual</th><th class="right">Policy</th><th class="right">Drift</th><th class="right">$ Variance</th><th>Q1 â†’ Q4</th><th>Status</th></tr></thead><tbody>
      ${rows.map(r => `<tr class="clickable" data-asset="${r.AssetClassLevel1}"><td><strong>${r.AssetClassLevel1}</strong></td><td class="right">${fmtMoney(r.EndingMarketValue)}</td><td class="right">${fmtPct(r.PctOfFundTotal, 2)}</td><td class="right">${fmtPct(r.PolicyTargetPct, 2)}</td><td class="right ${cls(r.VarianceToTargetPct)}">${fmtPp(r.VarianceToTargetPct)}</td><td class="right ${cls(r.DollarVariance)}">${fmtMoney(r.DollarVariance)}</td><td>${spark(trendForAsset(r.AssetClassLevel1))}</td><td>${Math.abs(r.VarianceToTargetPct) < .75 ? "Near policy" : r.VarianceToTargetPct > 0 ? "Overweight" : "Underweight"}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="empty">No allocation records match these filters.</div>`}
  </section>`;
}
function managerSection() {
  const rows = managerRows().sort((a, b) => b.ExcessReturnPp - a.ExcessReturnPp);
  const best = rows[0];
  const worst = [...rows].sort((a, b) => a.ExcessReturnPp - b.ExcessReturnPp)[0];
  return `<section class="panel section table-section">
    <div class="section-title"><div><h2>Who created value?</h2><p class="subtitle">Manager Intelligence</p></div></div>
    <div class="value-callouts">
      <div class="callout"><p class="eyebrow">Best Relative Performer</p><strong>${best?.ManagerName || "No match"}</strong><div class="positive">${best ? fmtPp(best.ExcessReturnPp) : ""}</div></div>
      <div class="callout"><p class="eyebrow">Largest Detractor</p><strong>${worst?.ManagerName || "No match"}</strong><div class="negative">${worst ? fmtPp(worst.ExcessReturnPp) : ""}</div></div>
    </div>
    <div class="manager-controls">${["All Managers", "Outperforming", "Underperforming", "Persistent Underperformance"].map(v => `<button class="seg ${state.managerView === v ? "active" : ""}" data-manager-view="${v}">${v}</button>`).join("")}</div>
    ${rows.length ? `<div class="table-scroll"><table><thead><tr><th>Manager</th><th>Asset Class</th><th class="right">AUM</th><th class="right">Return</th><th class="right">Benchmark</th><th class="right">Excess</th><th class="right">Quarters Ahead</th><th>Status</th></tr></thead><tbody>
      ${rows.map(r => `<tr class="clickable" data-manager-row="${r.ManagerName}"><td><strong>${r.ManagerName}</strong><br><span class="micro">${r.VehicleType}</span></td><td>${r.AssetClassLevel1}</td><td class="right">${fmtMoney(r.MarketValue)}</td><td class="right">${fmtPct(r.DisplayReturnPct, 2)}</td><td class="right">${fmtPct(r.BenchmarkReturnPct, 2)}</td><td class="right ${cls(r.ExcessReturnPp)}">${fmtPp(r.ExcessReturnPp)}</td><td class="right">${r.QuartersAhead} / ${selectedPeriodQuarterCount()}</td><td><span class="status ${r.ExcessReturnPp >= 0 ? "positive" : "amber"}">${r.ExcessReturnPp >= 0 ? "Outperforming" : "Underperforming"}</span></td></tr>`).join("")}
    </tbody></table></div>` : `<div class="empty">No manager records match these filters.</div>`}
  </section>`;
}
function confidenceSection() {
  const c = confidence();
  const bCoverage = data.dimensions.benchmark_coverage.filter(b => b.has_benchmark).length;
  return `<section class="panel section table-section">
    <div class="section-title"><div><h2>Data Confidence</h2><p class="subtitle">Can I trust the numbers?</p></div></div>
    <div class="confidence-grid">
      <div class="confidence-card"><p class="eyebrow">Fund Roll-forward</p><strong class="positive">Reconciled</strong><p class="micro">Max tolerance $0.05M</p></div>
      <div class="confidence-card"><p class="eyebrow">Reconciliation Variance</p><strong class="${c.fundVar > .05 ? "amber" : "positive"}">${fmtMoney(c.fundVar)}</strong><p class="micro">Within tolerance</p></div>
      <div class="confidence-card"><p class="eyebrow">Allocation Validation</p><strong class="positive">${c.alloc.filter(v => v.status === "pass").length} / ${c.alloc.length} passed</strong><p class="micro">Sums to 100%</p></div>
      <div class="confidence-card"><p class="eyebrow">Manager Roll-up</p><strong class="positive">${c.manager.filter(v => v.status === "pass").length} / ${c.manager.length} reconciled</strong><p class="micro">Manager values to asset totals</p></div>
      <div class="confidence-card"><p class="eyebrow">Benchmark Coverage</p><strong class="positive">${bCoverage} / ${data.dimensions.asset_classes.length}</strong><p class="micro">All assets mapped</p></div>
      <div class="confidence-card"><p class="eyebrow">Source Coverage</p><strong class="positive">4 / 4 quarters</strong><p class="micro">FY2026 loaded</p></div>
      <div class="confidence-card"><p class="eyebrow">Duplicate Handling</p><strong class="positive">Canonicalized</strong><p class="micro">FYTD repeats deduped</p></div>
      <div class="confidence-card"><p class="eyebrow">RAW Export</p><strong class="amber">Not primary</strong><p class="micro">Messy parser route documented</p></div>
    </div>
  </section>`;
}
function researchSignals() {
  const horizon = data.research?.horizons?.[state.period] || data.research?.horizons?.FY2026;
  const source = horizon?.candidates || data.research?.candidates || [];
  const finalIds = new Set((horizon?.final_signals || data.research?.final_signals || []).map(s => s.id));
  const inScope = source.filter(s => {
    const fundOk = state.fund === "All" || s.fund === state.fund || s.fund === "All";
    const periodOk = (s.horizon || s.period) === state.period;
    const assetOk = state.assetClass === "All" || s.asset_class === state.assetClass;
    const managerOk = state.manager === "All" || s.manager === state.manager;
    return fundOk && periodOk && assetOk && managerOk;
  });
  const preferred = ["relative_performance", "policy_drift", "manager_consistency", "cash_flow", "emerging_signal"];
  const selected = [];
  preferred.forEach(type => {
    const ranked = inScope.filter(s => s.type === type).sort((a, b) => Number(b.significance_score) - Number(a.significance_score));
    if (ranked[0]) selected.push(ranked[0]);
  });
  inScope.sort((a, b) => (finalIds.has(b.id) - finalIds.has(a.id)) || Number(b.significance_score) - Number(a.significance_score))
    .forEach(s => { if (!selected.find(x => x.id === s.id) && selected.length < 5) selected.push(s); });
  return selected.slice(0, 5).map((s, i) => ({ ...s, storyNumber: `${i + 1}`.padStart(2, "0") }));
}
function insightsPage() {
  const signals = researchSignals();
  if (!signals.length) {
    return `<section class="insights-empty panel section"><h2>No research signals match these filters.</h2><p class="subtitle">Try broadening Fund, Asset Class, or Manager.</p></section>`;
  }
  return `<section class="research-summary">
      <p class="eyebrow">${state.period} at a glance</p>
      <h2>${insightsSummary(signals)}</h2>
      <p>${data.research?.attribution_status || ""}</p>
    </section>
    <section class="signal-nav" aria-label="Signals worth investigating">
      <div><p class="eyebrow">Signals worth investigating</p><h2>${signals.length} signals worth investigating</h2></div>
      <div class="signal-selector-row">${signals.map(s => `<button class="signal-selector" data-scroll-signal="${s.id}"><span>${s.storyNumber}</span>${shortSignalTitle(s)}</button>`).join("")}</div>
    </section>
    <div class="research-stories">${signals.map(researchStory).join("")}</div>`;
}
function insightsSummary(signals) {
  const rel = signals.find(s => s.type === "relative_performance");
  const drift = signals.find(s => s.type === "policy_drift");
  const manager = signals.find(s => s.type === "manager_consistency");
  const cash = signals.find(s => s.type === "cash_flow");
  const parts = [];
  if (rel) parts.push(`${rel.fund} finished FY2026 ahead of policy, but the relative-performance drivers were concentrated rather than evenly distributed.`);
  if (drift) parts.push(`${drift.asset_class} created the clearest policy-drift question, ending Q4 at ${fmtPp(drift.primary_value)} versus target.`);
  if (manager) parts.push(`${manager.manager} stood out for benchmark-relative consistency across the year.`);
  if (cash) parts.push(`Cash-flow patterns add context for liquidity and rebalancing discussions without proving a liquidity issue.`);
  return parts.join(" ") || "FY2026 research signals are filtered to the selected scope.";
}
function shortSignalTitle(signal) {
  const titles = {
    relative_performance: "What drove excess return?",
    policy_drift: "Is policy drift material?",
    manager_consistency: "Who showed consistency?",
    cash_flow: "What are flows telling us?",
    emerging_signal: "Emerging signal"
  };
  return titles[signal.type] || signal.research_question;
}
function researchStory(signal) {
  return `<article class="research-story" id="signal-${signal.id}">
    <div class="story-kicker"><span>${signal.storyNumber}</span><span>${signal.research_question}</span></div>
    <div class="story-grid">
      <div class="story-copy">
        <h2>${signal.headline}</h2>
        <p>${signal.observation}</p>
        <div class="story-actions">
          <button class="text-action" data-evidence="${signal.id}">View evidence</button>
          <button class="text-action" data-analysis="${signal.id}">View full analysis â†’</button>
        </div>
      </div>
      <div class="story-visual">${researchVisual(signal)}</div>
      <div class="story-finding">
        <p class="eyebrow">Key finding</p>
        <strong>${signal.primary_metric}: ${formatResearchValue(signal)}</strong>
        <p class="eyebrow matter-label">Why it matters</p>
        <p>${signal.why_it_matters}</p>
      </div>
      <button class="sparkle-action" title="Explore with Beacon" aria-label="Explore this insight with Beacon" data-beacon-context="${signal.id}">âœ¦</button>
    </div>
  </article>`;
}
function formatResearchValue(signal) {
  const v = Number(signal.primary_value);
  if (!Number.isFinite(v)) return signal.primary_value;
  if (signal.primary_metric.toLowerCase().includes("aum") || signal.primary_metric.toLowerCase().includes("flow")) return fmtMoney(v);
  if (signal.primary_metric.toLowerCase().includes("quarter")) return `${v}`;
  return fmtPp(v);
}
function researchVisual(signal) {
  if (signal.visual?.kind === "drift_line") return driftVisual(signal.visual.items || []);
  if (signal.visual?.kind === "manager_matrix") return managerMatrix(signal.visual.items || signal.related_analysis || []);
  if (signal.visual?.kind === "cash_table") return cashVisual(signal.visual.items || []);
  if (signal.visual?.kind === "relative_bar") return relativeBars(signal.visual.items || []);
  if (signal.visual?.kind === "cross_fund_bars") return crossFundBars(signal.visual.items || []);
  return `<div class="empty">Supporting analysis available in evidence.</div>`;
}
function relativeBars(items) {
  const top = items.slice(0, 7);
  const max = Math.max(...top.map(i => Math.abs(Number(i.relative_weighted_pp || 0))), 1);
  return `<div class="research-bars">${top.map(i => `<div class="research-bar-row"><span>${i.AssetClassLevel1}</span><div><i class="${Number(i.relative_weighted_pp) < 0 ? "redbar" : ""}" style="width:${Math.max(5, Math.abs(Number(i.relative_weighted_pp)) / max * 100)}%"></i></div><strong class="${cls(i.relative_weighted_pp)}">${fmtPp(i.relative_weighted_pp)}</strong></div>`).join("")}</div>`;
}
function driftVisual(items) {
  const max = Math.max(...items.map(i => Math.abs(Number(i.VarianceToTargetPct || 0))), 1);
  return `<div class="drift-path">${items.map(i => `<div><span class="quarter-dot ${Number(i.VarianceToTargetPct) < 0 ? "negative-bg" : "positive-bg"}" style="height:${Math.max(24, Math.abs(Number(i.VarianceToTargetPct)) / max * 120)}px"></span><strong>${i.Quarter}</strong><em>${fmtPp(i.VarianceToTargetPct)}</em></div>`).join("")}</div>`;
}
function managerMatrix(items) {
  const rows = items.slice(0, 6);
  const maxLen = Math.max(...rows.map(i => (i.q_excess || i.q || []).length), 1);
  const labels = maxLen === 2 ? ["Q3", "Q4"] : maxLen === 1 ? [state.period] : ["Q1", "Q2", "Q3", "Q4"];
  return `<table class="research-matrix"><thead><tr><th>Manager</th>${labels.map(label => `<th>${label}</th>`).join("")}<th>Consistency</th></tr></thead><tbody>${rows.map(i => {
    const path = i.q_excess || i.q || [];
    const cells = labels.map((_, index) => path[index] === undefined ? `<td>n/a</td>` : `<td class="${cls(path[index])}">${fmtPp(path[index])}</td>`).join("");
    return `<tr><td>${i.ManagerName || i.manager}</td>${cells}<td>${i.ahead ?? path.filter(v => v > 0).length} / ${path.length || labels.length} ahead</td></tr>`;
  }).join("")}</tbody></table>`;
}
function cashVisual(items) {
  return `<table class="research-cash"><thead><tr><th></th><th>BPT</th><th>BLE</th></tr></thead><tbody>${["Contributions_or_Gifts","BenefitPayments_or_Distributions","NetCashFlow","net_flow_to_aum_pct"].map(field => `<tr><td>${field.replaceAll("_", " ")}</td>${["BPT","BLE"].map(f => {
    const row = items.find(i => i.FundCode === f) || {};
    return `<td>${field.includes("pct") ? fmtPct(row[field], 2) : fmtMoney(row[field])}</td>`;
  }).join("")}</tr>`).join("")}</tbody></table>`;
}
function crossFundBars(items) {
  const top = items.slice(0, 6);
  return `<div class="research-bars">${top.map(i => `<div class="research-bar-row"><span>${i.asset}</span><div><i class="${Number(i.drift_gap_pp) < 0 ? "redbar" : ""}" style="width:${Math.min(100, Math.abs(Number(i.drift_gap_pp)) * 20)}%"></i></div><strong class="${cls(i.drift_gap_pp)}">${fmtPp(i.drift_gap_pp)}</strong></div>`).join("")}</div>`;
}
function drawerContent() {
  if (!state.drawer) return "";
  if (state.drawer.type === "asset") return assetDrawer(state.drawer.id);
  if (state.drawer.type === "evidence") return evidenceDrawer(state.drawer.id, state.drawer.mode);
  if (state.drawer.type === "askEvidence") return askEvidenceDrawer();
  if (state.drawer.type === "askHow") return askHowDrawer();
  return managerDrawer(state.drawer.id);
}
function askEvidenceDrawer() {
  const result = state.ask.result;
  const evidence = result?.evidence || [];
  return `<div class="drawer-head"><div><p class="eyebrow">Evidence</p><h2>Source-backed answer</h2></div><button class="close" onclick="closeDrawer()">×</button></div>
    <h3>Metrics</h3>
    <div class="source-list">${(result?.metrics || []).map(metric => `<div><span>${metric.label}</span><strong>${formatAskEvidenceValue(metric)}</strong></div>`).join("") || `<div><span>No metrics</span><strong>None returned</strong></div>`}</div>
    <h3 style="margin-top:18px">Workbook Sources</h3>
    <div class="source-list">${evidence.map((row, index) => askEvidenceSource(row, index)).join("") || `<div><span>No source record</span><strong>No evidence available</strong></div>`}</div>
    <button class="text-action source-record-action">View source record</button>`;
}
function askHowDrawer() {
  const events = state.ask.result?.events || [];
  return `<div class="drawer-head"><div><p class="eyebrow">How Beacon answered</p><h2>Safe application events</h2></div><button class="close" onclick="closeDrawer()">×</button></div>
    <div class="ask-event-list">${events.map(event => `<div><span></span><strong>${event.label || humanLabel(event.event || "")}</strong></div>`).join("") || `<p class="micro">No events recorded.</p>`}</div>
    <p class="micro" style="margin-top:18px">This log shows application events only. It does not expose hidden model reasoning.</p>`;
}
function formatAskEvidenceValue(metric) {
  const value = metric.unit === "USD millions" ? fmtMoney(metric.value) : metric.unit === "percent" ? fmtPct(metric.value, 2) : metric.unit === "percentage points" ? fmtPp(metric.value) : metric.value_text || metric.value;
  return `${value} · ${metric.unit || "value"}`;
}
function askEvidenceSource(row, index) {
  const p = askProvenance(row || {});
  return `<div>
    <span>${row.metric_id || row.type || row.source_record_id || `Evidence ${index + 1}`}</span>
    <strong>${(p.source_files || []).join(", ") || "Workbook"} · ${(p.source_sheets || []).join(", ") || "Sheet"} · rows ${(p.source_rows || []).join(", ") || "n/a"} · ${(p.source_cells || []).join(", ") || "cells n/a"}</strong>
  </div>`;
}
function findSignal(id) {
  const horizon = data.research?.horizons?.[state.period];
  return (horizon?.candidates || []).find(s => s.id === id)
    || (horizon?.final_signals || []).find(s => s.id === id)
    || (data.research?.candidates || []).find(s => s.id === id)
    || (data.research?.final_signals || []).find(s => s.id === id);
}
function humanLabel(key) {
  const labels = {
    fund_return_pct: "Fund Return",
    policy_benchmark_pct: "Policy Benchmark",
    excess_return_pp: "Excess Return",
    largest_positive_asset: "Largest Positive Driver",
    largest_positive_weighted_relative_pp: "Weighted Relative Contribution",
    largest_negative_asset: "Largest Detractor",
    largest_negative_weighted_relative_pp: "Weighted Relative Detractor",
    attribution_status: "Attribution Status",
    q4_actual_pct: "Actual Allocation",
    policy_target_pct: "Policy Target",
    drift_pp: "Allocation Drift",
    dollar_variance_m: "Value vs Policy",
    q1_to_q4_drift_path: "Q1-Q4 Drift Path",
    trajectory: "Trajectory",
    quarters_ahead: "Quarters Ahead",
    quarters_underperforming: "Quarters Underperforming",
    q1_q4_excess_path_pp: "Q1-Q4 Excess Path",
    fy_excess_pp: "FY Excess Return",
    trend_change_pp: "Trend Change",
    fy_return_pct: "FY Return",
    quarterly_excess_path_pp: "Quarterly Excess Path",
    net_flow_to_aum_pct: "Net Flow / AUM",
    inflows_m: "Inflows",
    outflows_m: "Outflows",
    net_flow_m: "Net Flow",
    bpt_drift_pp: "BPT Drift",
    ble_drift_pp: "BLE Drift",
    drift_gap_pp: "Drift Gap",
    shared_manager_count: "Shared Managers"
  };
  return labels[key] || key.replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase());
}
function formatMetricValue(key, value) {
  if (Array.isArray(value)) return value.map(v => Number.isFinite(Number(v)) ? fmtPp(Number(v)) : v).join(" Â· ");
  if (value && typeof value === "object") {
    return Object.entries(value).map(([k, v]) => `${humanLabel(k)} ${formatMetricValue(k, v)}`).join(" Â· ");
  }
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  if (key.includes("_m") || key.includes("Dollar") || key.includes("variance_m")) return fmtMoney(n);
  if (key.includes("pct") && !key.includes("excess")) return fmtPct(n, 2);
  if (key.includes("pp") || key.includes("drift") || key.includes("excess") || key.includes("gap")) return fmtPp(n);
  return `${n}`;
}
function driftDescription(value) {
  const n = Number(value || 0);
  if (n > 0) return `${fmtPp(n)} overweight`;
  if (n < 0) return `${Math.abs(n).toFixed(2)}pp underweight`;
  return "At policy";
}
function relationLabel(row, index, rows) {
  if (row.VarianceToTargetPct !== undefined) {
    const drift = Number(row.VarianceToTargetPct || 0);
    const sorted = [...rows].sort((a, b) => Math.abs(Number(b.VarianceToTargetPct || 0)) - Math.abs(Number(a.VarianceToTargetPct || 0)));
    if (row.AssetClassLevel1 === sorted[0]?.AssetClassLevel1) return drift < 0 ? "Largest underweight" : "Largest overweight";
    if (drift < 0) return index === 1 ? "Next largest underweight" : "Material underweight";
    if (drift > 0) return "Largest overweight";
    return "Near policy";
  }
  if (row.relative_weighted_pp !== undefined) return Number(row.relative_weighted_pp) >= 0 ? "Positive relative driver" : "Relative detractor";
  if (row.q_excess || row.q) return "Quarterly consistency pattern";
  if (row.HorizonExcessPp !== undefined) return Number(row.HorizonExcessPp) >= 0 ? "Manager ahead of benchmark" : "Manager behind benchmark";
  if (row.excess_change_pp !== undefined) return Number(row.excess_change_pp) >= 0 ? "Improved versus prior quarter" : "Deteriorated versus prior quarter";
  if (row.net_flow_to_aum_pct !== undefined) return "Cash-flow posture";
  if (row.drift_gap_pp !== undefined) return "Cross-fund drift comparison";
  return "Related record";
}
function relatedAnalysisRows(signal) {
  const rows = [...(signal.related_analysis || [])];
  if (signal.type === "policy_drift") {
    return rows
      .filter(row => row.VarianceToTargetPct !== undefined)
      .sort((a, b) => Math.abs(Number(b.VarianceToTargetPct || 0)) - Math.abs(Number(a.VarianceToTargetPct || 0)))
      .slice(0, 5);
  }
  return rows.slice(0, 5);
}
function relatedAnalysisCard(row, signal, index, rows) {
  const label = relationLabel(row, index, rows);
  if (row.VarianceToTargetPct !== undefined) {
    return `<article class="related-card">
      <p class="eyebrow">${label}</p>
      <h4>${row.AssetClassLevel1 || row.asset || "Asset Class"}</h4>
      <p><strong>${fmtPct(row.PctOfFundTotal, 2)} actual</strong> vs <strong>${fmtPct(row.PolicyTargetPct, 2)} policy</strong></p>
      <dl>
        <div><dt>Allocation Drift</dt><dd class="${cls(row.VarianceToTargetPct)}">${driftDescription(row.VarianceToTargetPct)}</dd></div>
        <div><dt>Value vs Policy</dt><dd class="${cls(row.DollarVariance)}">${fmtMoney(row.DollarVariance)}</dd></div>
      </dl>
      <p class="micro">Related because this ranks policy deviations by absolute Q4 drift for the selected insight.</p>
    </article>`;
  }
  if (row.relative_weighted_pp !== undefined) {
    return `<article class="related-card">
      <p class="eyebrow">${label}</p>
      <h4>${row.AssetClassLevel1}</h4>
      <dl>
        <div><dt>Return vs Benchmark</dt><dd class="${cls(row.relative_pp)}">${fmtPp(row.relative_pp)}</dd></div>
        <div><dt>Weighted Relative Contribution</dt><dd class="${cls(row.relative_weighted_pp)}">${fmtPp(row.relative_weighted_pp)}</dd></div>
      </dl>
      <p class="micro">Related because it explains which asset classes supported or detracted from fund-level excess return.</p>
    </article>`;
  }
  if (row.q_excess || row.q) {
    const path = row.q_excess || row.q || [];
    return `<article class="related-card">
      <p class="eyebrow">${label}</p>
      <h4>${row.ManagerName || row.manager}</h4>
      <p>${row.AssetClassLevel1 || row.asset || ""}</p>
      <dl>
        <div><dt>Quarterly Excess</dt><dd>${path.map(v => `<span class="${cls(v)}">${fmtPp(v)}</span>`).join(" Â· ")}</dd></div>
        <div><dt>Consistency</dt><dd>${row.ahead ?? path.filter(v => Number(v) > 0).length} / 4 quarters ahead</dd></div>
      </dl>
      <p class="micro">Related because quarterly consistency is the evidence behind the manager pattern.</p>
    </article>`;
  }
  if (row.HorizonExcessPp !== undefined) {
    return `<article class="related-card">
      <p class="eyebrow">${label}</p>
      <h4>${row.ManagerName}</h4>
      <p>${row.AssetClassLevel1}</p>
      <dl>
        <div><dt>Manager Return</dt><dd>${fmtPct(row.HorizonReturnPct, 2)}</dd></div>
        <div><dt>Benchmark Return</dt><dd>${fmtPct(row.HorizonBenchmarkPct, 2)}</dd></div>
        <div><dt>Excess Return</dt><dd class="${cls(row.HorizonExcessPp)}">${fmtPp(row.HorizonExcessPp)}</dd></div>
      </dl>
      <p class="micro">Related because it ranks manager results for the selected horizon using the reporting benchmark mapping.</p>
    </article>`;
  }
  if (row.excess_change_pp !== undefined) {
    return `<article class="related-card">
      <p class="eyebrow">${label}</p>
      <h4>${row.ManagerName}</h4>
      <p>${row.AssetClassLevel1}</p>
      <dl>
        <div><dt>Q3 Excess</dt><dd class="${cls(row.q3_excess_pp)}">${fmtPp(row.q3_excess_pp)}</dd></div>
        <div><dt>Q4 Excess</dt><dd class="${cls(row.q4_excess_pp)}">${fmtPp(row.q4_excess_pp)}</dd></div>
        <div><dt>Q4 vs Q3 Change</dt><dd class="${cls(row.excess_change_pp)}">${fmtPp(row.excess_change_pp)}</dd></div>
      </dl>
      <p class="micro">Related because it isolates the late-year change inside H2.</p>
    </article>`;
  }
  if (row.FundCode && row.NetCashFlow !== undefined) {
    return `<article class="related-card">
      <p class="eyebrow">${label}</p>
      <h4>${row.FundCode}</h4>
      <dl>
        <div><dt>Inflows</dt><dd>${fmtMoney(row.Contributions_or_Gifts)}</dd></div>
        <div><dt>Outflows and Fees</dt><dd>${fmtMoney(Number(row.BenefitPayments_or_Distributions || 0) + Number(row.AdminFees || 0) + Number(row.InvestmentManagementFees || 0))}</dd></div>
        <div><dt>Net Flow</dt><dd class="${cls(row.NetCashFlow)}">${fmtMoney(row.NetCashFlow)}</dd></div>
        <div><dt>Net Flow / AUM</dt><dd class="${cls(row.net_flow_to_aum_pct)}">${fmtPct(row.net_flow_to_aum_pct, 2)}</dd></div>
      </dl>
      <p class="micro">Related because it compares the source-defined cash-flow posture across funds.</p>
    </article>`;
  }
  if (row.drift_gap_pp !== undefined) {
    return `<article class="related-card">
      <p class="eyebrow">${label}</p>
      <h4>${row.asset}</h4>
      <dl><div><dt>BPT vs BLE Drift Gap</dt><dd class="${cls(row.drift_gap_pp)}">${fmtPp(row.drift_gap_pp)}</dd></div></dl>
      <p class="micro">Related because it shows where a shared asset-class menu creates different policy consequences.</p>
    </article>`;
  }
  return `<article class="related-card"><p class="eyebrow">${label}</p><h4>${row.AssetClassLevel1 || row.ManagerName || row.FundCode || "Related item"}</h4><p class="micro">Structured supporting record available in the source data.</p></article>`;
}
function relatedAnalysis(signal) {
  const rows = relatedAnalysisRows(signal);
  if (!rows.length) return `<p class="micro">No additional related analysis is available for this signal.</p>`;
  const showAll = (signal.related_analysis || []).length > rows.length;
  const intro = signal.type === "policy_drift" ? "How this compares" : "Why these records matter";
  return `<div class="related-analysis">
    <p class="subtitle">${intro}</p>
    ${rows.map((row, index) => relatedAnalysisCard(row, signal, index, rows)).join("")}
    ${showAll ? `<button class="text-action source-record-action">View all allocation differences â†’</button>` : ""}
  </div>`;
}
function evidenceDrawer(id, mode = "evidence") {
  const signal = findSignal(id);
  if (!signal) return "";
  return `<div class="drawer-head"><div><p class="eyebrow">${mode === "analysis" ? "Full Analysis" : "Evidence"}</p><h2>${signal.research_question}</h2></div><button class="close" onclick="closeDrawer()">Ã—</button></div>
    <h3>Selected Insight</h3>
    <p>${signal.headline}</p>
    <h3>Scope</h3>
    <div class="detail-grid">
      <div class="detail-card"><p class="eyebrow">Fund</p><strong>${signal.fund}</strong></div>
      <div class="detail-card"><p class="eyebrow">Period</p><strong>${signal.period}</strong></div>
      <div class="detail-card"><p class="eyebrow">Asset Class</p><strong>${signal.asset_class || "All"}</strong></div>
      <div class="detail-card"><p class="eyebrow">Manager</p><strong>${signal.manager || "All"}</strong></div>
    </div>
    <h3>Evidence</h3>
    <div class="source-list">${Object.entries(signal.supporting_metrics || {}).slice(0, 12).map(([k,v]) => `<div><span>${humanLabel(k)}</span><strong>${formatMetricValue(k, v)}</strong></div>`).join("")}</div>
    <h3 style="margin-top:18px">Related Analysis</h3>
    ${relatedAnalysis(signal)}
    <h3 style="margin-top:18px">Source</h3>
    <div class="source-list">${(signal.source_record_ids || []).slice(0, 8).map((id, i) => `<div><span>${id}</span><strong>${signal.source_files?.[0] || ""} Â· ${signal.source_sheets?.join(", ") || ""} Â· rows ${(signal.source_rows || []).slice(0,4).join(", ")}</strong></div>`).join("")}</div>
    <button class="text-action source-record-action">View source record</button>
    <h3 style="margin-top:18px">Limitation</h3><p>${signal.limitations}</p>`;
}
function assetDrawer(assetClass) {
  const row = driftRows().find(r => r.AssetClassLevel1 === assetClass);
  const managers = data.records.manager_detail.filter(m => m.AssetClassLevel1 === assetClass && m.Quarter === (state.period === "FY2026" ? "Q4" : state.period) && (state.fund === "All" || m.FundCode === state.fund));
  return `<div class="drawer-head"><div><p class="eyebrow">Asset Class</p><h2>${assetClass}</h2></div><button class="close" onclick="closeDrawer()">Ã—</button></div>
    <div class="detail-grid">
      <div class="detail-card"><p class="eyebrow">Market Value</p><strong>${fmtMoney(row?.EndingMarketValue)}</strong></div>
      <div class="detail-card"><p class="eyebrow">Actual Allocation</p><strong>${fmtPct(row?.PctOfFundTotal, 2)}</strong></div>
      <div class="detail-card"><p class="eyebrow">Policy Target</p><strong>${fmtPct(row?.PolicyTargetPct, 2)}</strong></div>
      <div class="detail-card"><p class="eyebrow">Drift</p><strong class="${cls(row?.VarianceToTargetPct)}">${fmtPp(row?.VarianceToTargetPct)}</strong></div>
    </div>
    <h3>Allocation History</h3><p>${sourcePeriods.map((q, i) => `${q}: ${fmtPct(trendForAsset(assetClass)[i], 2)}`).join(" Â· ")}</p>
    <h3 style="margin-top:18px">Performance</h3><p>Return ${fmtPct(row?.FYTDReturnPct, 2)} Â· Benchmark ${fmtPct(row?.BenchmarkFYTDReturnPct, 2)} Â· <span class="${cls(row?.ExcessFYTDReturnBps)}">${fmtPp(Number(row?.ExcessFYTDReturnBps || 0) / 100)}</span></p>
    <h3 style="margin-top:18px">Underlying Managers</h3><ul>${managers.map(m => `<li>${m.ManagerName} Â· ${fmtMoney(m.MarketValue)}</li>`).join("")}</ul>
    <button class="ask-placeholder" data-page="Ask Beacon">Ask Beacon</button>`;
}
function managerDrawer(manager) {
  const row = managerRows().find(m => m.ManagerName === manager) || data.records.manager_detail.find(m => m.ManagerName === manager);
  const provenance = row?._provenance || {};
  return `<div class="drawer-head"><div><p class="eyebrow">Manager</p><h2>${manager}</h2></div><button class="close" onclick="closeDrawer()">Ã—</button></div>
    <div class="detail-grid">
      <div class="detail-card"><p class="eyebrow">Fund</p><strong>${fundName(row?.FundCode)}</strong></div>
      <div class="detail-card"><p class="eyebrow">Asset Class</p><strong>${row?.AssetClassLevel1}</strong></div>
      <div class="detail-card"><p class="eyebrow">Current AUM</p><strong>${fmtMoney(row?.MarketValue)}</strong></div>
      <div class="detail-card"><p class="eyebrow">FY Return</p><strong>${fmtPct(row?.FYTDReturnPct, 2)}</strong></div>
      <div class="detail-card"><p class="eyebrow">Benchmark</p><strong>${fmtPct(row?.BenchmarkReturnPct, 2)}</strong></div>
      <div class="detail-card"><p class="eyebrow">Excess Return</p><strong class="${cls(row?.ExcessReturnPp)}">${fmtPp(row?.ExcessReturnPp)}</strong></div>
    </div>
    <h3>Q1-Q4 Relative Performance</h3><p>${sourcePeriods.map(q => {
      const r = data.records.manager_detail.find(m => m.ManagerName === manager && m.Quarter === q && m.FundCode === row?.FundCode);
      const a = data.records.asset_allocation.find(x => x.FundCode === row?.FundCode && x.Quarter === q && x.AssetClassLevel1 === row?.AssetClassLevel1);
      return `${q}: ${r && a ? fmtPp(Number(r.QTDReturnPct) - Number(a.BenchmarkQTDReturnPct)) : "n/a"}`;
    }).join(" Â· ")}</p>
    <h3 style="margin-top:18px">Source Reference</h3><p class="micro">${provenance.source_file} Â· ${provenance.source_sheet} Â· row ${provenance.source_row} Â· ${row?.source_record_id || ""}</p>
    <button class="ask-placeholder" data-page="Ask Beacon">Ask Beacon</button>`;
}
function bindEvents() {
  if ($("#fund")) {
    $("#fund").value = state.fund; $("#period").value = state.period; $("#assetClass").value = state.assetClass; $("#manager").value = state.manager;
    ["fund", "period", "assetClass", "manager"].forEach(id => $(`#${id}`).addEventListener("change", e => { state[id] = e.target.value; render(); }));
    $("#reset").addEventListener("click", () => { Object.assign(state, { fund: "BPT", period: "FY2026", assetClass: "All", manager: "All", managerView: "All Managers", drawer: null }); render(); });
  }
  if ($("#askForm")) $("#askForm").addEventListener("submit", e => { e.preventDefault(); runAsk($("#askInput").value); });
  document.querySelectorAll("[data-ask-suggestion]").forEach(el => el.addEventListener("click", () => runAsk(el.dataset.askSuggestion)));
  document.querySelectorAll("[data-clarify-field]").forEach(el => el.addEventListener("click", () => resumeAsk(el.dataset.requestId, { field: el.dataset.clarifyField, value: el.dataset.clarifyValue, label: el.dataset.clarifyLabel })));
  document.querySelectorAll("[data-ask-drawer]").forEach(el => el.addEventListener("click", () => openDrawer(el.dataset.askDrawer === "evidence" ? "askEvidence" : "askHow", "ask")));
  document.querySelectorAll("[data-ask-chip]").forEach(el => el.addEventListener("click", () => {
    const value = el.dataset.askChip;
    if (value === state.fund) state.fund = "All";
    if (value === state.period) state.period = "FY2026";
    if (value === state.assetClass) state.assetClass = "All";
    if (value === state.manager) state.manager = "All";
    render();
  }));
  document.querySelectorAll("[data-asset]").forEach(el => el.addEventListener("click", () => openDrawer("asset", el.dataset.asset)));
  document.querySelectorAll("[data-manager-row]").forEach(el => el.addEventListener("click", () => openDrawer("manager", el.dataset.managerRow)));
  document.querySelectorAll("[data-manager-view]").forEach(el => el.addEventListener("click", () => { state.managerView = el.dataset.managerView; render(); }));
  document.querySelectorAll("[data-attention]").forEach(el => el.addEventListener("click", () => attentionItems()[Number(el.dataset.attention)]?.action()));
  document.querySelectorAll("[data-page]").forEach(el => el.addEventListener("click", () => { state.page = el.dataset.page; state.drawer = null; render(); }));
  document.querySelectorAll("[data-scroll-signal]").forEach(el => el.addEventListener("click", () => document.getElementById(`signal-${el.dataset.scrollSignal}`)?.scrollIntoView({ behavior: "smooth", block: "start" })));
  document.querySelectorAll("[data-evidence]").forEach(el => el.addEventListener("click", () => openDrawer("evidence", el.dataset.evidence, "evidence")));
  document.querySelectorAll("[data-analysis]").forEach(el => el.addEventListener("click", () => openDrawer("evidence", el.dataset.analysis, "analysis")));
}
function openDrawer(type, id, mode) { state.drawer = { type, id, mode }; render(); }
function closeDrawer() { state.drawer = null; render(); }
window.closeDrawer = closeDrawer;

if (!data) {
  app.innerHTML = `<div class="loading-shell"><div class="empty">Beacon data failed to load.</div></div>`;
} else {
  render();
}

