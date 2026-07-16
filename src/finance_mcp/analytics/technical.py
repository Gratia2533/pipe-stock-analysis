from __future__ import annotations

from collections.abc import Sequence
from math import sqrt
from statistics import stdev


def simple_moving_average(values: Sequence[float], window: int) -> float:
    if window <= 0:
        raise ValueError("window must be greater than zero")
    if len(values) < window:
        raise ValueError(f"at least {window} values are required")
    return sum(values[-window:]) / window


def exponential_moving_average_series(values: Sequence[float], span: int) -> list[float]:
    if span <= 0:
        raise ValueError("span must be greater than zero")
    if not values:
        raise ValueError("values cannot be empty")

    multiplier = 2.0 / (span + 1.0)
    result = [float(values[0])]
    for value in values[1:]:
        result.append((float(value) - result[-1]) * multiplier + result[-1])
    return result


def exponential_moving_average(values: Sequence[float], span: int) -> float:
    return exponential_moving_average_series(values, span)[-1]


def relative_strength_index(values: Sequence[float], period: int = 14) -> float:
    if period <= 0:
        raise ValueError("period must be greater than zero")
    if len(values) < period + 1:
        raise ValueError(f"at least {period + 1} values are required")

    pairs = zip(
        values[-period - 1 : -1],
        values[-period:],
        strict=True,
    )
    deltas = [current - previous for previous, current in pairs]
    gains = sum(delta for delta in deltas if delta > 0) / period
    losses = abs(sum(delta for delta in deltas if delta < 0)) / period

    if losses == 0:
        return 100.0
    relative_strength = gains / losses
    return 100.0 - (100.0 / (1.0 + relative_strength))


def period_return_percent(values: Sequence[float]) -> float:
    if len(values) < 2:
        raise ValueError("at least two values are required")
    if values[0] == 0:
        raise ValueError("the initial value cannot be zero")
    return ((values[-1] / values[0]) - 1.0) * 100.0


def annualized_volatility_percent(
    values: Sequence[float],
    trading_days: int = 252,
) -> float:
    if trading_days <= 0:
        raise ValueError("trading_days must be greater than zero")
    if len(values) < 3:
        raise ValueError("at least three values are required")

    returns = [
        (current / previous) - 1.0
        for previous, current in zip(values[:-1], values[1:], strict=True)
    ]
    return stdev(returns) * sqrt(trading_days) * 100.0


def moving_average_convergence_divergence(
    values: Sequence[float],
    fast_span: int = 12,
    slow_span: int = 26,
    signal_span: int = 9,
) -> tuple[float, float, float]:
    if fast_span >= slow_span:
        raise ValueError("fast_span must be smaller than slow_span")
    minimum_values = slow_span + signal_span - 1
    if len(values) < minimum_values:
        raise ValueError(f"at least {minimum_values} values are required")

    fast = exponential_moving_average_series(values, fast_span)
    slow = exponential_moving_average_series(values, slow_span)
    macd_series = [
        fast_value - slow_value for fast_value, slow_value in zip(fast, slow, strict=True)
    ]
    signal = exponential_moving_average(macd_series, signal_span)
    macd = macd_series[-1]
    return macd, signal, macd - signal


def summarize_prices(rows: Sequence[dict[str, object]]) -> dict[str, float | int | str]:
    if not rows:
        raise ValueError("price rows cannot be empty")

    ordered_rows = sorted(rows, key=lambda row: str(row["date"]))
    closes = [float(row["close"]) for row in ordered_rows]
    latest = ordered_rows[-1]
    result: dict[str, float | int | str] = {
        "date": str(latest["date"]),
        "close": closes[-1],
        "change_percent": ((closes[-1] / closes[-2]) - 1.0) * 100.0 if len(closes) >= 2 else 0.0,
        "sample_size": len(ordered_rows),
    }

    if len(closes) >= 2:
        result["period_return_percent"] = period_return_percent(closes)
    if len(closes) >= 3:
        result["annualized_volatility_percent"] = annualized_volatility_percent(closes)

    for window in (5, 20, 60):
        if len(closes) >= window:
            result[f"ma{window}"] = simple_moving_average(closes, window)

    for span in (12, 26):
        if len(closes) >= span:
            result[f"ema{span}"] = exponential_moving_average(closes, span)

    if len(closes) >= 15:
        result["rsi14"] = relative_strength_index(closes)

    if len(closes) >= 34:
        macd, signal, histogram = moving_average_convergence_divergence(closes)
        result["macd"] = macd
        result["macd_signal"] = signal
        result["macd_histogram"] = histogram

    return result
