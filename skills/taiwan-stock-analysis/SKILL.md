---
name: taiwan-stock-analysis
description: 使用 Finance MCP 編排台股多面向分析。
version: 0.1.0
author: Tommy Li, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Taiwan Stocks, Finance, Technical, Fundamental, Institutional, Events]
    category: finance
    related_skills: [stocks]
---

# Taiwan Stock Analysis Skill

這個 Skill 負責台股分析的角色分工、證據整合與輸出格式，不負責直接取得市場資料，也不執行交易。價格、估值、月營收、財務三表、法人買賣超、融資融券與官方重大訊息必須透過 Finance MCP 取得，避免把資料邏輯與 Agent 編排耦合在一起。

## When to Use

- 使用者要求分析台股、ETF 或比較多檔台股。
- 使用者詢問股價趨勢、估值、營收成長或法人動向。
- 使用者要求整理多方、空方與風險觀點。
- 使用者詢問「今天為什麼漲跌」，且現有資料能支持技術面或籌碼面的解釋。

不適用於即時下單、帳戶查詢、持倉管理或券商操作。

## Prerequisites

Hermes 必須已啟用名稱為 `finance` 的 MCP Server，並可使用以下工具：

- `mcp_finance_analyze_taiwan_stock_technical`
- `mcp_finance_analyze_taiwan_stock_fundamental`
- `mcp_finance_analyze_taiwan_stock_financial_health`
- `mcp_finance_analyze_taiwan_stock_institutional_flows`
- `mcp_finance_analyze_taiwan_stock_margin_trading`
- `mcp_finance_get_taiwan_stock_prices`
- `mcp_finance_get_taiwan_stock_official_quote`
- `mcp_finance_get_taiwan_stock_material_announcements`
- `mcp_finance_get_taiwan_stock_news`
- `mcp_finance_get_taiwan_stock_valuation`
- `mcp_finance_get_taiwan_stock_monthly_revenue`
- `mcp_finance_get_taiwan_stock_institutional_flows`
- `mcp_finance_get_taiwan_stock_financial_reports`
- `mcp_finance_get_taiwan_stock_margin_trading`

若 MCP 無法連線，停止分析並清楚指出資料取得失敗，不得憑空補數字。

## How to Run

可直接使用自然語言：

```text
分析 2330 最近的技術面、基本面與法人動向。
比較 2330 和 2454，列出多空理由與主要風險。
```

也可使用 Skill 指令：

```text
/taiwan-stock-analysis 2330
```

## Quick Reference

預設參數：

- 技術分析：`lookback_days=180`
- 基本面估值：`valuation_lookback_days=365`
- 月營收：`revenue_lookback_months=24`
- 財務健康：`lookback_years=3`
- 法人買賣超：`lookback_days=30`
- 融資融券：`lookback_days=30`
- 官方重大訊息：當日資料，預設 `include_details=false`

使用者指定期間時，以使用者需求為準，但必須符合 Tool 的參數限制。

## Procedure

### 1. 正規化標的

- 台股代碼以純數字傳入，例如 `2330`，不要附加 `.TW` 或 `.TWO`。
- 多檔比較時，每檔使用相同分析期間，避免比較基準不一致。
- 無法辨識代碼時先要求明確股票代碼，不要猜公司。

### 2. 取得六類分析結果

一般完整分析應呼叫：

1. `mcp_finance_analyze_taiwan_stock_technical`
2. `mcp_finance_analyze_taiwan_stock_fundamental`
3. `mcp_finance_analyze_taiwan_stock_financial_health`
4. `mcp_finance_analyze_taiwan_stock_institutional_flows`
5. `mcp_finance_analyze_taiwan_stock_margin_trading`
6. `mcp_finance_get_taiwan_stock_material_announcements`

只有在需要檢查原始數據、特定日期或解釋異常時，才呼叫其他 `get_*` 類工具。當使用者在意最新收盤價、上市／上櫃市場歸屬或資料新鮮度時，使用 `mcp_finance_get_taiwan_stock_official_quote` 交叉驗證；重大訊息先取標題與條款，只有需要判讀細節時才設定 `include_details=true`。

### 3. Hermes 角色編排

角色只存在於 Hermes，不應要求 MCP 扮演角色或做投資決策。

