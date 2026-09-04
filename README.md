# Intelligent LLM Gateway & AI Traffic Optimizer

Production-oriented portfolio project for an LLM gateway, policy-driven traffic optimizer, and provider abstraction layer.

## Project status

**Phase 12 — Provider health-aware deterministic routing complete.** Balanced, cost-aware, and latency-aware routing remain intact. The quality objective now ranks eligible candidates by configured normalized health score; health is metadata, not live monitoring or measured provider reliability. Retries, fallback, budgets, authentication, database, Redis, streaming, and optimization recommendations are not implemented.

- [Architecture freeze](docs/architecture-freeze.md): source of truth for system and implementation boundaries.
- [Gateway API contract](docs/api-contract.md): versioned `/api/v1` contract and provider-neutral boundary.

## Local development

### Backend

From the repository root:

```bash
cd backend
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell
.venv\\Scripts\\Activate.ps1
# Windows cmd
.venv\\Scripts\\activate.bat
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m uvicorn gateway.main:app --reload
```

The foundation health check is available at <http://127.0.0.1:8000/health>.

Cost routing uses normalized USD-per-million-token pricing declared in provider metadata. The deterministic mock provider's input/output pricing is configurable with `PHASE3_MOCK_INPUT_USD_PER_MILLION_TOKENS` and `PHASE3_MOCK_OUTPUT_USD_PER_MILLION_TOKENS`; missing pricing is unknown, never zero. Its configured latency is controlled by `PHASE3_MOCK_LATENCY_MS`; this is an estimate for routing, not a live measurement. Its normalized health score is controlled by `PHASE3_MOCK_HEALTH_SCORE` from 0 to 100; this is deterministic metadata, not runtime health monitoring.
It is intentionally self-contained and does not connect to PostgreSQL or Redis.

Run backend tests from `backend`:

```bash
python -m pytest
```

### Frontend

From the repository root:

```bash
cd frontend
npm install
npm run dev
```

Open the Vite URL shown in the terminal, normally <http://localhost:5173>.

For Windows PowerShell, the commands are the same. If script execution policy prevents activation of a Python virtual environment, use `backend\\.venv\\Scripts\\activate.bat` from Command Prompt or run the Python executable directly.

Validate the frontend without starting the dev server:

```bash
npm run typecheck
npm run build
```

## Environment configuration

Copy `backend/.env.example` to `backend/.env` for local backend configuration. Configure `DEFAULT_PROVIDER_ID` explicitly (`phase3-mock`, `openai`, `anthropic`, `gemini`, or `ollama`). OpenAI, Anthropic, and Gemini require their corresponding `*_API_KEY` and `*_DEFAULT_MODEL`; Ollama uses optional `OLLAMA_BASE_URL` and `OLLAMA_DEFAULT_MODEL`. Providers with missing configuration are not registered. Never commit credentials or place them in frontend configuration. Tests use fake clients/transports and do not require provider credentials. Streaming remains unsupported.

## Project layout

```text
backend/
  src/gateway/
    api/             # HTTP/API boundary
    application/     # application use cases (reserved)
    config/          # typed settings
    domain/          # domain model and ports (reserved)
    infrastructure/  # external integrations (reserved)
  tests/
frontend/
  src/               # React application shell
  package.json

docs/architecture-freeze.md
```

Phase 5 deliberately provides configured provider adapters without implementing routing, persistence, Redis, authentication, authorization, streaming, fallback, or production deployment.
