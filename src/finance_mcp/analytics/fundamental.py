from __future__ import annotations

from collections.abc import Sequence


def _growth_percent(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return ((current / previous) - 1.0) * 100.0


def summarize_valuation(rows: Sequence[dict[str, object]]) -> dict[str, float | int | str]:
    if not rows:
        raise ValueError("valuation rows cannot be empty")

    ordered_rows = sorted(rows, key=lambda row: str(row["date"]))
    latest = ordered_rows[-1]
    result: dict[str, float | int | str] = {
        "date": str(latest["date"]),
        "per": float(latest["PER"]),
        "pbr": float(latest["PBR"]),
        "dividend_yield_percent": float(latest["dividend_yield"]),
        "sample_size": len(ordered_rows),
    }

    positive_per = [float(row["PER"]) for row in ordered_rows if float(row["PER"]) > 0]
    positive_pbr = [float(row["PBR"]) for row in ordered_rows if float(row["PBR"]) > 0]
    if positive_per:
        result["period_average_per"] = sum(positive_per) / len(positive_per)
    if positive_pbr:
        result["period_average_pbr"] = sum(positive_pbr) / len(positive_pbr)
    return result


def summarize_monthly_revenue(
    rows: Sequence[dict[str, object]],
) -> dict[str, float | int | str]:
    if not rows:
        raise ValueError("monthly revenue rows cannot be empty")

    ordered_rows = sorted(
        rows,
        key=lambda row: (int(row["revenue_year"]), int(row["revenue_month"])),
    )
    latest = ordered_rows[-1]
    latest_revenue = float(latest["revenue"])
    latest_year = int(latest["revenue_year"])
    latest_month = int(latest["revenue_month"])
    result: dict[str, float | int | str] = {
        "date": str(latest["date"]),
        "revenue_year": latest_year,
        "revenue_month": latest_month,
        "revenue": int(latest_revenue),
        "sample_size": len(ordered_rows),
    }

    if len(ordered_rows) >= 2:
        previous_revenue = float(ordered_rows[-2]["revenue"])
        mom_growth = _growth_percent(latest_revenue, previous_revenue)
        if mom_growth is not None:
            result["mom_growth_percent"] = mom_growth

    prior_year_row = next(
        (
            row
            for row in reversed(ordered_rows[:-1])
            if int(row["revenue_year"]) == latest_year - 1
            and int(row["revenue_month"]) == latest_month
        ),
        None,
    )
    if prior_year_row is not None:
        yoy_growth = _growth_percent(latest_revenue, float(prior_year_row["revenue"]))
        if yoy_growth is not None:
            result["yoy_growth_percent"] = yoy_growth

    if len(ordered_rows) >= 12:
        trailing_12_month_revenue = sum(float(row["revenue"]) for row in ordered_rows[-12:])
        result["trailing_12_month_revenue"] = int(trailing_12_month_revenue)

    if len(ordered_rows) >= 24:
        latest_twelve = sum(float(row["revenue"]) for row in ordered_rows[-12:])
        previous_twelve = sum(float(row["revenue"]) for row in ordered_rows[-24:-12])
        trailing_growth = _growth_percent(latest_twelve, previous_twelve)
        if trailing_growth is not None:
            result["trailing_12_month_yoy_growth_percent"] = trailing_growth

    return result
