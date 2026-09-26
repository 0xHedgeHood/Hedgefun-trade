"use strict";

const $ = (id) => document.getElementById(id);
const controls = ["lp_bps", "sale_bps", "virtual_stock", "buy_stock", "sell_fraction_bps", "supply", "lp_fee_bps", "trade_tax_bps"];
let revision = 0;
let timer;
let lastForkBps = null;

function number(value, digits = 2) {
  if (!Number.isFinite(value)) return "—";
  if (value !== 0 && Math.abs(value) < 0.01) return value.toPrecision(4);
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: digits }).format(value);
}

function percent(value, digits = 1) {
  return `${number(value, digits)}%`;
}

function readInput() {
  const value = (id) => Number($(id).value);
  const data = {
    supply: value("supply"),
    virtual_stock: value("virtual_stock"),
    sale_bps: value("sale_bps") * 100,
    lp_bps: value("lp_bps") * 100,
    buy_stock: value("buy_stock"),
    sell_fraction_bps: value("sell_fraction_bps") * 100,
    lp_fee_bps: value("lp_fee_bps"),
    trade_tax_bps: value("trade_tax_bps"),
  };
  if (Object.values(data).some((item) => !Number.isFinite(item))) throw new Error("请填写有效的数字。 ");
  return data;
}

async function simulate(payload) {
  const response = await fetch("/api/simulate", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "模拟失败");
  return result;
}

function setError(message) {
  $("error").textContent = message || "";
  $("error").hidden = !message;
}

function updatePreset() {
  const lp = Number($("lp_bps").value);
  if (lastForkBps !== null && lp * 100 !== lastForkBps) {
    $("fork-result").hidden = true;
    lastForkBps = null;
  }
  $("lp-display").textContent = `${lp}%`;
  document.querySelectorAll(".preset").forEach((button) => {
    button.classList.toggle("active", Number(button.dataset.lp) === lp);
  });
  $("fork-ratio").textContent = `当前比例：${lp} / ${100 - lp}`;
  $("fork-mismatch").textContent = lp === 50
    ? "50/50 核对当前候选合约；其他输入仍只影响左侧模型。"
    : `${lp}/${100 - lp} 会在隔离副本中修改分配常量后重编译；其他输入仍只影响左侧模型。`;
}

function setText(id, text) { $(id).textContent = text; }

function drawChart(result, data) {
  const svg = $("sell-chart");
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  const NS = "http://www.w3.org/2000/svg";
  const make = (name, attrs, parent = svg) => {
    const node = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    parent.appendChild(node);
    return node;
  };
  const x0 = 38, x1 = 692, y0 = 6, y1 = 202;
  const selected = data.sell_fraction_bps / 100;
  const maxPercent = selected <= 20 ? 20 : Math.min(100, Math.ceil(selected / 10) * 10);
  const sold = result.graduation.sold_user_token_estimate;
  const xReserve = result.graduation.lp_token;
  const priceAt = (pct) => 100 / (1 + sold * pct / 100 / xReserve) ** 2;
  const xp = (pct) => x0 + pct / maxPercent * (x1 - x0);
  const yp = (price) => y1 - price / 100 * (y1 - y0);
  for (const y of [100, 75, 50, 25, 0]) {
    make("line", { x1: x0, x2: x1, y1: yp(y), y2: yp(y), class: "grid-line" });
    const label = make("text", { x: 0, y: yp(y) + 4, class: "axis-label" });
    label.textContent = `${y}`;
  }
  const steps = 80;
  const points = Array.from({ length: steps + 1 }, (_, i) => {
    const pct = i / steps * maxPercent;
    return [xp(pct), yp(priceAt(pct))];
  });
  const line = points.map((point, i) => `${i ? "L" : "M"}${point[0].toFixed(2)} ${point[1].toFixed(2)}`).join(" ");
  make("path", { d: `${line} L${x1} ${y1} L${x0} ${y1} Z`, class: "chart-fill" });
  make("path", { d: line, class: "chart-line" });
  if (selected <= maxPercent) {
    make("line", { x1: xp(selected), x2: xp(selected), y1: yp(priceAt(selected)), y2: y1, stroke: "#81a692", "stroke-dasharray": "4 4" });
    make("circle", { cx: xp(selected), cy: yp(priceAt(selected)), r: 6, class: "chart-marker" });
  }
  const axis = $("chart-axis");
  axis.replaceChildren();
  for (let i = 0; i <= 4; i++) {
    const label = document.createElement("span");
    label.textContent = `${maxPercent * i / 4}%${i === 0 ? " 已售 FUN 卖出" : ""}`;
    axis.appendChild(label);
  }
}

