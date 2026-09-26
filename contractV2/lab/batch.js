"use strict";

const batchPresets = {
  small: ["0.1", "140", "1"],
  large: ["20", "140", "20"],
  strict: ["0.01", "140", "0.1"],
};

const batchFields = {
  bot: document.querySelector("#batch-bot"),
  user: document.querySelector("#batch-user"),
  slippage: document.querySelector("#batch-slippage"),
};
const batchButton = document.querySelector("#run-batch");
const batchError = document.querySelector("#batch-error");
const batchResults = document.querySelector("#batch-results");
let batchRequestId = 0;
const batchNumber = (value, places = 4) => value == null ? "—" : new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: places,
  minimumFractionDigits: 0,
}).format(value);

function batchNode(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content != null) node.textContent = content;
  return node;
}

function batchFact(label, value) {
  const row = batchNode("div", "batch-fact");
  row.append(batchNode("span", null, label), batchNode("strong", null, value));
  return row;
}

function batchCard(title, tag, value, result, extra) {
  const card = batchNode("article", "batch-card");
  card.append(batchNode("span", "batch-card-tag", tag), batchNode("h3", null, title));
  const pnl = batchNode("strong", `batch-pnl ${value >= 0 ? "positive" : "negative"}`,
    `${value >= 0 ? "+" : ""}${batchNumber(value, 6)} STOCK`);
  card.append(batchNode("span", "batch-caption", "机器人买入、用户买入、机器人立即卖出的净收益"), pnl);
  const facts = batchNode("div", "batch-facts");
  facts.append(batchFact("用户订单", result.user_filled ? "成交 · 最低到账满足" : "未成交 · 全额退款"));
  facts.append(batchFact("最低净到账", `${batchNumber(result.user_min_out_fun, 2)} FUN`));
  facts.append(batchFact("用户收到", result.user_filled ? `${batchNumber(result.user_out_fun, 2)} FUN` : "0 FUN"));
  facts.append(batchFact("用户退款", `${batchNumber(result.user_refund_stock, 2)} STOCK`));
  facts.append(batchFact("用户相对独立报价少得", result.user_filled ? `${batchNumber(result.user_loss_pct, 4)}%` : "—"));
  if (extra) facts.append(batchFact("实际成交批次：批后现价 / 统一清算价", `${batchNumber(result.spot_to_clearing_ratio, 3)}×`));
  card.append(facts);
  return card;
}

function lateBatchOutcome(label, result) {
  const stale = result.stale_user_filled
    ? `沿用开盘前最低到账仍成交，机器人 ${batchNumber(result.stale_bot_pnl_stock, 6)} STOCK`
    : `沿用开盘前最低到账则用户退回 ${batchNumber(result.stale_user_refund_stock)} STOCK，机器人 ${batchNumber(result.stale_bot_pnl_stock, 6)} STOCK`;
  return `${label}：用户批后重新报价并成交时，机器人 ${batchNumber(result.bot_pnl_stock, 6)} STOCK；${stale}。`;
}

function showBatchResults(data) {
  batchResults.replaceChildren();
  batchResults.append(batchNode("p", "batch-run-summary",
    `本次输入：机器人 ${batchNumber(data.inputs.bot_stock)} STOCK，用户 ${batchNumber(data.inputs.user_stock)} STOCK，最多滑点 ${batchNumber(data.inputs.slippage_bps / 100, 2)}%。各方案最低到账按该方案的独立报价计算。`));
  const grid = batchNode("div", "batch-result-grid");
  grid.append(
    batchCard("现行逐笔曲线", "第 1 秒 · 66% 买税", data.current_second_one.bot_pnl_stock, data.current_second_one, false),
    batchCard("直接聚合买单", "同批同价 · 10% 买税", data.naive_batch.bot_pnl_stock, data.naive_batch, true),
    batchCard("清算价对齐批后价格", "同批同价 · 10% 买税", data.aligned_batch.bot_pnl_stock, data.aligned_batch, true),
  );
  batchResults.append(grid);
  const retained = batchNode("div", "batch-tax-note");
  retained.append(batchNode("strong", null, "如果继续保留开盘 66% 买税"));
  const taxedNaive = data.retained_opening_tax.naive_batch;
  const taxedAligned = data.retained_opening_tax.aligned_batch;
  retained.append(batchNode("p", null,
    `同批机器人净收益：直接聚合 ${batchNumber(taxedNaive.bot_pnl_stock, 6)} STOCK，价格对齐 ${batchNumber(taxedAligned.bot_pnl_stock, 6)} STOCK。用户最低到账仍逐笔检查；本行与上方 10% 买税方案分开看。`));
  batchResults.append(retained);
  const note = batchNode("div", "batch-late-note");
  note.append(batchNode("strong", null, "跨批仍有风险"));
  note.append(batchNode("p", null,
    `机器人独占开盘批次、用户下一批才买后机器人卖出。${lateBatchOutcome("直接聚合", data.late_user.naive_batch)}${lateBatchOutcome("价格对齐", data.late_user.aligned_batch)}统一价让同批订单同价；跨批风险取决于用户是否重新报价与其最低到账。`));
  batchResults.append(note);
  batchResults.hidden = false;
}

async function runBatch() {
  const requestId = ++batchRequestId;
  batchError.hidden = true;
  batchResults.hidden = true;
  batchResults.replaceChildren();
  const bot = Number(batchFields.bot.value);
  const user = Number(batchFields.user.value);
  const slippage = Number(batchFields.slippage.value);
  const bps = Math.round(slippage * 100);
  if (!batchFields.bot.value || !batchFields.user.value || !batchFields.slippage.value ||
      !Number.isFinite(bot) || !Number.isFinite(user) || !Number.isFinite(slippage) ||
      bot < 0.01 || bot > 50 || user < 1 || user > 180 || bot + user > 190 ||
      slippage < 0 || slippage > 20 || Math.abs(slippage * 100 - bps) > 1e-8) {
    batchError.textContent = "请输入有效范围：机器人 0.01–50 STOCK，用户 1–180 STOCK，合计不超过 190；滑点 0–20%。";
    batchError.hidden = false;
    return;
  }
  batchButton.disabled = true;
  batchButton.textContent = "正在计算…";
  try {
    const response = await fetch("/api/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bot_stock: batchFields.bot.value, user_stock: batchFields.user.value, slippage_bps: bps }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "计算失败");
    if (requestId === batchRequestId) showBatchResults(result);
  } catch (error) {
    if (requestId === batchRequestId) {
      batchError.textContent = error.message;
      batchError.hidden = false;
    }
  } finally {
    if (requestId === batchRequestId) {
      batchButton.disabled = false;
      batchButton.textContent = "运行开盘对照 ↗";
    }
  }
}

document.querySelectorAll("[data-batch-preset]").forEach((button) => {
  button.addEventListener("click", () => {
    const preset = batchPresets[button.dataset.batchPreset];
    [batchFields.bot.value, batchFields.user.value, batchFields.slippage.value] = preset;
    document.querySelectorAll("[data-batch-preset]").forEach((item) => item.classList.toggle("active", item === button));
    runBatch();
  });
});
batchButton.addEventListener("click", runBatch);
runBatch();
