from __future__ import annotations

import httpx
import pytest

from finance_mcp.providers.twse import TwseClient


def _client(handler: httpx.MockTransport) -> TwseClient:
    return TwseClient(
        market_index_url="https://example.test/v1/exchangeReport/MI_INDEX",
        etf_ranking_url="https://example.test/v1/ETFReport/ETFRank",
        new_listing_url="https://example.test/v1/company/newlisting",
        holiday_schedule_url="https://example.test/v1/holidaySchedule/holidaySchedule",
        transport=handler,
        retry_base_seconds=0,
        cache_ttl_seconds=0,
    )


@pytest.mark.asyncio
async def test_twse_market_breadth_endpoints_are_normalized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/MI_INDEX"):
            return httpx.Response(
                200,
                json=[
                    {
                        "日期": "1150709",
                        "指數": "發行量加權股價指數",
                        "收盤指數": "45,354.61",
                        "漲跌": "-",
                        "漲跌點數": "379.80",
                        "漲跌百分比": "-0.83",
                        "特殊處理註記": "",
                    }
                ],
            )
        if request.url.path.endswith("/ETFRank"):
            return httpx.Response(
                200,
                json=[
                    {
                        "No": "1",
                        "ETFsSecurityCode": "0050",
                        "ETFsName": "元大台灣50",
                        "ETFsNumberofTradingAccounts": "1,204,776",
                    }
                ],
            )
        if request.url.path.endswith("/newlisting"):
            return httpx.Response(
                200,
                json=[
                    {
                        "Code": "7827",
                        "Company": "漢康-KY創",
                        "ApplicationDate": "1141124",
                        "Chairman": "劉世高",
                        "AmountofCapital ": "1302057",
                        "CommitteeDate": "1150309",
                        "ApprovedDate": "1150324",
                        "AgreementDate": "1150407",
                        "ListingDate": "",
                        "ApprovedListingDate": "1150529",
                        "Underwriter": "國泰",
                        "UnderwritingPrice": "120.00",
                        "Note": "創新板第一上市",
                    }
                ],
            )
        if request.url.path.endswith("/holidaySchedule"):
            return httpx.Response(
                200,
                json=[
                    {
                        "Name": "農曆春節前最後交易日",
                        "Date": "1150211",
                        "Weekday": "三",
                        "Description": "農曆春節前最後交易。<br>",
                    }
                ],
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    client = _client(httpx.MockTransport(handler))

    indices = await client.fetch_market_indices()
    assert indices == [
        {
            "date": "2026-07-09",
            "name": "發行量加權股價指數",
            "close": 45354.61,
            "change_points": -379.8,
            "change_percent": -0.83,
            "direction": "-",
            "special_note": None,
            "source": "TWSE/MI_INDEX",
        }
    ]

    etfs = await client.fetch_etf_rankings()
    assert etfs[0]["etf_id"] == "0050"
    assert etfs[0]["trading_account_count"] == 1204776
    assert etfs[0]["ranking_basis"] == "number_of_trading_accounts"

    listings = await client.fetch_new_listings()
    assert listings[0]["stock_id"] == "7827"
    assert listings[0]["application_date"] == "2025-11-24"
    assert listings[0]["approved_listing_date"] == "2026-05-29"
    assert listings[0]["listing_date"] is None
    assert listings[0]["underwriting_price"] == 120.0

    calendar = await client.fetch_holiday_schedule()
    assert calendar == [
        {
            "date": "2026-02-11",
            "name": "農曆春節前最後交易日",
            "weekday": "三",
            "description": "農曆春節前最後交易。",
            "source": "TWSE/holidaySchedule",
        }
    ]


@pytest.mark.asyncio
async def test_twse_market_breadth_rejects_non_list_payload() -> None:
    client = _client(
        httpx.MockTransport(lambda request: httpx.Response(200, json={"error": "bad"}))
    )

    with pytest.raises(RuntimeError, match="MI_INDEX returned an invalid response"):
        await client.fetch_market_indices()
