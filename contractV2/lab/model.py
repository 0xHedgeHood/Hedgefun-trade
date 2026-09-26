"""Dependency-free, *approximate* V2 graduation and post-graduation market model.

Amounts are human-readable FUN and stock units, not Solidity raw token units. The
real contracts round raw units, quantize the V4 sqrt price, and use a full-range
concentrated-liquidity position. This model uses continuous ``x * y = k`` math
for a quick economic comparison; it is never a transaction quote.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext


BPS = Decimal(10_000)
MAX_AMOUNT = Decimal("1e30")
MIN_NONZERO_AMOUNT = Decimal("1e-18")


def _amount(payload: dict, key: str, default: str | None = None, *, positive: bool = False) -> Decimal:
    value = payload.get(key, default)
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{key} must be a finite number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{key} must be a finite number") from None
    if (not number.is_finite() or number < 0 or number > MAX_AMOUNT
            or (positive and number == 0) or (number != 0 and number < MIN_NONZERO_AMOUNT)):
        bound = "positive" if positive else "nonnegative"
        raise ValueError(f"{key} must be a finite {bound} amount from 1e-18 to 1e30 when nonzero")
    return number


def _bps(payload: dict, key: str, default: int, low: int, high: int) -> int:
    value = _amount(payload, key, str(default))
    if value != value.to_integral_value() or value < low or value > high:
        raise ValueError(f"{key} must be an integer from {low} to {high}")
    return int(value)


def _out(number: Decimal) -> float:
    return float(number)


def simulate(payload: dict) -> dict:
    """Return JSON-serializable graduation, independent buy/sell, and push-capital scenarios.

    Required: ``supply`` and ``virtual_stock``. Optional defaults: sale 80%, LP
    50%, buy 10 stock, sell 10% of curve-distributed FUN, LP fee 0.30%, flat
    trade tax 10%. ``lp_fee_bps`` uses ordinary basis points: 30 means 0.30%.
    ``lp_bps`` is an experiment only; the current V2 contract fixes it at 5000.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")

    with localcontext() as ctx:
        ctx.prec = 80
        supply = _amount(payload, "supply", positive=True)
        virtual_stock = _amount(payload, "virtual_stock", positive=True)
        sale_bps = _bps(payload, "sale_bps", 8000, 1000, 9000)
        lp_bps = _bps(payload, "lp_bps", 5000, 1, 10_000)
        buy_stock = _amount(payload, "buy_stock", "10")
        sell_fraction_bps = _bps(payload, "sell_fraction_bps", 1000, 0, 10_000)
        lp_fee_bps = _bps(payload, "lp_fee_bps", 30, 0, 30)
        trade_tax_bps = _bps(payload, "trade_tax_bps", 1000, 0, 1500)

        sale_fraction = Decimal(sale_bps) / BPS
        lp_fraction = Decimal(lp_bps) / BPS
        lp_fee_fraction = Decimal(lp_fee_bps) / BPS
        tax_fraction = Decimal(trade_tax_bps) / BPS

        # The Solidity curve uses Tmin=floor(S*(1-saleBps)) and
        # Yg=ceil(S*V/Tmin). We omit those raw-unit floors/ceilings here.
        terminal_token_inventory = supply * (1 - sale_fraction)
        terminal_effective_stock = supply * virtual_stock / terminal_token_inventory
        real_stock = terminal_effective_stock - virtual_stock
        terminal_price = terminal_effective_stock / terminal_token_inventory

        # Real stock alone is distributed. Virtual stock cannot be spent.
        lp_stock = real_stock * lp_fraction
        treasury_stock = real_stock - lp_stock
        lp_token = lp_stock / terminal_price
        token_burned = terminal_token_inventory - lp_token
        gross_curve_sold = supply - terminal_token_inventory
        curve_tax_burned_estimate = gross_curve_sold * tax_fraction
        sold_user_token_estimate = gross_curve_sold - curve_tax_burned_estimate

        # Exact-input BUY: LP fee comes out of stock input; the hook takes a
        # flat tax from the FUN output. LP fee is accrued separately, not used
        # for the price movement.
        effective_buy = buy_stock * (1 - lp_fee_fraction)
        gross_fun_out = lp_token * effective_buy / (lp_stock + effective_buy)
        buy_hook_tax = gross_fun_out * tax_fraction
        fun_out = gross_fun_out - buy_hook_tax
        buy_spot_after = terminal_price * (1 + effective_buy / lp_stock) ** 2
        buy_average_price = buy_stock / fun_out if fun_out > 0 else terminal_price
        buy_slippage = (buy_average_price / terminal_price - 1) * 100 if fun_out > 0 else Decimal(0)
        buy_impact = (buy_spot_after / terminal_price - 1) * 100

        # Independent exact-input SELL from the *original* graduation pool.
        # The fraction refers to tokens distributed to users by the curve,
        # after estimated curve buy-tax burns for a one-way sale path. If
        # tokens are sold back to the curve then bought again, cumulative
        # gross buys and burns are higher than this estimate.
        fun_in = sold_user_token_estimate * Decimal(sell_fraction_bps) / BPS
        effective_sell = fun_in * (1 - lp_fee_fraction)
        gross_stock_out = lp_stock * effective_sell / (lp_token + effective_sell)
        sell_hook_tax = gross_stock_out * tax_fraction
        stock_out = gross_stock_out - sell_hook_tax
        sell_spot_after = terminal_price / (1 + effective_sell / lp_token) ** 2
        sell_average_price = stock_out / fun_in if fun_in > 0 else terminal_price
        sell_slippage = (1 - sell_average_price / terminal_price) * 100 if fun_in > 0 else Decimal(0)

        # A 20% spot push requires this much STOCK principal on the input side
        # of a BUY. The attacker still owns FUN afterward. It is *capital*,
        # not the net round-trip cost or a profit estimate.
        price_target = Decimal("1.2")
        effective_push = lp_stock * (price_target.sqrt() - 1)
        push_stock_capital = effective_push / (1 - lp_fee_fraction)

        return {
            "inputs": {
                "supply": _out(supply),
                "virtual_stock": _out(virtual_stock),
                "sale_bps": sale_bps,
                "lp_bps": lp_bps,
                "buy_stock": _out(buy_stock),
                "sell_fraction_bps": sell_fraction_bps,
                "lp_fee_bps": lp_fee_bps,
                "trade_tax_bps": trade_tax_bps,
            },
            "graduation": {
                "real_stock": _out(real_stock),
                "terminal_price": _out(terminal_price),
                "lp_stock": _out(lp_stock),
                "treasury_stock": _out(treasury_stock),
                "lp_token": _out(lp_token),
                "token_burned": _out(token_burned),
                "curve_tax_burned_estimate": _out(curve_tax_burned_estimate),
                "sold_user_token_estimate": _out(sold_user_token_estimate),
            },
            "buy": {
                "stock_in": _out(buy_stock),
                "lp_fee_stock": _out(buy_stock - effective_buy),
                "gross_fun_out": _out(gross_fun_out),
                "hook_tax_fun": _out(buy_hook_tax),
                "fun_out": _out(fun_out),
                "avg_execution_price": _out(buy_average_price),
                "avg_slippage_pct": _out(buy_slippage),
                "spot_after": _out(buy_spot_after),
                "spot_impact_pct": _out(buy_impact),
            },
            "sell": {
                "fun_in": _out(fun_in),
                "lp_fee_fun": _out(fun_in - effective_sell),
                "gross_stock_out": _out(gross_stock_out),
                "hook_tax_stock": _out(sell_hook_tax),
                "stock_out": _out(stock_out),
                "avg_execution_price": _out(sell_average_price),
                "avg_slippage_pct": _out(sell_slippage),
                "price_after": _out(sell_spot_after),
                "price_ratio_pct": _out(sell_spot_after / terminal_price * 100),
            },
            "manipulation": {
                "target_price_ratio_pct": 120.0,
                "stock_for_20pct_up": _out(push_stock_capital),
                "capital_not_cost": True,
            },
            "assumptions": [
                "Continuous x*y=k approximation of the V4 full-range pool; no raw-unit rounding or sqrt-price quantization.",
                "Buy and sell are independent scenarios, each starting at the graduation spot and LP depth.",
                "Flat output-side hook tax only; no sell spike, treasury buyback, fee collection, MEV or strategy P&L.",
                "No external payment route, upstream slippage, transaction gas or V4 price limit.",
                "LP share is experimental: the current V2 candidate fixes it at 50%; zero LP fee is a counterfactual benchmark.",
                "Estimated curve buy-tax burn assumes a one-way sale to graduation; sell-and-rebuy churn burns more FUN and changes the sellable base.",
                "Price-push stock is capital deployed into a BUY, not attacker net cost or expected profit.",
            ],
        }
