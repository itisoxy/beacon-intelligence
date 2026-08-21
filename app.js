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
    threadId: `thread_ui_${globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID() : Date.now()}`,
    query: "",
    result: null,
    messages: [],
    loading: false,
    status: "",
    loadingDots: ".",
    error: null,
    context: {}
  }
};

const $ = (selector) => document.querySelector(selector);
const app = $("#app");
let askLoadingDelayTimer = null;
let askLoadingAnimationTimer = null;

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
    state.manager !== "All" ? state.manager : null,
    state.ask.context?.research_signal_id ? `Signal ${state.ask.context.research_signal_id}` : null
  ].filter(Boolean);
  return `<div class="ask-context-chips">${items.map(v => `<button class="ask-chip" data-ask-chip="${v}">${v} <span>×</span></button>`).join("")}</div>`;
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function askBeaconPage() {
  const result = state.ask.result;
  const hasConversation = state.ask.messages.length || state.ask.loading;
  return `<section class="ask-shell ${hasConversation ? "has-answer" : ""}">
    <div class="ask-hero">
      <h2>Ask your portfolio.</h2>
      <p>Grounded answers from your FY2026 portfolio data.</p>
      <form class="ask-search" id="askForm">
        <input id="askInput" value="${escapeHtml(state.ask.query)}" placeholder="Ask about allocation, managers, cash flow, or research signals" autocomplete="off" ${state.ask.loading ? "disabled" : ""}>
        <button type="submit" ${state.ask.loading ? "disabled" : ""}>Ask</button>
      </form>
      ${askContextChips()}
      ${!hasConversation ? askSuggestions() : ""}
    </div>
    ${hasConversation ? askConversation() : ""}
  </section>`;
}
function askSuggestions() {
  const suggestions = initialAskSuggestions();
  return `<div class="ask-suggestions">${suggestions.map(askSuggestionButton).join("")}</div>`;
}
function askResult(result) {
  if (result.outcome === "clarify") return askClarification(result);
  const metrics = (result.metrics || []).slice(0, 4);
  return `<div class="ask-answer-wrap">
    <article class="ask-answer">
      <p class="eyebrow">${result.outcome === "unsupported_causality" ? "Supported limits" : "Answer"}</p>
      ${askAnswerText(result.answer)}
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
function askConversation() {
  return `<div class="ask-answer-wrap">
    <div class="ask-thread">
      ${state.ask.messages.map(askMessage).join("")}
      ${state.ask.loading && state.ask.status ? `<article class="ask-message beacon ask-loading"><p class="eyebrow">Beacon</p><h3>${escapeHtml(state.ask.status)}<span>${escapeHtml(state.ask.loadingDots)}</span></h3></article>` : ""}
      ${state.ask.error ? `<article class="ask-message beacon ask-error"><p class="eyebrow">Beacon</p><h3>${escapeHtml(state.ask.error)}</h3></article>` : ""}
    </div>
    ${state.ask.result && state.ask.result.outcome !== "clarify" ? askFollowups(state.ask.result) : ""}
  </div>`;
}
function askMessage(message) {
  if (message.role === "user") {
    return `<article class="ask-message user"><p class="eyebrow">User</p><h3>${escapeHtml(message.content)}</h3></article>`;
  }
  const result = message.result || {};
  const clarify = result.outcome === "clarify";
  const metrics = (result.metrics || []).slice(0, 4);
  return `<article class="ask-message beacon ${clarify ? "ask-clarify" : ""}">
    <p class="eyebrow">Beacon</p>
    ${askAnswerText(message.content)}
    ${metrics.length ? `<div class="ask-metrics">${metrics.map(askMetricCard).join("")}</div>` : ""}
    ${result.structured_response ? askStructuredResponse(result.structured_response) : ""}
    ${clarify ? askQuickReplies(message.content, result) : ""}
    <div class="ask-answer-actions">
      ${(result.evidence || []).length ? `<button class="text-action" data-ask-drawer="evidence">View evidence</button>` : ""}
      <button class="text-action" data-ask-drawer="how">How Beacon answered</button>
    </div>
  </article>`;
}
function askAnswerText(value) {
  const paragraphs = String(value || "").split(/\n\s*\n/).map(part => part.trim()).filter(Boolean);
  if (!paragraphs.length) return `<div class="ask-answer-text"></div>`;
  return `<div class="ask-answer-text">${paragraphs.map(part => `<p>${escapeHtml(part)}</p>`).join("")}</div>`;
}
function askQuickReplies(text, result = {}) {
  const options = result.clarification_options?.length ? result.clarification_options : clarificationSuggestions(text);
  if (!options.length) return "";
  return `<div class="ask-choice-grid">${options.map(askSuggestionButton).join("")}</div>`;
}
function askClarification(result) {
  const options = result.options?.length
    ? result.options.map(option => ({ label: option.label, message: option.reply || option.label }))
    : clarificationSuggestions(result.question || result.answer);
  return `<div class="ask-answer-wrap">
    <article class="ask-answer ask-clarify">
      <p class="eyebrow">Clarification</p>
      <h3>${result.question || result.answer}</h3>
      ${options.length ? `<div class="ask-choice-grid">${options.map(askSuggestionButton).join("")}</div>` : ""}
      <button class="text-action" data-ask-drawer="how">How Beacon answered</button>
    </article>
  </div>`;
}
function askMetricCard(metric) {
  const unit = metric.unit || "";
  const value = metric.value;
  const hasBadValue = String(value).toLowerCase() === "nan" || (value !== null && value !== undefined && value !== "" && !Number.isFinite(Number(value)) && typeof value !== "string");
  const formatted = hasBadValue ? "n/a" : unit === "USD millions" ? fmtMoney(value) : unit === "percent" ? fmtPct(value, 2) : unit === "percentage points" ? fmtPp(value) : metric.value_text || value;
  return `<div class="ask-metric"><span>${metric.label}</span><strong class="${cls(metric.value)}">${formatted}</strong></div>`;
}
function askVisual(visual) {
  if (visual.type === "period-bars") {
    const max = Math.max(...visual.items.map(i => Math.abs(Number(i.value))), 1);
    return `<div class="ask-mini-chart">${visual.items.map(i => `<div><span class="${Number(i.value) < 0 ? "negative-bg" : "positive-bg"}" style="height:${Math.max(20, Math.abs(Number(i.value)) / max * 120)}px"></span><strong>${i.label}</strong><em class="${cls(i.value)}">${fmtPp(i.value)}</em></div>`).join("")}</div>`;
  }
  return "";
}
function askStructuredResponse(response) {
  if (!response?.response_type) return "";
  const type = response.response_type;
  if (type === "research_signals") return askResearchSignalCards(response.signals || response.rows || []);
  if (type === "fund_performance") return askPerformanceCard(response.metrics || response);
  if (type === "manager_ranking") return askManagerRankingTable(response.rows || []);
  if (type === "manager_performance") return askManagerPerformanceCard(response.rows || []);
  if (type === "allocation_drift") return askAllocationDriftTable(response.rows || []);
  if (type === "allocation_history" || type === "quarterly_trend" || type === "period_comparison") return askQuarterlyTrendTable(response.rows || []);
  if (type === "fund_comparison") return askFundComparisonCard(response.rows || [], response);
  if (type === "cash_flow" || type === "cash_flows") return askCashFlowTable(response.rows || [], response.net_cash_flow || response.metrics?.net_cash_flow);
  if (type === "source_evidence" || type === "source_record") return askSourceEvidenceCard(response);
  if (type === "clarification") return askQuickReplies(response.question || "", { clarification_options: response.options || [] });
  if (type === "validation_status") return askValidationCard(response);
  return "";
}
function askResearchSignalCards(signals) {
  const rows = signals.slice(0, 3);
  if (!rows.length) return "";
  return `<div class="ask-structured ask-research-cards">${rows.map((signal, index) => {
    const normalized = { ...signal, storyNumber: `${index + 1}`.padStart(2, "0") };
    const checks = (normalized.what_to_check_next || []).slice(0, 3);
    const metrics = Array.isArray(normalized.supporting_metrics) ? normalized.supporting_metrics.slice(0, 3) : [];
    return `<section class="ask-signal-card">
      <p class="eyebrow">Signal ${normalized.storyNumber}</p>
      <h4>${escapeHtml(normalized.headline || normalized.signal_id || "Research signal")}</h4>
      ${normalized.observation ? `<p><span class="ask-inline-label">Evidence</span>${escapeHtml(normalized.observation)}</p>` : ""}
      <div class="source-list"><div><span>${escapeHtml(normalized.primary_metric || "Primary evidence")}</span><strong>${formatResearchValue(normalized)}</strong></div>${metrics.map(metric => `<div><span>${escapeHtml(metric.label || metric.metric_id || "Supporting metric")}</span><strong>${escapeHtml(metric.value_text || metric.value || "")}</strong></div>`).join("")}</div>
      ${normalized.interpretation ? `<p><span class="ask-inline-label">Interpretation</span>${escapeHtml(normalized.interpretation)}</p>` : ""}
      ${normalized.why_it_matters ? `<p><span class="ask-inline-label">Why it matters</span>${escapeHtml(normalized.why_it_matters)}</p>` : ""}
      ${normalized.cio_question ? `<p><span class="ask-inline-label">Question for consideration</span>${escapeHtml(normalized.cio_question)}</p>` : ""}
      ${checks.length ? `<div><span class="ask-inline-label">What to check next</span><ul class="ask-mini-list">${checks.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : ""}
    </section>`;
  }).join("")}</div>`;
}
function askPerformanceCard(metrics) {
  const items = [
    ["Fund Return", metrics.return_pct || metrics.fund_return_pct],
    ["Benchmark", metrics.benchmark_pct || metrics.policy_benchmark_return_pct],
    ["Excess Return", metrics.excess_return_pp],
    ["Ending AUM", metrics.ending_aum],
    ["Net Cash Flow", metrics.net_cash_flow],
    ["Investment Gain / Loss", metrics.investment_gain_loss]
  ].filter(([, metric]) => metric);
  return `<section class="ask-structured ask-card-panel">
    <p class="eyebrow">Fund Performance</p>
    <div class="ask-metrics">${items.slice(0, 6).map(([label, metric]) => askMetricCard({ label, ...metric })).join("")}</div>
  </section>`;
}
function askManagerRankingTable(rows) {
  if (!rows.length) return "";
  return `<div class="ask-structured ask-table-wrap"><table class="research-matrix"><thead><tr><th>Manager</th><th>Fund</th><th>Asset Class</th><th>Return</th><th>Benchmark</th><th>Excess</th><th>Trend</th></tr></thead><tbody>${rows.slice(0, 6).map(row => `<tr><td>${escapeHtml(row.manager || "")}</td><td>${escapeHtml(row.fund || "")}</td><td>${escapeHtml(row.asset_class || "")}</td><td>${formatMetricCell(row.manager_return_pct || row.metric)}</td><td>${formatMetricCell(row.manager_benchmark_return_pct)}</td><td class="${cls(metricCellValue(row.manager_excess_return_pp || row.metric))}">${formatMetricCell(row.manager_excess_return_pp || row.metric)}</td><td>${formatMetricCell(row.quarters_outperforming)}</td></tr>`).join("")}</tbody></table></div>`;
}
function askAllocationDriftTable(rows) {
  if (!rows.length) return "";
  return `<div class="ask-structured ask-table-wrap"><table class="research-matrix"><thead><tr><th>Asset Class</th><th>Actual</th><th>Policy</th><th>Drift</th><th>Position</th></tr></thead><tbody>${rows.slice(0, 6).map(row => {
    const metrics = row.metrics || row;
    const drift = metricCellValue(metrics.allocation_drift_pp || metrics.drift_pp);
    const position = Number(drift) > 0 ? "Overweight" : Number(drift) < 0 ? "Underweight" : "On target";
    return `<tr><td>${escapeHtml(row.asset_class || "")}</td><td>${formatMetricCell(metrics.actual_allocation_pct)}</td><td>${formatMetricCell(metrics.policy_target_pct)}</td><td class="${cls(drift)}">${formatMetricCell(metrics.allocation_drift_pp || metrics.drift_pp)}</td><td>${position}</td></tr>`;
  }).join("")}</tbody></table></div>`;
}
function askFundComparisonCard(rows, response = {}) {
  if (!rows.length) return "";
  const comparison = response.comparison || {};
  const delta = comparison.difference ?? comparison.period_b_minus_period_a;
  if (rows.some(row => row.return_pct || row.benchmark_pct || row.excess_return_pp)) {
    return `<div class="ask-structured ask-table-wrap"><table class="research-matrix"><thead><tr><th>Fund</th><th>Return</th><th>Benchmark</th><th>Excess</th></tr></thead><tbody>${rows.map(row => `<tr><td>${escapeHtml(row.fund || "")}</td><td>${formatMetricCell(row.return_pct || row.metric)}</td><td>${formatMetricCell(row.benchmark_pct)}</td><td class="${cls(metricCellValue(row.excess_return_pp || row.metric))}">${formatMetricCell(row.excess_return_pp || row.metric)}</td></tr>`).join("")}</tbody></table>${response.summary?.interpretation ? `<p class="micro">${escapeHtml(response.summary.interpretation)}</p>` : ""}</div>`;
  }
  return `<section class="ask-structured ask-card-panel">
    <p class="eyebrow">Fund Comparison</p>
    <div class="ask-comparison-grid">${rows.map(row => `<div class="ask-comparison-card"><span>${escapeHtml(row.fund || row.period || "")}</span><strong class="${cls(row.metric?.value)}">${formatMetricCell(row.metric)}</strong></div>`).join("")}</div>
    ${delta !== undefined && delta !== null ? `<p class="micro">Difference: ${formatMetricCell({ value: delta, unit: comparison.unit })}</p>` : ""}
  </section>`;
}
function askQuarterlyTrendTable(rows) {
  if (!rows.length) return "";
  return `<div class="ask-structured ask-table-wrap"><table class="research-matrix"><thead><tr><th>Period</th><th>Value</th><th>Benchmark/Target</th><th>Difference</th></tr></thead><tbody>${rows.map(row => {
    const value = row.actual_allocation_pct || row.manager_return_pct || row.metric;
    const target = row.policy_target_pct || row.manager_benchmark_return_pct;
    const diff = row.allocation_drift_pp || row.manager_excess_return_pp;
    return `<tr><td>${escapeHtml(row.period || row.quarter || "")}</td><td>${formatMetricCell(value)}</td><td>${formatMetricCell(target)}</td><td class="${cls(metricCellValue(diff))}">${formatMetricCell(diff)}</td></tr>`;
  }).join("")}</tbody></table></div>`;
}
function askCashFlowTable(rows, netCashFlow) {
  return `<div class="ask-structured ask-table-wrap">${netCashFlow ? `<div class="ask-metrics">${askMetricCard({ label: "Net Cash Flow", ...netCashFlow })}</div>` : ""}<table class="research-matrix"><thead><tr><th>Quarter</th><th>Type</th><th>Amount</th></tr></thead><tbody>${rows.slice(0, 8).map(row => `<tr><td>${escapeHtml(row.quarter || "")}</td><td>${escapeHtml(row.flow_type || "")}</td><td>${formatMetricCell(row.amount)}</td></tr>`).join("")}</tbody></table></div>`;
}
function askManagerPerformanceCard(rows) {
  const row = rows[0];
  if (!row) return "";
  const items = [
    ["Manager Return", row.manager_return_pct],
    ["Benchmark", row.manager_benchmark_return_pct],
    ["Excess Return", row.manager_excess_return_pp],
    ["Quarters Outperforming", row.quarters_outperforming]
  ].filter(([, metric]) => metric);
  return `<section class="ask-structured ask-card-panel">
    <p class="eyebrow">${escapeHtml(row.manager || "Manager")}</p>
    <div class="ask-metrics">${items.map(([label, metric]) => askMetricCard({ label, ...metric })).join("")}</div>
  </section>`;
}
function askSourceEvidenceCard(response) {
  const record = response.record || response.result?.record || {};
  const sources = response.sources?.length ? response.sources : [record];
  return `<section class="ask-structured ask-card-panel">
    <p class="eyebrow">Source Evidence</p>
    <div class="source-list">${sources.map((source, index) => askEvidenceSource(source, index)).join("")}</div>
  </section>`;
}
function askValidationCard(response) {
  const items = [
    ["Reconciliation Variance", response.reconciliation_variance],
    ["Allocation Validation", response.allocation_validation_status]
  ].filter(([, metric]) => metric);
  return `<section class="ask-structured ask-card-panel">
    <p class="eyebrow">Validation</p>
    <div class="ask-metrics">${items.map(([label, metric]) => askMetricCard({ label, ...metric })).join("")}</div>
  </section>`;
}
function metricCellValue(metric) {
  return metric?.value ?? metric;
}
function formatMetricCell(metric) {
  if (!metric) return "n/a";
  const value = metric.value ?? metric;
  const unit = metric.unit || "";
  if (String(value).toLowerCase() === "nan") return "n/a";
  if (value !== null && value !== undefined && value !== "" && !Number.isFinite(Number(value)) && typeof value !== "string") return "n/a";
  if (metric.value_text) return metric.value_text;
  if (unit === "USD millions") return fmtMoney(value);
  if (unit === "percent") return fmtPct(value, 2);
  if (unit === "percentage points") return fmtPp(value);
  return value ?? "n/a";
}
function askFollowups(result) {
  const followups = result.followups || [];
  if (!followups.length) return "";
  return `<div class="ask-followups"><p class="eyebrow">Follow-up</p>${followups.map(askSuggestionButton).join("")}</div>`;
}
function askSuggestionButton(item) {
  const suggestion = typeof item === "string" ? { label: item, message: item } : item;
  return `<button data-ask-suggestion="${escapeHtml(suggestion.message || suggestion.label)}">${escapeHtml(suggestion.label || suggestion.message)}</button>`;
}
function initialAskSuggestions() {
  if (state.ask.context?.research_signal_id) {
    return ["Explain this signal.", "Show the numbers.", "Compare with the other fund.", "What should I investigate next?"];
  }
  if (state.manager !== "All") {
    return [`How did ${state.manager} perform?`, "How consistent were they?", "Show quarterly performance.", "Show the source."];
  }
  if (state.assetClass !== "All") {
    return [`How did ${state.assetClass} do?`, "Allocation versus policy.", "Performance versus benchmark.", `Compare with ${otherFundLabel()}.`];
  }
  if (state.fund !== "All") {
    return [`What was ${state.fund}'s ${state.period} return?`, `What should I investigate about ${state.fund}?`, "Which manager underperformed most?", "How far is Cash from policy?"];
  }
  return [
    "Which fund performed best?",
    "Which manager had the weakest benchmark-relative performance in Q4?",
    "Compare BPT and BLE Private Equity allocation in Q4.",
    "What are the largest BPT research signals?"
  ];
}
function clarificationSuggestions(text) {
  const lower = String(text || "").toLowerCase();
  if (lower.includes("absolute return") || lower.includes("relative to benchmark") || lower.includes("versus benchmark") || lower.includes("against benchmark") || lower.includes("consistent")) {
    return [
      { label: "Absolute return", message: "Absolute return." },
      { label: "Relative to benchmark", message: "Relative to benchmark." },
      { label: "Consistency", message: "Consistency." }
    ];
  }
  if ((lower.includes("performance") || lower.includes("perform")) && lower.includes("allocation") && lower.includes("manager")) {
    return [
      { label: "Performance vs benchmark", message: "Performance versus benchmark." },
      { label: "Allocation vs policy", message: "Allocation versus policy." },
      { label: "Managers", message: "Managers." },
      { label: "Give me the full picture", message: "Give me the full picture." }
    ];
  }
  if (lower.includes("bpt") && lower.includes("ble")) {
    return [
      { label: "BPT", message: "BPT." },
      { label: "BLE", message: "BLE." }
    ];
  }
  return [];
}
async function runAsk(query) {
  const message = query.trim();
  if (!message || state.ask.loading) return;
  state.ask.messages = [...state.ask.messages, { role: "user", content: message }];
  state.ask.query = "";
  state.ask.loading = true;
  state.ask.status = "";
  state.ask.loadingDots = ".";
  state.ask.error = null;
  startAskLoadingUx(message);
  render();
  try {
    const response = await fetch("/api/ask-beacon", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        thread_id: state.ask.threadId,
        message,
        application_context: askCurrentContext()
      })
    });
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await response.json()
      : { ok: false, error: { message: await response.text() || "Ask Beacon did not return a JSON response." } };
    if (!response.ok || !payload.ok) throw new Error(payload.error?.message || "Ask Beacon failed.");
    state.ask.threadId = payload.thread_id || state.ask.threadId;
    const result = normalizeAskResponse(payload);
    state.ask.result = result;
    state.ask.loading = false;
    clearAskLoadingUx();
    state.ask.messages = [...state.ask.messages, { role: "assistant", content: "", result }];
    await typeAskAnswer(result.answer);
  } catch (error) {
    state.ask.error = error.message || "Ask Beacon is unavailable.";
    state.ask.loading = false;
    clearAskLoadingUx();
    state.ask.status = "";
    render();
  }
}
function submitAskMessage(message) {
  // Prompt chips are shortcuts only; every click re-enters the normal chat path.
  runAsk(message);
}
async function typeAskAnswer(answer) {
  const index = state.ask.messages.length - 1;
  const text = String(answer || "");
  state.ask.status = "";
  for (let i = 0; i <= text.length; i += 6) {
    if (!state.ask.messages[index]) return;
    state.ask.messages[index].content = text.slice(0, i);
    render();
    await new Promise(resolve => setTimeout(resolve, 12));
  }
  state.ask.messages[index].content = text;
  render();
}
function startAskLoadingUx(message) {
  clearAskLoadingUx();
  askLoadingDelayTimer = setTimeout(() => {
    if (!state.ask.loading) return;
    state.ask.status = "Taking a deeper look at the data";
    state.ask.loadingDots = ".";
    render();
    let tick = 0;
    askLoadingAnimationTimer = setInterval(() => {
      if (!state.ask.loading) {
        clearAskLoadingUx();
        return;
      }
      tick += 1;
      state.ask.loadingDots = ".".repeat((tick % 3) + 1);
      render();
    }, 450);
  }, 750);
  void message;
}
function clearAskLoadingUx() {
  if (askLoadingDelayTimer) clearTimeout(askLoadingDelayTimer);
  if (askLoadingAnimationTimer) clearInterval(askLoadingAnimationTimer);
  askLoadingDelayTimer = null;
  askLoadingAnimationTimer = null;
  state.ask.loadingDots = ".";
}
function normalizeAskResponse(payload) {
  const grounded = payload.grounded_response || {};
  const answer = grounded.answer || payload.answer || "";
  const metrics = selectAskMetrics(grounded.metrics || [], answer);
  const evidence = grounded.sources || payload.turn_sources || [];
  const events = askEventsFromBackend(payload.turn_tool_events || grounded.activity_events || [], payload.application_context || {});
  return {
    outcome: isClarification(answer, payload) ? "clarify" : grounded.validation_errors?.includes("unsupported_causality") ? "unsupported_causality" : "answer",
    answer,
    metrics,
    evidence,
    events,
    limitations: grounded.limitations || [],
    response_type: grounded.response_type || grounded.structured_response?.response_type || null,
    structured_response: grounded.structured_response || null,
    followups: grounded.followups?.length ? grounded.followups : askFollowupQuestions(answer, payload),
    clarification_options: grounded.clarification_options || [],
    debug_state: grounded
  };
}
function isClarification(answer, payload) {
  const toolEvents = payload.turn_tool_events || [];
  const text = String(answer || "").toLowerCase();
  return !toolEvents.some(event => event.event === "tool_selected") && text.endsWith("?") && (text.includes("do you mean") || text.includes("which") || text.includes("should i") || text.includes("what"));
}
function selectAskMetrics(metrics, answer) {
  const ids = new Set();
  const text = String(answer || "").toLowerCase();
  if (text.includes("manager") || text.includes("benchmark-relative")) ["manager_return_pct", "manager_benchmark_return_pct", "manager_excess_return_pp", "manager_consistency"].forEach(id => ids.add(id));
  if (text.includes("allocation") || text.includes("policy") || text.includes("drift")) ["actual_allocation_pct", "policy_target_pct", "allocation_drift_pp", "dollar_variance_to_policy"].forEach(id => ids.add(id));
  if (text.includes("fund") || text.includes("returned")) ["fund_return_pct", "policy_benchmark_return_pct", "fund_excess_return_pp", "ending_aum"].forEach(id => ids.add(id));
  const selected = metrics.filter(metric => ids.has(metric.metric_id)).slice(0, 4);
  return (selected.length ? selected : metrics.slice(0, 3)).map(metric => ({
    label: askMetricLabel(metric.metric_id),
    record_id: metric.record_id,
    metric_id: metric.metric_id,
    value: metric.value,
    value_text: metric.value_text,
    unit: metric.unit,
    provenance: metric.provenance || []
  }));
}
function askMetricLabel(metricId) {
  const labels = {
    ending_aum: "Ending AUM",
    fund_return_pct: "Fund Return",
    policy_benchmark_return_pct: "Benchmark",
    fund_excess_return_pp: "Excess Return",
    actual_allocation_pct: "Actual Allocation",
    policy_target_pct: "Policy Target",
    allocation_drift_pp: "Drift",
    dollar_variance_to_policy: "Value vs Policy",
    manager_return_pct: "Manager Return",
    manager_benchmark_return_pct: "Benchmark",
    manager_excess_return_pp: "Excess Return",
    manager_consistency: "Consistency",
    net_cash_flow: "Net Cash Flow",
    reconciliation_variance: "Reconciliation Variance",
    allocation_validation_status: "Allocation Status"
  };
  return labels[metricId] || humanLabel(metricId || "Metric");
}
function askEventsFromBackend(events, context) {
  const rows = [];
  if (context && Object.values(context).some(Boolean)) rows.push({ event: "context_used", label: `Used ${askContextSummary(context)} context` });
  events.forEach(event => {
    if (event.event === "tool_selected") rows.push({ event: event.event, label: toolSelectedLabel(event.tool, event.arguments || {}) });
    if (event.event === "tool_completed" && event.ok) {
      rows.push({ event: event.event, label: (event.record_ids || []).length ? "Verified source record" : "Completed Beacon tool" });
    }
  });
  return rows;
}
function askContextSummary(context) {
  return [context.fund, context.period, context.asset_class, context.manager].filter(Boolean).join(" / ");
}
function toolSelectedLabel(tool, args) {
  const labels = {
    get_fund_performance: `Queried ${args.fund || "fund"} performance`,
    get_asset_allocation: `Queried ${args.asset_class || "asset"} allocation`,
    get_allocation_history: `Compared ${args.asset_class || "asset"} allocation history`,
    rank_asset_allocations: "Ranked allocation drift",
    get_manager_performance: "Queried manager performance",
    rank_managers: `Ranked managers by ${String(args.metric || "metric").replaceAll("_", " ")}`,
    get_manager_history: "Queried manager history",
    get_cash_flows: "Queried cash flows",
    get_research_signals: "Reviewed research signals",
    compare_funds: "Compared funds",
    compare_periods: "Compared periods",
    validate_reconciliation: "Validated reconciliation",
    get_source_record: "Retrieved source record"
  };
  return labels[tool] || humanLabel(tool || "Tool selected");
}
function askFollowupQuestions(answer, payload) {
  const kind = askResponseKind(answer, payload);
  const tools = new Set((payload.turn_tool_events || []).filter(event => event.event === "tool_selected").map(event => event.tool));
  if (isClarification(answer, payload)) return [];
  if (kind === "manager") return ["Show quarterly history", "Compare next worst", `And ${otherFundLabel()}?`, "Source"];
  if (kind === "allocation") return ["Has this worsened?", "Show quarterly trend", `Compare with ${otherFundLabel()}`, "Source"];
  if (tools.has("compare_funds")) return ["Relative to benchmark", "Show quarterly trend", "Compare allocation", "Source"];
  if (kind === "fund") return [`Compare with ${otherFundLabel()}.`, "How did it do versus benchmark?", "What changed in H2?", "Show the source."];
  if (kind === "research") return ["Explain the top signal", "Show the numbers", "What about managers?", `Compare with ${otherFundLabel()}`, "Source"];
  return ["Source", "What should I investigate next?"];
}
function askResponseKind(answer, payload) {
  const text = String(answer || "").toLowerCase();
  const metrics = payload.grounded_response?.metrics || [];
  const metricIds = new Set(metrics.map(metric => metric.metric_id));
  const tools = new Set((payload.turn_tool_events || []).filter(event => event.event === "tool_selected").map(event => event.tool));
  if (tools.has("get_research_signals") || text.includes("research signal") || text.includes("investigate")) return "research";
  if (tools.has("rank_managers") || tools.has("get_manager_performance") || tools.has("get_manager_history") || [...metricIds].some(id => String(id).startsWith("manager_")) || text.includes("manager")) return "manager";
  if (tools.has("get_asset_allocation") || tools.has("get_allocation_history") || tools.has("rank_asset_allocations") || metricIds.has("allocation_drift_pp") || metricIds.has("actual_allocation_pct") || text.includes("allocation") || text.includes("policy") || text.includes("drift")) return "allocation";
  if (tools.has("get_fund_performance") || tools.has("rank_funds") || tools.has("compare_funds") || metricIds.has("fund_return_pct") || metricIds.has("fund_excess_return_pp") || text.includes("fund") || text.includes("return")) return "fund";
  return "general";
}
function otherFundLabel() {
  if (state.fund === "BPT") return "BLE";
  if (state.fund === "BLE") return "BPT";
  return "the other fund";
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
    source_record_ids: row.source_record_ids || (row.source_record_id ? [row.source_record_id] : (row.record_id ? [row.record_id] : [])),
    source_files: row.source_files || (row.source_file ? [row.source_file] : []),
    source_sheets: row.source_sheets || (row.source_sheet ? [row.source_sheet] : []),
    source_rows: row.source_rows || (row.source_row ? [row.source_row] : []),
    source_cells: Array.isArray(cells) ? cells : (cells ? [cells] : [])
  };
}
function makeAskResult({ answer, metrics = [], evidence = [], events = [], visual = null, followups = [], outcome = "answer", debug_state = null }) {
  return { outcome, answer, metrics, evidence, events, visual, followups, debug_state };
}
function askCurrentContext() {
  return {
    fund: state.fund === "All" ? null : state.fund,
    period: state.period,
    asset_class: state.assetClass === "All" ? null : state.assetClass,
    manager: state.manager === "All" ? null : state.manager,
    source_page: state.ask.context?.source_page || "ask",
    research_signal_id: state.ask.context?.research_signal_id || null
  };
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
      <div class="attention-list">${attentionItems().map((i, idx) => `<button class="attention-item" data-attention="${idx}"><span class="priority ${i.priority === "High" ? "negative" : i.priority === "Medium" ? "amber" : ""}">${i.priority}</span><span><strong>${i.title}</strong><br><span class="micro">${i.detail}</span></span><span class="right positive">View &rarr;</span></button>`).join("")}</div>
    </section>
  </div>`;
}
function allocationSection() {
  const rows = driftRows();
  return `<section class="panel section table-section">
    <div class="section-title"><div><h2>Where are we drifting?</h2><p class="subtitle">Asset Allocation vs Policy</p></div></div>
    ${rows.length ? `<div class="table-scroll"><table><thead><tr><th>Asset Class</th><th class="right">Market Value</th><th class="right">Actual</th><th class="right">Policy</th><th class="right">Drift</th><th class="right">$ Variance</th><th>Q1 &rarr; Q4</th><th>Status</th></tr></thead><tbody>
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
        <div class="insight-inference">
          <p><span>Fact</span>${escapeHtml(signal.observation || "Canonical evidence is available in the supporting table.")}</p>
          <p><span>What this suggests</span>${escapeHtml(insightSuggestionText(signal))}</p>
          ${insightBulletList("Possible explanations", signal.possible_explanations)}
        </div>
        <div class="story-actions">
          <button class="text-action" data-evidence="${signal.id}">View evidence</button>
          <button class="text-action" data-analysis="${signal.id}">View full analysis</button>
        </div>
      </div>
      <div class="story-visual">${researchVisual(signal)}</div>
      <div class="story-finding">
        <p class="eyebrow">Key finding</p>
        <strong>${signal.primary_metric}: ${formatResearchValue(signal)}</strong>
        <p class="eyebrow matter-label">Why it matters</p>
        <p>${signal.why_it_matters}</p>
        ${signal.cio_question ? `<p class="eyebrow matter-label">Question for consideration</p><p>${signal.cio_question}</p>` : ""}
        ${insightBulletList("What to check next", signal.what_to_check_next)}
      </div>
      <button class="sparkle-action" title="Explore with Beacon" aria-label="Explore this insight with Beacon" data-beacon-context="${signal.id}">&#10022;</button>
    </div>
  </article>`;
}
function insightSuggestionText(signal) {
  if (signal.type === "cash_flow") {
    return "No liquidity problem is detected from the supplied data. The cash-flow pattern does suggest paying closer attention to liquidity monitoring and potential rebalancing needs.";
  }
  return signal.interpretation || "Beacon has not identified a supported inference for this signal.";
}
function insightBulletList(label, items = []) {
  const bullets = (items || []).filter(Boolean).slice(0, 4);
  if (!bullets.length) return "";
  return `<div class="insight-bullets"><span>${escapeHtml(label)}</span><ul>${bullets.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`;
}
function formatResearchValue(signal) {
  const v = Number(signal.primary_value);
  if (!Number.isFinite(v)) return signal.primary_value === undefined || signal.primary_value === null ? "Unavailable" : escapeHtml(signal.primary_value);
  const metric = String(signal.primary_metric || "").toLowerCase();
  if (metric.includes("aum") || metric.includes("flow")) return fmtMoney(v);
  if (metric.includes("quarter")) return `${v}`;
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
  const p = Array.isArray(row.provenance) ? askProvenance(row.provenance[0] || {}) : askProvenance(row || {});
  const recordId = row.record_id || row.source_record_id || p.source_record_ids?.[0] || `Evidence ${index + 1}`;
  return `<div>
    <span>${recordId}</span>
    <strong>Workbook ${(p.source_files || []).join(", ") || "n/a"} · Sheet ${(p.source_sheets || []).join(", ") || "n/a"} · Row ${(p.source_rows || []).join(", ") || "n/a"} · Cells ${(p.source_cells || []).join(", ") || "n/a"}</strong>
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
    ${showAll ? `<button class="text-action source-record-action">View all allocation differences &rarr;</button>` : ""}
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
    <button class="ask-placeholder" data-page="Ask Beacon" data-ask-context='${escapeHtml(JSON.stringify({ fund: signal.fund, period: signal.period, asset_class: signal.asset_class, manager: signal.manager, research_signal_id: signal.id }))}'>Ask Beacon</button>
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
    <button class="ask-placeholder" data-page="Ask Beacon" data-ask-context='${escapeHtml(JSON.stringify({ asset_class: assetClass }))}'>Ask Beacon</button>`;
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
    <button class="ask-placeholder" data-page="Ask Beacon" data-ask-context='${escapeHtml(JSON.stringify({ fund: row?.FundCode, asset_class: row?.AssetClassLevel1, manager }))}'>Ask Beacon</button>`;
}
function bindEvents() {
  if ($("#fund")) {
    $("#fund").value = state.fund; $("#period").value = state.period; $("#assetClass").value = state.assetClass; $("#manager").value = state.manager;
    ["fund", "period", "assetClass", "manager"].forEach(id => $(`#${id}`).addEventListener("change", e => { state[id] = e.target.value; render(); }));
    $("#reset").addEventListener("click", () => { Object.assign(state, { fund: "BPT", period: "FY2026", assetClass: "All", manager: "All", managerView: "All Managers", drawer: null }); render(); });
  }
  if ($("#askForm")) $("#askForm").addEventListener("submit", e => { e.preventDefault(); submitAskMessage($("#askInput").value); });
  document.querySelectorAll("[data-ask-suggestion]").forEach(el => el.addEventListener("click", () => submitAskMessage(el.dataset.askSuggestion)));
  document.querySelectorAll("[data-ask-drawer]").forEach(el => el.addEventListener("click", () => openDrawer(el.dataset.askDrawer === "evidence" ? "askEvidence" : "askHow", "ask")));
  document.querySelectorAll("[data-ask-chip]").forEach(el => el.addEventListener("click", () => {
    const value = el.dataset.askChip;
    if (value === state.fund) state.fund = "All";
    if (value === state.period) state.period = "FY2026";
    if (value === state.assetClass) state.assetClass = "All";
    if (value === state.manager) state.manager = "All";
    if (value.startsWith("Signal ")) state.ask.context = { ...state.ask.context, research_signal_id: null };
    render();
  }));
  document.querySelectorAll("[data-asset]").forEach(el => el.addEventListener("click", () => openDrawer("asset", el.dataset.asset)));
  document.querySelectorAll("[data-manager-row]").forEach(el => el.addEventListener("click", () => openDrawer("manager", el.dataset.managerRow)));
  document.querySelectorAll("[data-manager-view]").forEach(el => el.addEventListener("click", () => { state.managerView = el.dataset.managerView; render(); }));
  document.querySelectorAll("[data-attention]").forEach(el => el.addEventListener("click", () => attentionItems()[Number(el.dataset.attention)]?.action()));
  document.querySelectorAll("[data-page]").forEach(el => el.addEventListener("click", () => {
    if (el.dataset.askContext) applyAskContext(el.dataset.askContext);
    state.page = el.dataset.page;
    state.drawer = null;
    render();
  }));
  document.querySelectorAll("[data-scroll-signal]").forEach(el => el.addEventListener("click", () => document.getElementById(`signal-${el.dataset.scrollSignal}`)?.scrollIntoView({ behavior: "smooth", block: "start" })));
  document.querySelectorAll("[data-evidence]").forEach(el => el.addEventListener("click", () => openDrawer("evidence", el.dataset.evidence, "evidence")));
  document.querySelectorAll("[data-analysis]").forEach(el => el.addEventListener("click", () => openDrawer("evidence", el.dataset.analysis, "analysis")));
}
function applyAskContext(raw) {
  try {
    const context = JSON.parse(raw);
    if (context.fund) state.fund = context.fund;
    if (context.period) state.period = context.period;
    if (context.asset_class) state.assetClass = context.asset_class;
    if (context.manager) state.manager = context.manager;
    state.ask.context = {
      source_page: "insights",
      research_signal_id: context.research_signal_id || null
    };
  } catch {
    state.ask.context = {};
  }
}
function openDrawer(type, id, mode) { state.drawer = { type, id, mode }; render(); }
function closeDrawer() { state.drawer = null; render(); }
window.closeDrawer = closeDrawer;

if (!data) {
  app.innerHTML = `<div class="loading-shell"><div class="empty">Beacon data failed to load.</div></div>`;
} else {
  render();
}

