from __future__ import annotations

from collections.abc import Sequence


def _change_percent(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return (current / previous - 1.0) * 100.0


def _utilization_percent(balance: float, limit: float) -> float | None:
    if limit == 0:
        return None
    return balance / limit * 100.0


def summarize_margin_trading(
    rows: Sequence[dict[str, object]],
) -> dict[str, object]:
    if not rows:
        raise ValueError("margin trading rows cannot be empty")

    ordered = sorted(rows, key=lambda row: str(row["date"]))
    first = ordered[0]
    latest = ordered[-1]

    first_margin = float(first["MarginPurchaseTodayBalance"])
    latest_margin = float(latest["MarginPurchaseTodayBalance"])
    first_short = float(first["ShortSaleTodayBalance"])
    latest_short = float(latest["ShortSaleTodayBalance"])

    result: dict[str, object] = {
        "start_date": str(first["date"]),
        "end_date": str(latest["date"]),
        "trading_day_count": len(ordered),
        "margin_purchase": {
            "latest_balance": int(latest_margin),
            "period_change": int(latest_margin - first_margin),
            "latest_daily_change": int(
                latest_margin - float(latest["MarginPurchaseYesterdayBalance"])
            ),
            "period_buy": sum(int(row["MarginPurchaseBuy"]) for row in ordered),
            "period_sell": sum(int(row["MarginPurchaseSell"]) for row in ordered),
            "period_cash_repayment": sum(
                int(row["MarginPurchaseCashRepayment"]) for row in ordered
            ),
        },
        "short_sale": {
            "latest_balance": int(latest_short),
            "period_change": int(latest_short - first_short),
            "latest_daily_change": int(
                latest_short - float(latest["ShortSaleYesterdayBalance"])
            ),
            "period_sell": sum(int(row["ShortSaleSell"]) for row in ordered),
            "period_buy": sum(int(row["ShortSaleBuy"]) for row in ordered),
            "period_cash_repayment": sum(
                int(row["ShortSaleCashRepayment"]) for row in ordered
            ),
        },
    }

    margin = result["margin_purchase"]
    short = result["short_sale"]
    assert isinstance(margin, dict)
    assert isinstance(short, dict)

    margin_change_percent = _change_percent(latest_margin, first_margin)
    if margin_change_percent is not None:
        margin["period_change_percent"] = margin_change_percent

    short_change_percent = _change_percent(latest_short, first_short)
    if short_change_percent is not None:
        short["period_change_percent"] = short_change_percent

    margin_utilization = _utilization_percent(
        latest_margin,
        float(latest["MarginPurchaseLimit"]),
    )
    if margin_utilization is not None:
        margin["limit_utilization_percent"] = margin_utilization

    short_utilization = _utilization_percent(
        latest_short,
        float(latest["ShortSaleLimit"]),
    )
    if short_utilization is not None:
        short["limit_utilization_percent"] = short_utilization

    return result
