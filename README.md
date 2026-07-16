# Pipe Stock Analysis

Self-hostable, read-only financial-market **MCP (Model Context Protocol, a standard for AI clients to invoke external tools)** server for Taiwan-listed securities and global stocks. It retrieves market data and computes reproducible indicators; it does not make investment decisions or place trades.

[繁體中文](README.zh-TW.md)

## Why this repository exists

Run your own instance. This project does **not** route other people to the maintainer's server, bundle API credentials, require OpenConnector, or require a hosted account.

- The public default calls FinMind and Finnhub directly with credentials from your local `.env`.
- FinMind is optional and supports anonymous access with lower limits. Finnhub-backed tools require your own API key.
- Advanced deployments can select the OpenConnector adapter so this process holds only a narrow runtime token.
- `.env`, OAuth state, private keys, SQLite databases, and generated credentials are ignored by Git.

## Three-step self-hosting

```bash
# 1. Clone
git clone https://github.com/Gratia2533/pipe-stock-analysis.git && cd pipe-stock-analysis

# 2. Create local configuration; optionally add FINMIND_TOKEN / FINNHUB_API_KEY
cp .env.example .env

# 3. Start with the default Direct adapter on a standard Docker bridge network
docker compose up -d --build
```

To enter a Finnhub API key without exposing it in shell history, use the interactive helper and choose whether to restart the services:

```bash
./scripts/set-finnhub-key.sh
```

Verify both local services:

```bash
curl http://127.0.0.1:8010/healthz
curl http://127.0.0.1:8011/healthz
```

- Internal MCP without OAuth: `http://127.0.0.1:8010/mcp`
- OAuth-protected MCP: `http://127.0.0.1:8011/mcp`

Both endpoints are published only on `127.0.0.1`. Keep port 8010 private; expose only port 8011 through a public HTTPS reverse proxy or tunnel.

## Data backends

### Direct (default)

```dotenv
DATA_BACKEND=direct
FINMIND_TOKEN=
FINNHUB_API_KEY=
```

The service starts without either optional credential, so TWSE, TPEx, MOPS, news, and deterministic analytics remain available. FinMind uses anonymous limits when its token is empty. A Finnhub tool reports a configuration error only when called without `FINNHUB_API_KEY`.

### OpenConnector (advanced, Linux/WSL)

Use this mode when a dedicated OpenConnector already exposes the curated FinMind and Finnhub Actions. The advanced Compose injects only its runtime token; upstream credentials remain in OpenConnector's encrypted credential store. For fail-closed separation, `FINMIND_TOKEN` and `FINNHUB_API_KEY` must be unset in this mode.

```dotenv
DATA_BACKEND=openconnector
OPEN_CONNECTOR_BASE_URL=http://127.0.0.1:8001
OPEN_CONNECTOR_RUNTIME_TOKEN=your-local-runtime-token
```

Because a bridged container cannot reach a connector bound to host loopback, the advanced Compose file intentionally uses Linux/WSL host networking while both services remain bound to `127.0.0.1`:

```bash
docker compose -f compose.openconnector.yaml up -d --build
```

Direct and OpenConnector modes use the same MCP tools and analytics code. OpenConnector is an adapter, not a separate branch or repository.

## Tools

- Historical prices with FinMind → TWSE fallback
- Latest TWSE / TPEx official close quotes
- TWSE market and sector indices, ETF rankings, listing/IPO pipeline, and trading calendar
- Valuation, monthly revenue, institutional flows, financial reports, margin trading
- MOPS material announcements and recent news
- Deterministic technical, fundamental, financial-health, institutional-flow, and margin summaries
- Finnhub global symbol search, quotes, candles, company profiles, financial metrics, reported statements, and company news

## Local development

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
uv sync --dev
set -a; source .env; set +a
MCP_TRANSPORT=streamable-http MCP_HOST=127.0.0.1 uv run finance-mcp
```

For stdio clients on the same host:

```bash
MCP_TRANSPORT=stdio uv run finance-mcp
```

## Hermes configuration

For a local HTTP deployment:

```bash
hermes mcp add pipe-stock-analysis --url http://127.0.0.1:8010/mcp
hermes mcp test pipe-stock-analysis
```

For stdio, point your MCP client to `uv run finance-mcp` from this repository. Configure the command and its environment in your own local client configuration; never commit tokens into this repository.

## Analysis skill

The reusable cross-market Hermes analysis workflow is included at [`skills/stock-analysis/SKILL.md`](skills/stock-analysis/SKILL.md). Install it directly from the raw GitHub URL:

```bash
hermes skills install https://raw.githubusercontent.com/Gratia2533/pipe-stock-analysis/main/skills/stock-analysis/SKILL.md
```

## OAuth deployment

Compose starts a separate OAuth-protected instance on port 8011 while leaving the port 8010 instance available for trusted local clients. Set `FINANCE_OAUTH_ISSUER_URL`, `FINANCE_OAUTH_RESOURCE_URL`, `FINANCE_OAUTH_USERNAME`, and `FINANCE_OAUTH_PASSWORD` for the public deployment. OAuth runtime data is stored in a persistent private volume and excluded by `.gitignore`.

The issuer and resource URLs must use the same public HTTPS hostname that forwards to `127.0.0.1:8011`.

## Verification

```bash
uv run ruff check .
uv run pytest -q
```

## Scope and disclaimer

- Read-only data and deterministic calculations only.
- No orders, broker accounts, portfolio access, or personalized investment recommendations.
- Data availability, latency, and accuracy depend on upstream providers. Verify material information independently.
- This is software, not investment advice.

## References and inspiration

- [FinMind](https://github.com/FinMind/FinMind/tree/master): upstream open-source project behind the optional FinMind market-data integration. This repository does not vendor FinMind code or credentials.
- [Finnhub](https://finnhub.io/): optional upstream provider for global-stock data. Endpoint availability depends on the user's Finnhub plan.
- [TradingAgents](https://github.com/TauricResearch/TradingAgents): conceptual reference for role-based research workflows. This server deliberately keeps data retrieval and deterministic calculations separate from agent orchestration and investment decisions.

## License

[MIT](LICENSE)
