from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence


def summarize_institutional_flows(rows: Sequence[dict[str, object]]) -> dict[str, object]:
    if not rows:
        raise ValueError("institutional flow rows cannot be empty")

    ordered_rows = sorted(rows, key=lambda row: str(row["date"]))
    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"buy": 0, "sell": 0, "net": 0})

    total_buy = 0
    total_sell = 0
    dates: set[str] = set()
    for row in ordered_rows:
        name = str(row["name"])
        buy = int(row["buy"])
        sell = int(row["sell"])
        net = buy - sell
        dates.add(str(row["date"]))
        total_buy += buy
        total_sell += sell
        totals[name]["buy"] += buy
        totals[name]["sell"] += sell
        totals[name]["net"] += net

    return {
        "start_date": str(ordered_rows[0]["date"]),
        "end_date": str(ordered_rows[-1]["date"]),
        "trading_day_count": len(dates),
        "row_count": len(ordered_rows),
        "total_buy": total_buy,
        "total_sell": total_sell,
        "total_net": total_buy - total_sell,
        "by_investor_type": dict(sorted(totals.items())),
    }
