"""Counterfactual opening-batch economics for the local V2 workbench.

This is not an auction contract or an executable quote. It keeps the V2 test
fixture's supply, virtual STOCK reserve and flat taxes, then compares a simple
pro-rata curve batch with a batch whose clearing price equals its post-batch
spot price. Decimal arithmetic avoids hiding a profitable path in float noise.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext


D = Decimal
SUPPLY = D(1_000_000)
VIRTUAL_STOCK = D(50)
MIN_TOKEN_RESERVE = SUPPLY * D("0.2")
INITIAL_INVARIANT = SUPPLY * VIRTUAL_STOCK
FLAT_TAX = D("0.10")
SECOND_ONE_BUY_TAX = D("0.66")


def _input(payload: dict, key: str, low: Decimal, high: Decimal) -> Decimal:
    value = payload.get(key)
    if isinstance(value, bool) or value is None or len(str(value)) > 80:
        raise ValueError(f"{key} must be a number from {low} to {high}")
    try:
        amount = D(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{key} must be a number from {low} to {high}") from None
    if not amount.is_finite() or not low <= amount <= high:
        raise ValueError(f"{key} must be a number from {low} to {high}")
    return amount


def _buy(x: Decimal, y: Decimal, k: Decimal, stock: Decimal, tax: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    next_x = k / (y + stock)
    if next_x < MIN_TOKEN_RESERVE:
        raise ValueError("Trade crosses the V2 graduation threshold")
    gross = x - next_x
    return next_x, y + stock, gross * (1 - tax)


def _sell(x: Decimal, y: Decimal, k: Decimal, tokens: Decimal, real_stock: Decimal) -> Decimal:
    gross = y - k / (x + tokens)
    if gross < 0 or gross > real_stock:
        raise ValueError("Model would pay more STOCK than its real reserve")
    return gross * (1 - FLAT_TAX)


def _opening_state(method: str, total: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    y = VIRTUAL_STOCK + total
    if method == "naive":
        # One ordinary aggregate curve buy, then split its net tokens pro rata.
        gross = SUPPLY - INITIAL_INVARIANT / y
        x = SUPPLY - gross
        k = INITIAL_INVARIANT
    elif method == "aligned":
        # Solve B/G = (V+B)/(S-G): uniform gross price equals the next spot.
        # The resulting invariant differs from the current immutable V2 one.
        gross = total * SUPPLY / (VIRTUAL_STOCK + 2 * total)
        x = SUPPLY - gross
        k = x * y
    else:
        raise ValueError("Unknown opening method")
    if x < MIN_TOKEN_RESERVE:
        raise ValueError("Batch crosses the V2 graduation threshold")
    return x, y, k, gross


def _format(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _current_sequential(bot: Decimal, user: Decimal, tolerance: Decimal) -> dict:
    _, _, clean_user = _buy(SUPPLY, VIRTUAL_STOCK, INITIAL_INVARIANT, user, SECOND_ONE_BUY_TAX)
    minimum = clean_user * (1 - tolerance)
    x_bot, y_bot, bot_tokens = _buy(SUPPLY, VIRTUAL_STOCK, INITIAL_INVARIANT, bot, SECOND_ONE_BUY_TAX)
    x_user, y_user, attacked_user = _buy(x_bot, y_bot, INITIAL_INVARIANT, user, SECOND_ONE_BUY_TAX)
    filled = attacked_user >= minimum
    if filled:
        x_exit, y_exit, real_stock = x_user, y_user, bot + user
    else:
        x_exit, y_exit, real_stock = x_bot, y_bot, bot
    sale = _sell(x_exit, y_exit, INITIAL_INVARIANT, bot_tokens, real_stock)
    return {
        "bot_pnl_stock": _format(sale - bot),
        "bot_tokens_fun": _format(bot_tokens),
        "user_filled": filled,
        "user_out_fun": _format(attacked_user) if filled else None,
        "user_min_out_fun": _format(minimum),
        "user_refund_stock": 0.0 if filled else _format(user),
        "user_loss_pct": _format((clean_user - attacked_user) / clean_user * 100) if filled else None,
    }


def _same_batch(method: str, bot: Decimal, user: Decimal, tolerance: Decimal,
                buy_tax: Decimal = FLAT_TAX) -> dict:
    _, _, _, clean_gross = _opening_state(method, user)
    clean_user = clean_gross * (1 - buy_tax)
    minimum = clean_user * (1 - tolerance)
    total = bot + user
    x, y, k, gross = _opening_state(method, total)
    net = gross * (1 - buy_tax)
    user_out = net * user / total
    filled = user_out >= minimum
    if not filled:
        total = bot
        x, y, k, gross = _opening_state(method, bot)
        net = gross * (1 - buy_tax)
        user_out = None
    bot_out = net * bot / total
    sale = _sell(x, y, k, bot_out, total)
    clearing_price = total / gross
    spot = y / x
    return {
        "bot_pnl_stock": _format(sale - bot),
        "bot_tokens_fun": _format(bot_out),
        "user_filled": filled,
        "user_out_fun": _format(user_out),
        "user_min_out_fun": _format(minimum),
        "user_refund_stock": 0.0 if filled else _format(user),
        "user_loss_pct": _format((clean_user - user_out) / clean_user * 100) if filled else None,
        "clearing_price_stock_per_gross_fun": _format(clearing_price),
        "post_spot_stock_per_fun": _format(spot),
        "spot_to_clearing_ratio": _format(spot / clearing_price),
        "invariant_changed": method == "aligned",
        "opening_buy_tax_pct": _format(buy_tax * 100),
    }


def _late_user(method: str, bot: Decimal, user: Decimal, tolerance: Decimal) -> dict:
    # The bot is alone in the opening batch. The user quotes and buys after
    # the batch closes, then the bot sells. A second path tests whether a
    # pre-opening solo quote would have rejected that later buy instead.
    _, _, _, stale_gross = _opening_state(method, user)
    stale_minimum = stale_gross * (1 - FLAT_TAX) * (1 - tolerance)
    x, y, k, gross = _opening_state(method, bot)
    bot_tokens = gross * (1 - FLAT_TAX)
    x_after_user, y_after_user, user_tokens = _buy(x, y, k, user, FLAT_TAX)
    sale = _sell(x_after_user, y_after_user, k, bot_tokens, bot + user)
    stale_filled = user_tokens >= stale_minimum
    stale_sale = sale if stale_filled else _sell(x, y, k, bot_tokens, bot)
    return {
        # Fresh quote is obtained immediately after the opening batch closes.
        "bot_pnl_stock": _format(sale - bot),
        "user_out_fun": _format(user_tokens),
        "user_filled": True,
        "fresh_user_min_out_fun": _format(user_tokens * (1 - tolerance)),
        "stale_user_min_out_fun": _format(stale_minimum),
        "stale_user_filled": stale_filled,
        "stale_user_refund_stock": 0.0 if stale_filled else _format(user),
        "stale_bot_pnl_stock": _format(stale_sale - bot),
    }


def simulate_batch_opening(payload: dict) -> dict:
    """Compare current second-1 execution with two three-block batch models.

    The user's min-out is the solo quote for each mechanism less the chosen
    tolerance. A failed order is fully refunded before settlement; the bot
    order then clears alone. No partial fills, gas, builder fees or external
    trading are simulated.
    """
    if not isinstance(payload, dict) or set(payload) != {"bot_stock", "user_stock", "slippage_bps"}:
        raise ValueError("Batch experiment accepts bot_stock, user_stock and slippage_bps only")
    with localcontext() as context:
        context.prec = 80
        bot = _input(payload, "bot_stock", D("0.01"), D(50))
        user = _input(payload, "user_stock", D(1), D(180))
        slippage = _input(payload, "slippage_bps", D(0), D(2000))
        if slippage != slippage.to_integral_value():
            raise ValueError("slippage_bps must be an integer")
        if bot + user > 190:
            raise ValueError("Combined buy must be at most 190 STOCK to stay below graduation")
        tolerance = slippage / D(10_000)
        return {
            "inputs": {"bot_stock": _format(bot), "user_stock": _format(user),
                       "slippage_bps": int(slippage), "opening_blocks": 3},
            "current_second_one": _current_sequential(bot, user, tolerance),
            "naive_batch": _same_batch("naive", bot, user, tolerance),
            "aligned_batch": _same_batch("aligned", bot, user, tolerance),
            "retained_opening_tax": {
                "naive_batch": _same_batch("naive", bot, user, tolerance, SECOND_ONE_BUY_TAX),
                "aligned_batch": _same_batch("aligned", bot, user, tolerance, SECOND_ONE_BUY_TAX),
            },
            "late_user": {
                "naive_batch": _late_user("naive", bot, user, tolerance),
                "aligned_batch": _late_user("aligned", bot, user, tolerance),
            },
            "assumptions": [
                "Research-only continuous x*y=k model; batch contract and price reset are not implemented.",
                "Three blocks only decide whether orders share a batch; the formulas do not model inclusion or cancellation.",
                "Primary opening batch uses 10% buy and sell taxes; a separate sensitivity keeps the current 66% second-one buy tax.",
                "Min-out checks net FUN at settlement against each method's solo quote; failed orders receive a full STOCK refund.",
                "The bot order has min-out zero in this two-order experiment; production orders would each carry a min-out.",
                "Late-user fresh quotes are taken after the bot-only batch; stale quotes are checked against the pre-opening solo quote and may refund.",
                "The aligned model resets the invariant to match clearing price and post-batch spot; current V2 immutables forbid this.",
                "Bot PnL excludes gas, priority fees, builder payments, competition, delayed withdrawals and external hedges.",
            ],
        }
