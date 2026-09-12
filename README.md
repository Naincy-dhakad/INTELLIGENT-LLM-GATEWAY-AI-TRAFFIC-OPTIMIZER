# Intelligent LLM Gateway & AI Traffic Optimizer

Production-oriented portfolio project for an LLM gateway, policy-driven traffic optimizer, and provider abstraction layer.

## Project status

**Phase 17 — Budget-aware deterministic routing complete.** Gateway-level budgets use authenticated-principal usage totals from optional PostgreSQL durable usage tracking and the existing Decimal cost estimator. Redis remains ephemeral. Dashboards, caching, optimization recommendations, and deployment remain outside this phase.

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

Copy `backend/.env.example` to `backend/.env` for local backend configuration. Configure `DEFAULT_PROVIDER_ID` explicitly (`phase3-mock`, `openai`, `anthropic`, `gemini`, or `ollama`). OpenAI, Anthropic, and Gemini require their corresponding provider credentials; Ollama uses optional `OLLAMA_BASE_URL` and `OLLAMA_DEFAULT_MODEL`. Providers with missing configuration are not registered. Gateway authentication is disabled by default (`GATEWAY_AUTH_ENABLED=false`). To enable it locally, set `GATEWAY_AUTH_ENABLED=true` and provide comma-separated development values in `GATEWAY_API_KEYS`; the application hashes these values at startup and never forwards them to providers. Never commit credentials or place them in frontend configuration. Gateway rate limiting is disabled by default (`RATE_LIMIT_ENABLED=false`) and requires gateway authentication when enabled. It uses Redis only for ephemeral fixed-window counters, fails closed with a safe `503` if Redis is unavailable, and excludes `/health`. Durable usage tracking is also disabled by default (`USAGE_TRACKING_ENABLED=false`); enable it with a local PostgreSQL `DATABASE_URL`, then apply `cd backend && alembic upgrade head`. Persistence is best-effort and never changes the original gateway response. Only normalized metadata is stored; prompts and completions are not stored. Tests use fake clients/transports and do not require provider credentials. Streaming remains unsupported.

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

The project deliberately keeps provider adapters, gateway authentication, routing, persistence, Redis, streaming, fallback, and deployment as separate bounded concerns.

### Phase 18 observability foundation

The gateway includes a dependency-free application observability port with normalized, request-correlated event types, a no-op implementation, bounded in-memory metrics for tests, and a standard-library structured logging adapter. The event schema excludes prompts, completions, credentials, API keys, authorization values, provider-native errors, and infrastructure URLs. Full lifecycle instrumentation, external metrics/tracing, `/metrics`, and `/ready` are intentionally deferred.

### Phase 18 lifecycle instrumentation

The gateway now emits safe `gateway_request_started` and `gateway_request_completed` events through the application observability port. Events reuse the existing request ID and measure total duration with a monotonic clock. Feature-specific events, external metrics/tracing, `/metrics`, and `/ready` remain deferred.

### Phase 18 authentication and rate-limit events

The gateway now emits safe `gateway_authentication_result` and `gateway_rate_limit_result` events at the existing API boundaries. Raw credentials, principal identifiers as metric labels, API keys, authorization values, and Redis internals are excluded. Classification, budget, routing, provider, retry, fallback, metrics, readiness, and tracing instrumentation remain deferred.

### Phase 18 classification observability

The gateway emits `gateway_classification_completed` with the normalized category, complexity level, complexity score, policy version, and request ID. Prompts, matched phrases, raw user content, and secrets are never emitted. Budget, routing, provider, retry, and fallback observability remain deferred.

### Phase 18 budget observability

The gateway emits `gateway_budget_decision` with normalized outcome, objective, policy version, and existing bounded error codes where applicable. Sensitive request content, credentials, and exact financial or budget amounts are never emitted. Routing, provider, retry, fallback, and metrics endpoint observability remain deferred.

### Phase 18 routing observability

The gateway emits `gateway_routing_decision` using the existing routing decision: normalized outcome, objective, policy version, provider/model identifiers, reason, and existing estimated cost, latency, and health values. Provider and model IDs are operational identifiers only. Prompts, secrets, and historical or remaining budget amounts are never emitted. Provider attempts, retries, fallbacks, `/metrics`, tracing, and dashboards remain deferred.
