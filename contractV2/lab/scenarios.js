"use strict";

// The server chooses the Foundry suite from an allowlist. No user-provided command is executed.
const scenarioFacts = {
  buy_wave: [
    ["Scenario buy-wave order raw firstWalletAdvantage", "首位比末位多拿", "FUN"],
    ["Scenario buy-wave graduation raw budget spent refund", "毕业边界退回", "STOCK"],
  ],
  sell_wave: [
    ["Scenario sell-wave curve first_stock_raw:", "曲线首位卖家到账", "STOCK"],
    ["Scenario sell-wave curve last_stock_raw:", "曲线末位卖家到账", "STOCK"],
    ["Scenario sell-wave v4 flat_total_stock_raw:", "V4 普通卖税总到账", "STOCK"],
    ["Scenario sell-wave v4 spike_total_stock_raw:", "V4 回购峰值总到账", "STOCK"],
  ],
  mixed_order: [
    ["Scenario mixed-curve sellerA-after-buy raw:", "曲线：买单先到，卖家 A", "STOCK"],
    ["Scenario mixed-curve sellerA-before-buy raw:", "曲线：卖单先到，卖家 A", "STOCK"],
    ["Scenario mixed-v4 sellerA-after-buy raw:", "V4：买单先到，卖家 A", "STOCK"],
    ["Scenario mixed-v4 sellerA-before-buy raw:", "V4：卖单先到，卖家 A", "STOCK"],
  ],
  opening_sniper: [
    ["Scenario curve_t0 bot_pnl_stock_raw:", "曲线第 0 秒：机器人净收益", "STOCK"],
    ["Scenario curve_t1 bot_pnl_stock_raw:", "曲线第 1 秒：机器人净收益", "STOCK"],
    ["Scenario curve_t2 bot_pnl_stock_raw:", "曲线第 2 秒：机器人净收益", "STOCK"],
    ["Scenario curve_t3 bot_pnl_stock_raw:", "曲线第 3 秒：机器人净收益", "STOCK"],
    ["Scenario curve_t1_small_bot_pnl_stock_raw:", "第 1 秒：0.1 STOCK 抢跑，用户限滑 1%", "STOCK"],
    ["Scenario curve_t1_small_bot_victim_loss_percent_e18:", "该用户实际少得", "%"],
    ["Scenario curve_t1_tiny_bot_pnl_stock_raw:", "第 1 秒：0.01 STOCK 抢跑，用户限滑 0.1%", "STOCK"],
    ["Scenario curve_t1_tiny_bot_victim_loss_percent_e18:", "该用户实际少得", "%"],
    ["Scenario v4_size_5_20 bot_pnl_stock_raw:", "V4：5 / 20 抢跑净收益", "STOCK"],
    ["Scenario v4_size_5_20 victim_loss_token_raw:", "V4：用户少得", "FUN"],
    ["Scenario sniper_v4_size_5_20_protected bot_pnl_stock_raw:", "设置最低到账后机器人净收益", "STOCK"],
  ],
};

function scenarioAmount(raw) {
  const value = Number(raw) / 1e18;
  if (!Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 4, minimumFractionDigits: 0 }).format(value);
}

function scenarioMetric(lines, prefix) {
  const line = lines.find((item) => item.startsWith(prefix));
  return line?.match(/(-?\d+)\s*$/)?.[1] ?? null;
}

function showScenarioResult(card, result) {
  const box = card.querySelector(".scenario-result");
  box.hidden = false;
  box.replaceChildren();
  const status = document.createElement("strong");
  status.className = result.passed ? "scenario-pass" : "scenario-fail";
  status.textContent = result.passed
    ? `✓ ${result.test_count} 项合约测试通过 · ${result.duration_seconds} 秒`
    : `实验未通过 · ${result.status || "错误"}`;
  box.appendChild(status);
  if (result.passed) {
    const metrics = document.createElement("div");
    metrics.className = "scenario-facts";
    for (const [prefix, label, unit] of scenarioFacts[result.scenario] || []) {
      const raw = scenarioMetric(result.metrics || [], prefix);
      if (raw === null) continue;
      const fact = document.createElement("div");
      const name = document.createElement("span");
      const amount = document.createElement("b");
      name.textContent = label;
      amount.textContent = `${scenarioAmount(raw)} ${unit}`;
      fact.append(name, amount);
      metrics.appendChild(fact);
    }
    box.appendChild(metrics);
  }
  if (result.error) {
    const error = document.createElement("p");
    error.textContent = result.error;
    box.appendChild(error);
  }
  if (result.output_tail) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    const log = document.createElement("pre");
    summary.textContent = "查看执行日志（最多末尾 12 KB）";
    log.textContent = result.output_tail;
    details.append(summary, log);
    box.appendChild(details);
  }
}

async function runScenario(button) {
  const name = button.dataset.scenario;
  const card = button.closest(".scenario-card");
  const buttons = [...document.querySelectorAll("[data-scenario]")];
  buttons.forEach((item) => { item.disabled = true; });
  const oldText = button.textContent;
  button.textContent = "正在重放交易…";
  try {
    const response = await fetch("/api/scenario", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario: name }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "实验运行失败");
    showScenarioResult(card, result);
  } catch (error) {
    showScenarioResult(card, { scenario: name, passed: false, status: "unavailable", error: error.message });
  } finally {
    button.textContent = oldText;
    buttons.forEach((item) => { item.disabled = false; });
  }
}

document.querySelectorAll("[data-scenario]").forEach((button) => {
  button.addEventListener("click", () => runScenario(button));
});