function render(result, data) {
  const g = result.graduation, b = result.buy, s = result.sell;
  setText("real-stock", `${number(g.real_stock)} 股`);
  setText("terminal-price", `${number(g.terminal_price, 8)} 股 / FUN`);
  setText("lp-stock", `${number(g.lp_stock)} 股`);
  setText("treasury-stock", `${number(g.treasury_stock)} 股`);
  $("lp-bar").style.width = `${data.lp_bps / 100}%`;
  $("treasury-bar").style.width = `${100 - data.lp_bps / 100}%`;
  setText("buy-slippage", percent(b.avg_slippage_pct));
  setText("buy-detail", `到账 ${number(b.fun_out)} FUN · 池现价上移 ${percent(b.spot_impact_pct)}`);
  setText("sell-price", percent(s.price_ratio_pct));
  setText("sell-detail", `独立卖压情景 · 获得 ${number(s.stock_out)} 股`);
  setText("burned-token", `${number(g.token_burned, 0)} FUN`);
  setText("push-capital", `推高现价 20% 约需投入 ${number(result.manipulation.stock_for_20pct_up)} 股；这是占用资金，不是攻击损失。`);
  drawChart(result, data);
}

function renderComparison(results, selectedBps) {
  const tbody = $("comparison");
  tbody.replaceChildren();
  for (const result of results.sort((a, b) => a.inputs.lp_bps - b.inputs.lp_bps)) {
    const row = document.createElement("tr");
    if (result.inputs.lp_bps === selectedBps) row.className = "selected";
    const values = [
      `${result.inputs.lp_bps / 100} / ${100 - result.inputs.lp_bps / 100}`,
      `${number(result.graduation.lp_stock)} 股`,
      percent(result.buy.avg_slippage_pct),
      percent(result.sell.price_ratio_pct),
    ];
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    }
    tbody.appendChild(row);
  }
}

async function refresh() {
  updatePreset();
  const current = ++revision;
  try {
    const data = readInput();
    const fractions = [...new Set([3000, 5000, 7000, data.lp_bps])];
    const results = await Promise.all(fractions.map((lp_bps) => simulate({ ...data, lp_bps })));
    if (current !== revision) return;
    const selected = results.find((item) => item.inputs.lp_bps === data.lp_bps);
    render(selected, data);
    renderComparison(results, data.lp_bps);
    setError("");
  } catch (error) {
    if (current === revision) setError(error.message || "模拟失败");
  }
}

function scheduleRefresh() {
  updatePreset();
  clearTimeout(timer);
  timer = setTimeout(refresh, 120);
}

function renderFork(result) {
  const box = $("fork-result");
  box.hidden = false;
  box.replaceChildren();
  const title = document.createElement("h4");
  title.className = result.passed ? "pass" : "fail";
  title.textContent = result.passed ? "✓ Fork 实验通过" : `Fork 实验未通过 · ${result.status || "错误"}`;
  box.appendChild(title);
  const lp = result.allocation?.lp_bps / 100;
  const facts = [
    `Robinhood Chain 区块 ${result.reported_block || result.requested_block || "—"} · ${Number.isFinite(lp) ? `${lp} / ${100 - lp}` : "分配未知"}`,
    lp === 50 ? "当前候选合约的固定 50/50" : "临时隔离合约变体；不是当前 PR 的合约配置",
    `耗时 ${number(result.duration_seconds)} 秒 · 无链上广播`,
  ];
  if (result.metrics?.graduation_refund_stock_raw != null) {
    facts.push(`最终毕业交易返还 ${number(result.metrics.graduation_refund_stock_raw / 1e18, 4)} GME`);
  }
  if (result.metrics?.lp_stock_raw != null && result.metrics?.treasury_stock_raw != null) {
    facts.push(`毕业分配：LP ${number(result.metrics.lp_stock_raw / 1e18, 4)} GME；国库 ${number(result.metrics.treasury_stock_raw / 1e18, 4)} GME`);
  }
  if (result.error) facts.push(result.error);
  for (const fact of facts) {
    const p = document.createElement("p"); p.textContent = fact; box.appendChild(p);
  }
  if (result.verified_checks?.length) {
    const p = document.createElement("p");
    p.textContent = `核对：${result.verified_checks.join("；")}`;
    box.appendChild(p);
  }
  if (result.output_tail) {
    const details = document.createElement("details");
    const summary = document.createElement("summary"); summary.textContent = "查看 Foundry 执行日志";
    const pre = document.createElement("pre"); pre.textContent = result.output_tail;
    details.append(summary, pre); box.appendChild(details);
  }
}

async function runFork() {
  const button = $("run-fork");
  const lp_bps = Number($("lp_bps").value) * 100;
  button.disabled = true;
  button.textContent = `正在运行 ${lp_bps / 100}/${100 - lp_bps / 100} Fork…`;
  try {
    const response = await fetch("/api/fork", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lp_bps }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Fork 运行失败");
    renderFork(result);
    lastForkBps = lp_bps;
  } catch (error) {
    renderFork({ passed: false, status: "unavailable", error: error.message });
    lastForkBps = lp_bps;
  } finally {
    button.disabled = false;
    button.innerHTML = "运行 Fork 实验 <span aria-hidden=\"true\">↗</span>";
    updatePreset();
  }
}

for (const id of controls) $(id).addEventListener("input", scheduleRefresh);
document.querySelectorAll(".preset").forEach((button) => button.addEventListener("click", () => {
  $("lp_bps").value = button.dataset.lp;
  scheduleRefresh();
}));
$("run-fork").addEventListener("click", runFork);
refresh();
