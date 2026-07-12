---
name: stock-analysis
description: 使用 Finance MCP 編排台股與全球股票的多面向分析，依市場選擇可用資料並統一證據、風險與情境輸出。
version: 0.2.0
author: Gratia
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Stocks, Global Stocks, Taiwan Stocks, Finance, Technical, Fundamental, Events]
    category: finance
    related_skills: [stocks]
---

# Stock Analysis Skill

## Overview

這個 Skill 提供跨市場的股票研究流程。分析框架不因市場而改變，但資料工具與可得欄位必須依市場分流：台股使用 FinMind、TWSE、TPEx、MOPS 與新聞來源；全球股票使用 Finnhub。MCP 只負責唯讀資料與確定性計算，投資論點、衝突處理與風險整合由 Hermes 完成。

## When to Use

- 分析台股、美股或其他 Finnhub 可辨識的全球股票。
- 比較同市場或跨市場標的。
- 整理技術面、基本面、事件、多空論點與主要風險。
- 判讀價格異動，但只在資料能支持時描述原因。

不適用於下單、券商帳戶、持倉管理或保證報酬。

## Prerequisites

Hermes 必須啟用 `finance` MCP。全球股票工具需要部署端設定 `FINNHUB_API_KEY`；若缺少金鑰、方案不支援 endpoint 或回傳空值，必須標示資料缺口，不得把台股資料或模型記憶冒充全球股票資料。

## Market Routing

### 台股

股票代碼用純數字，例如 `2330`，不要附加 `.TW` 或 `.TWO`。完整分析優先使用：

1. `analyze_taiwan_stock_technical`
2. `analyze_taiwan_stock_fundamental`
3. `analyze_taiwan_stock_financial_health`
4. `analyze_taiwan_stock_institutional_flows`
5. `analyze_taiwan_stock_margin_trading`
6. `get_taiwan_stock_material_announcements`
7. `get_taiwan_stock_news`

需要最新官方收盤、市場歸屬或原始資料時，再使用對應 `get_taiwan_stock_*` 工具交叉驗證。

### 全球股票

先用 `search_global_stock_symbols` 確認 symbol；常見美股如 `NVDA`、`AAPL` 使用大寫 ticker。完整分析依需求取得：

1. `get_global_stock_quote`
2. `get_global_stock_prices`
3. `get_global_stock_profile`
4. `get_global_stock_basic_financials`
5. `get_global_stock_financial_reports`
6. `get_global_stock_news`

Finnhub endpoint 可用性取決於方案。若 K 線或財報 endpoint 被拒絕，不能因此推定公司沒有資料；應寫成「供應商方案或 endpoint 不可用」。

## Procedure

### 1. 確認標的與市場

- 數字代碼預設按台股處理；英文字母 ticker 預設先查 Finnhub。
- 公司名稱或代碼有歧義時先搜尋 symbol，不猜測交易所。
- 多檔比較採相同日期區間、幣別標示與指標定義。

完成條件：每個標的都有明確 symbol、交易所或市場，以及資料來源。

### 2. 取得價格、公司與事件資料

先取得報價與公司 profile，再平行取得價格歷史、基本財務、財報及新聞。台股另取得法人、融資融券與 MOPS；全球股票不應虛構台灣式籌碼指標。

完成條件：清楚列出每項資料的最新日期、期間、來源與缺漏。

### 3. 多思維分析

市場差異不應縮減分析維度。依資料可得性執行：

- 技術面：趨勢、動能、區間報酬、波動率、支撐與壓力。
- 基本面：營收與獲利成長、利潤率、資產負債、現金流、估值。
- 產業面：競爭位置、需求週期、供應鏈、替代技術與議價能力。
- 事件面：財報、公司新聞、監管、產品與資本配置。
- 總體面：利率、匯率、關稅、政策、地緣政治與景氣循環。
- 多方研究員：整理資料支持的最強正面論點。
- 空方研究員：整理最強反面論點、反證與失效條件。
- 風險審查員：檢查資料日期、缺漏、指標衝突、估值假設與敘事過度延伸。

完成條件：每個重要論點皆有數據、已確認事件或明確資料缺口對應。

### 4. 處理衝突與市場差異

不要強迫不同時間尺度得出同一方向。例如短期動能強、但估值高與現金流轉弱，可以同時成立。跨市場比較時必須額外處理：

- 幣別與匯率
- 會計準則與財報期間
- 交易時間與報價日期
- 產業結構與市場估值基準
- 資料供應商欄位定義

完成條件：衝突被保留並解釋，不用單一分數掩蓋不確定性。

### 5. 輸出

依序輸出：

1. 標的、市場、資料日期與範圍
2. 價格與技術面
3. 基本面、財務健康與估值
4. 產業、事件與總體環境
5. 市場特有資料：台股籌碼；全球股票則標示 Finnhub 可得欄位
6. 多方論點
7. 空方論點
8. 主要風險與資料限制
9. 綜合判斷、信心程度與三種情境

結論使用「偏多、偏空、中性、證據不足」之一；信心用「高、中、低」。每個行動建議寫成「證據 → 影響 → 行動」，但不得把一般研究包裝成個人化投資指示。除非有獨立估值模型與假設，不輸出精確目標價。

## Evidence Rules

- 報價、新聞與財報都要附資料日期；不得把最新資料日期寫成今天。
- 新聞標題不是已確認事實，區分公司公告、媒體報導與市場推測。
- 技術指標描述型態，不保證未來方向。
- 估值需對照成長、產業與自身歷史，不能只看單一 PER 或 PBR。
- 台股法人與融資融券不能套用到全球股票；全球股票缺少此資料時直接省略。
- Finnhub 基本方案的 endpoint 限制是資料限制，不是公司基本面訊號。
- 缺少事件證據時，只能描述價格與指標的相關變化，不能宣稱漲跌原因。

## Verification

```bash
hermes mcp test finance
```

確認工具連線後，分別測試：

```text
使用股票分析流程分析 2330，列出技術、基本面、籌碼與風險。
使用股票分析流程分析 NVDA，列出技術、基本面、產業、事件與多空風險。
```

回覆必須引用 MCP 實際回傳資料、標示缺漏，且不可聲稱已執行交易。
