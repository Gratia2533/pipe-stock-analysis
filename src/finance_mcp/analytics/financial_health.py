from __future__ import annotations

from collections.abc import Sequence
from datetime import date


def _safe_percent(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator * 100.0


def _growth_percent(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return (current / previous - 1.0) * 100.0


def _rows_by_date(rows: Sequence[dict[str, object]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, dict[str, float]] = {}
    for row in rows:
        row_date = str(row["date"])
        row_type = str(row["type"])
        grouped.setdefault(row_date, {})[row_type] = float(row["value"])
    return grouped


def _latest_date(grouped: dict[str, dict[str, float]]) -> str:
    if not grouped:
        raise ValueError("financial statement rows cannot be empty")
    return max(grouped)


def _same_period_last_year(
    grouped: dict[str, dict[str, float]],
    latest_date: str,
) -> dict[str, float] | None:
    latest = date.fromisoformat(latest_date)
    prior_date = latest.replace(year=latest.year - 1).isoformat()
    return grouped.get(prior_date)


def summarize_financial_health(
    income_rows: Sequence[dict[str, object]],
    balance_rows: Sequence[dict[str, object]],
    cash_flow_rows: Sequence[dict[str, object]],
) -> dict[str, object]:
    if not income_rows:
        raise ValueError("income statement rows cannot be empty")
    if not balance_rows:
        raise ValueError("balance sheet rows cannot be empty")
    if not cash_flow_rows:
        raise ValueError("cash flow rows cannot be empty")

    income_grouped = _rows_by_date(income_rows)
    balance_grouped = _rows_by_date(balance_rows)
    cash_grouped = _rows_by_date(cash_flow_rows)

    income_date = _latest_date(income_grouped)
    balance_date = _latest_date(balance_grouped)
    cash_date = _latest_date(cash_grouped)

    income = income_grouped[income_date]
    balance = balance_grouped[balance_date]
    cash = cash_grouped[cash_date]
    prior_income = _same_period_last_year(income_grouped, income_date)

    missing: list[str] = []

    income_result: dict[str, float | str] = {"date": income_date}
    income_fields = {
        "revenue": "Revenue",
        "gross_profit": "GrossProfit",
        "operating_income": "OperatingIncome",
        "net_income": "IncomeAfterTaxes",
        "eps": "EPS",
    }
    for output_name, source_name in income_fields.items():
        if source_name in income:
            income_result[output_name] = income[source_name]
        else:
            missing.append(f"income.{source_name}")

    revenue = income.get("Revenue")
    if revenue is not None:
        ratio_fields = {
            "gross_margin_percent": "GrossProfit",
            "operating_margin_percent": "OperatingIncome",
            "net_margin_percent": "IncomeAfterTaxes",
        }
        for output_name, source_name in ratio_fields.items():
            value = income.get(source_name)
            if value is not None:
                ratio = _safe_percent(value, revenue)
                if ratio is not None:
                    income_result[output_name] = ratio

    if prior_income is not None:
        for output_name, source_name in (
            ("revenue_yoy_growth_percent", "Revenue"),
            ("eps_yoy_growth_percent", "EPS"),
        ):
            current_value = income.get(source_name)
            previous_value = prior_income.get(source_name)
            if current_value is not None and previous_value is not None:
                growth = _growth_percent(current_value, previous_value)
                if growth is not None:
                    income_result[output_name] = growth
    else:
        missing.append("income.same_period_last_year")

    balance_result: dict[str, float | str] = {"date": balance_date}
    balance_fields = {
        "total_assets": "TotalAssets",
        "total_liabilities": "Liabilities",
        "current_assets": "CurrentAssets",
        "current_liabilities": "CurrentLiabilities",
        "cash_and_cash_equivalents": "CashAndCashEquivalents",
    }
    for output_name, source_name in balance_fields.items():
        if source_name in balance:
            balance_result[output_name] = balance[source_name]
        else:
            missing.append(f"balance.{source_name}")

    total_assets = balance.get("TotalAssets")
    total_liabilities = balance.get("Liabilities")
    current_assets = balance.get("CurrentAssets")
    current_liabilities = balance.get("CurrentLiabilities")
    cash_and_equivalents = balance.get("CashAndCashEquivalents")

    if total_assets is not None and total_liabilities is not None:
        balance_result["total_equity_estimated"] = total_assets - total_liabilities
        debt_ratio = _safe_percent(total_liabilities, total_assets)
        if debt_ratio is not None:
            balance_result["debt_to_assets_percent"] = debt_ratio
    if current_assets is not None and current_liabilities not in {None, 0}:
        balance_result["current_ratio"] = current_assets / current_liabilities
    if cash_and_equivalents is not None and total_assets is not None:
        cash_ratio = _safe_percent(cash_and_equivalents, total_assets)
        if cash_ratio is not None:
            balance_result["cash_to_assets_percent"] = cash_ratio

    cash_result: dict[str, float | str] = {"date": cash_date}
    operating_cash_flow = cash.get("CashFlowsFromOperatingActivities")
    if operating_cash_flow is None:
        operating_cash_flow = cash.get("NetCashInflowFromOperatingActivities")
    capital_expenditure_cash_flow = cash.get("PropertyAndPlantAndEquipment")

    if operating_cash_flow is not None:
        cash_result["operating_cash_flow"] = operating_cash_flow
    else:
        missing.append("cash_flow.operating_cash_flow")
    if capital_expenditure_cash_flow is not None:
        cash_result["capital_expenditure_cash_flow"] = capital_expenditure_cash_flow
        cash_result["capital_expenditure_outflow"] = max(0.0, -capital_expenditure_cash_flow)
    else:
        missing.append("cash_flow.PropertyAndPlantAndEquipment")
    if operating_cash_flow is not None and capital_expenditure_cash_flow is not None:
        cash_result["free_cash_flow"] = operating_cash_flow + capital_expenditure_cash_flow

    return {
        "reporting_dates": {
            "income_statement": income_date,
            "balance_sheet": balance_date,
            "cash_flow_statement": cash_date,
        },
        "income_statement": income_result,
        "balance_sheet": balance_result,
        "cash_flow_statement": cash_result,
        "data_quality": {
            "dates_aligned": len({income_date, balance_date, cash_date}) == 1,
            "missing_metrics": missing,
        },
    }
