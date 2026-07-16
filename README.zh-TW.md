# Pipe Stock Analysis

可自行部署、唯讀的金融市場 **MCP (Model Context Protocol，讓 AI 用戶端呼叫外部工具的標準協定)** 伺服器，涵蓋台灣上市櫃標的與全球股票。它負責取得市場資料與計算可重現指標；不做投資決策，也不下單。

[English](README.md)

## 這個 repo 的定位

每個人部署自己的 instance。本專案不會把使用者導向維護者的 server、不內建 API credential、不強制使用 OpenConnector，也不要求任何代管帳號。

- 公開版預設直接讀取本機 `.env`，呼叫 FinMind 與 Finnhub。
- FinMind 可不填 token，以較低的匿名額度使用；Finnhub 工具則需要使用者自己的 API key。
- 進階部署可選擇 OpenConnector Adapter，讓本服務只持有最小權限 runtime token。
- `.env`、OAuth state、私鑰、SQLite 資料庫與自動產生的憑證都被 Git 忽略。

## 三步自架

```bash
# 1. Clone
git clone https://github.com/Gratia2533/pipe-stock-analysis.git && cd pipe-stock-analysis

# 2. 建立本機設定；視需要填入 FINMIND_TOKEN／FINNHUB_API_KEY
cp .env.example .env

# 3. 使用預設 Direct Adapter 與標準 Docker bridge network 啟動
docker compose up -d --build
```

驗證：

```bash
curl http://127.0.0.1:8010/healthz
```

MCP endpoint：`http://127.0.0.1:8010/mcp`

宿主機只會在 `127.0.0.1` 發布 MCP。不要把未驗證的 MCP endpoint 直接暴露至公網。

## 資料後端

### Direct（預設）

```dotenv
DATA_BACKEND=direct
FINMIND_TOKEN=
FINNHUB_API_KEY=
```

即使未提供這兩個選用 credential，服務仍可啟動，TWSE、TPEx、MOPS、新聞與確定性分析功能可以繼續使用。FinMind token 留空時使用匿名額度；未設定 `FINNHUB_API_KEY` 時，只有實際呼叫 Finnhub 工具才會收到明確的設定錯誤。

### OpenConnector（進階，Linux／WSL）

此模式適合已有專用 OpenConnector，且其中提供固定 FinMind／Finnhub Actions 的部署。進階 Compose 只會注入 runtime token；上游 credential 保留在 OpenConnector 加密 credential store。為了維持 fail-closed 隔離，此模式下必須清除 `FINMIND_TOKEN` 與 `FINNHUB_API_KEY`。

```dotenv
DATA_BACKEND=openconnector
OPEN_CONNECTOR_BASE_URL=http://127.0.0.1:8001
OPEN_CONNECTOR_RUNTIME_TOKEN=your-local-runtime-token
```

bridge container 無法連線到只綁定宿主機 loopback 的 connector，因此進階 Compose 刻意使用 Linux／WSL host networking，並讓兩個服務繼續只綁定 `127.0.0.1`：

```bash
docker compose -f compose.openconnector.yaml up -d --build
```

Direct 與 OpenConnector 模式共用相同 MCP tools 與 analytics；OpenConnector 只是 Adapter，不是另一個 branch 或 repository。

## 提供的工具

- 歷史價格，FinMind 失敗時可 fallback 至 TWSE
- TWSE／TPEx 官方最新收盤報價
- TWSE 大盤與產業指數、ETF 排行、上市／IPO 流程、交易行事曆
- 估值、月營收、法人買賣、財務三表、融資融券
- MOPS 重大訊息與近期新聞
- 可重現的技術面、基本面、財務健康、法人流向與融資融券摘要
- Finnhub 全球股票代碼搜尋、報價、K 線、公司資料、財務指標、財報與公司新聞

## 本機開發

需要 Python 3.11+ 與 [uv](https://docs.astral.sh/uv/)。

```bash
cp .env.example .env
uv sync --dev
set -a; source .env; set +a
MCP_TRANSPORT=streamable-http MCP_HOST=127.0.0.1 uv run finance-mcp
```

若 MCP client 與服務在同一台主機，可用 stdio：

```bash
MCP_TRANSPORT=stdio uv run finance-mcp
```

## Hermes 設定

本機 HTTP 部署可直接加入：

```bash
hermes mcp add pipe-stock-analysis --url http://127.0.0.1:8010/mcp
hermes mcp test pipe-stock-analysis
```

若使用 stdio，請在 MCP client 的本機設定中指向此 repo 內的 `uv run finance-mcp`，並把環境變數配置留在本機，不要提交 token。

## 分析 Skill

可重用的跨市場 Hermes 分析流程已放在 [`skills/stock-analysis/SKILL.md`](skills/stock-analysis/SKILL.md)，可透過 raw GitHub URL 安裝：

```bash
hermes skills install https://raw.githubusercontent.com/Gratia2533/pipe-stock-analysis/main/skills/stock-analysis/SKILL.md
```

## 可選 OAuth

OAuth 預設關閉。若要開啟，你必須自行提供公開 HTTPS issuer/resource URL 與登入憑證，並透過部署平台的 secrets 傳入。OAuth 執行期資料必須放在私有 persistent volume，且已列入 `.gitignore`。

## 驗證

```bash
uv run ruff check .
uv run pytest -q
```

## 範圍與聲明

- 只提供唯讀資料與確定性計算。
- 不下單、不讀券商帳戶、不存取持倉，也不提供個人化投資建議。
- 資料可用性、延遲和準確度取決於上游來源；重要資訊請自行交叉驗證。
- 這是軟體，不是投資建議。

## 參考來源與設計啟發

- [FinMind](https://github.com/FinMind/FinMind/tree/master)：可選 FinMind 市場資料整合的上游開源專案。本 repo 不內嵌 FinMind 程式碼或任何憑證。
- [Finnhub](https://finnhub.io/)：全球股票資料的可選上游來源；可用 endpoint 取決於使用者自己的 Finnhub 方案。
- [TradingAgents](https://github.com/TauricResearch/TradingAgents)：作為角色導向研究流程的概念參考。本 server 刻意把資料取得與確定性計算，和 agent 編排及投資決策分開。

## 授權

[MIT](LICENSE)
