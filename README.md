# pipe-stock-analysis

Self-hostable, read-only Taiwan stock-analysis **MCP (Model Context Protocol, a standard for AI clients to invoke external tools)** server. It retrieves market data and computes reproducible indicators; it does not make investment decisions or place trades.

[繁體中文](README.zh-TW.md)

## Why this repository exists

Run your own instance. This project does **not** route other people to the maintainer's server, bundle a FinMind token, or require a hosted account.

- Your `FINMIND_TOKEN` stays in your local `.env` or deployment secret store.
- The token is read only from the server process environment, never from an MCP tool argument.
- `.env`, OAuth state, private keys, SQLite databases, and generated credentials are ignored by Git.
- FinMind is optional, but anonymous usage has lower limits. Get your own token from [FinMind](https://finmindtrade.com/).

## Tools

- Historical prices with FinMind → TWSE fallback
- Latest TWSE / TPEx official close quotes
- Valuation, monthly revenue, institutional flows, financial reports, margin trading
- MOPS material announcements and recent news
- Deterministic technical, fundamental, financial-health, institutional-flow, and margin summaries

Current coverage is Taiwan-listed securities. The generic repository name is intentional; do not mistake it for global-market coverage.

## Quick start: Docker Compose

```bash
git clone https://github.com/Gratia2533/pipe-stock-analysis.git
cd pipe-stock-analysis
cp .env.example .env
# Edit .env and add your own FINMIND_TOKEN

docker compose up -d --build
curl http://127.0.0.1:8000/healthz
```

MCP endpoint: `http://127.0.0.1:8000/mcp`

The Compose port binds to `127.0.0.1` deliberately. Do not expose an unauthenticated MCP endpoint to the public internet.

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
hermes mcp add pipe-stock-analysis --url http://127.0.0.1:8000/mcp
hermes mcp test pipe-stock-analysis
```

For stdio, point your MCP client to `uv run finance-mcp` from this repository. Configure the command and its environment in your own local client configuration; never commit tokens into this repository.

## Analysis skill

The reusable Hermes analysis workflow is included at [`skills/taiwan-stock-analysis/SKILL.md`](skills/taiwan-stock-analysis/SKILL.md). Because this repository is private, clone it on the Hermes host and copy the skill directory into `~/.hermes/skills/`. If you later make the repository public, install it directly from the raw GitHub URL:

```bash
hermes skills install https://raw.githubusercontent.com/Gratia2533/pipe-stock-analysis/main/skills/taiwan-stock-analysis/SKILL.md
```

## Optional OAuth

OAuth is off by default. If you enable it, you must supply your own public HTTPS issuer/resource URLs and credentials through deployment secrets. Runtime OAuth data belongs in a persistent private volume and is excluded by `.gitignore`.

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
- [TradingAgents](https://github.com/TauricResearch/TradingAgents): conceptual reference for role-based research workflows. This server deliberately keeps data retrieval and deterministic calculations separate from agent orchestration and investment decisions.

## License

[MIT](LICENSE)
