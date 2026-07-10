# pipe-stock-analysis

可自行部署、唯讀的台股分析 **MCP (Model Context Protocol，讓 AI 用戶端呼叫外部工具的標準協定)** 伺服器。它負責取得市場資料與計算可重現指標；不做投資決策，也不下單。

[English](README.md)

## 這個 repo 的定位

每個人部署自己的 instance，不會把使用者導向維護者的 server、不內建 FinMind token，也不要求使用任何代管帳號。

- `FINMIND_TOKEN` 只留在你的本機 `.env` 或部署平台的 secret store。
- token 只由 server process 的環境變數讀取，絕不從 MCP tool argument 傳入。
- `.env`、OAuth state、私鑰、SQLite 資料庫與自動產生的憑證都被 Git 忽略。
- FinMind 是可選項，但匿名額度較低。請自行到 [FinMind](https://finmindtrade.com/) 取得 token。

## 提供的工具

- 歷史價格，FinMind 失敗時可 fallback 至 TWSE
- TWSE／TPEx 官方最新收盤報價
- 估值、月營收、法人買賣、財務三表、融資融券
- MOPS 重大訊息與近期新聞
- 可重現的技術面、基本面、財務健康、法人流向與融資融券摘要

目前資料範圍是台灣上市櫃標的。repo 名稱故意不綁地區，但別誤認它支援全球市場。

## 快速啟動：Docker Compose

```bash
git clone https://github.com/Gratia2533/pipe-stock-analysis.git
cd pipe-stock-analysis
cp .env.example .env
# 編輯 .env，填入你自己的 FINMIND_TOKEN

docker compose up -d --build
curl http://127.0.0.1:8000/healthz
```

MCP endpoint：`http://127.0.0.1:8000/mcp`

Compose 預設只綁定 `127.0.0.1`。不要把未驗證的 MCP endpoint 直接暴露在公網。

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
hermes mcp add pipe-stock-analysis --url http://127.0.0.1:8000/mcp
hermes mcp test pipe-stock-analysis
```

若使用 stdio，請在 MCP client 的本機設定中指向此 repo 內的 `uv run finance-mcp`，並把環境變數配置留在本機，不要提交 token。

## 分析 Skill

可重用的 Hermes 分析流程已放在 [`skills/taiwan-stock-analysis/SKILL.md`](skills/taiwan-stock-analysis/SKILL.md)。此 repo 可供你的 Hermes instance 存取後，透過 raw GitHub URL 安裝：

```bash
hermes skills install https://raw.githubusercontent.com/Gratia2533/pipe-stock-analysis/main/skills/taiwan-stock-analysis/SKILL.md
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

## 授權

[MIT](LICENSE)