- 技術分析師：解讀趨勢、均線、RSI、MACD、區間報酬與波動率。
- 基本面分析師：解讀 PER、PBR、殖利率、月營收、EPS、利潤率、負債比、流動比率與自由現金流。
- 籌碼分析師：解讀外資、投信、自營商買賣超，以及融資與融券餘額的方向和持續性。
- 事件分析師：整理當日 MOPS 重大訊息的主旨、條款、公告發言日期與事實發生日；`announcement_date` 是發言日期，`event_occurrence_date` 才是事件日期，兩者不得混用。
- 多方研究員：只根據已取得資料整理最強的正面論點。
- 空方研究員：只根據已取得資料整理最強的反面論點與失效條件。
- 風險審查員：檢查資料日期、缺漏、指標衝突、樣本期間與過度推論。
- 整合者：輸出結論、證據與不確定性，不揭露冗長內部推理過程。

### 4. 證據規則

- 每個結論都要對應具體數據或明確的資料缺口。
- 技術指標只能描述目前型態，不得包裝成確定預測。
- 法人買超不等同未來必漲，法人賣超也不等同未來必跌。
- 融資增加不等同看多訊號，融券增加也不等同必然軋空；必須搭配價格與期間解讀。
- PER、PBR 必須搭配公司成長與自身歷史區間解讀，不能只用單一絕對值判斷便宜或昂貴。
- 「股價為何漲跌」若缺少新聞、公告或事件資料，只能說明價格、技術面與籌碼面的可觀察關聯，不能宣稱因果。
- 當日沒有 MOPS 重大訊息，只代表官方當日資料未命中，不代表市場沒有新聞、傳聞或舊事件影響。

### 5. 衝突處理

當不同面向互相矛盾時，不要強迫產生一致答案。例如：

- 技術面偏多，但月營收轉弱。
- 估值低於歷史平均，但法人持續賣超。
- 短期動能強，但年化波動率過高。

應直接列出衝突，說明其代表的時間尺度不同，並降低整體信心。

### 6. 輸出格式

完整分析依序包含：

1. 資料日期與範圍
2. 技術面：交代價格相對 MA5／MA20／MA60、RSI、MACD、支撐壓力與波動率，說明偏多或偏空的具體原因
3. 估值與月營收
4. 財務健康與現金流
5. 法人與融資融券籌碼
6. 官方重大訊息、近期新聞與事件限制
7. 外部環境：依公司曝險檢查關稅、匯率、利率、地緣政治、產業循環或政策變化；需要最新資料時必須查網路，不得靠記憶補寫
8. 多方論點
9. 空方論點
10. 主要風險、資料限制與持倉集中度
11. 綜合判斷、可執行情境與信心程度

每個建議都必須寫出「證據 → 影響 → 行動」：例如技術面轉弱、新聞催化或關稅風險如何改變持有、加碼、減碼或等待的理由。不得只丟結論或指標清單；新聞標題也不能直接當事實，需區分已確認事件、媒體推測與市場情緒。若使用者提供股數與成本，計算帳面損益、持倉市值占比，並優先檢查單一持股集中風險。

綜合判斷使用「偏多、偏空、中性、證據不足」其中之一。信心程度使用「高、中、低」，並附上一句原因。短線建議至少提供續強、震盪、跌破關鍵位三種情境及對應行動。不要輸出精確目標價，除非有獨立估值模型與清楚假設。

多檔比較時，最後加入比較矩陣，欄位至少包含趨勢、營收成長、獲利能力、現金流、估值、法人方向、融資融券、波動率與主要風險。

## Pitfalls

- 不要把角色人格、辯論結果或最終決策寫回 Finance MCP。
- 不要混用不同日期區間後直接排名。
- 不要因某個指標缺失就自行推算未提供的財務數字。
- 不要把資料來源的最新日期誤寫成今天。
- TPEx 官方報價來源目前只提供最新收盤快照，不得描述成歷史行情或拿來計算長期技術指標。
- MOPS Tool 目前是當日重大訊息，不是完整新聞資料庫，也不是歷史事件搜尋器。
- 不要把分析結果描述成保證獲利或個人化投資建議。

## Verification

執行以下檢查：

```bash
hermes mcp test finance
```

應顯示 Finance MCP 連線成功並發現分析工具。啟動新的 Hermes Session 後，輸入：

```text
使用台股分析流程分析 2330，列出技術面、基本面、法人籌碼與多空風險。
```

回覆必須引用 MCP 回傳的實際數據，且不可聲稱已執行交易。
