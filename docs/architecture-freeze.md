# Intelligent LLM Gateway & AI Traffic Optimizer
## Phase 0 — Architecture Freeze

**Status:** Final for Phase 0
**Date:** 2026-08-26
**Scope:** Architecture and engineering constraints only. No application features are implemented by this document.

## 1. Repository inspection and decision record

The repository currently contains only a minimal `README.md` and the initial commit. No architecture, design, API, infrastructure, or technology documentation was present to inspect. Consequently, there are no existing repository decisions to preserve beyond the product direction in the project name and request.

The decisions below are therefore the baseline architecture contract for the next phase. They intentionally choose a production-oriented **modular monolith** rather than prematurely splitting the gateway into services. A decision may be changed later only through an explicit ADR that records the problem, alternatives, operational impact, and migration plan.

### Phase 0 exclusions

The following are deliberately not implemented in Phase 0:

- Provider integrations or provider SDK calls
- Routing, fallback, authentication, Redis, PostgreSQL, or frontend functionality
- Production deployment manifests and cloud-specific resources
- Prompt/content storage or an analytics pipeline

## 2. Final system architecture

```text
Browser / approved API clients
              |
       TLS + edge controls
              |
   React/TypeScript web application
              |
       Versioned HTTP API
              |
   FastAPI gateway (stateless)
   |       |        |        |
 auth   request   policy   provider
boundary pipeline engine  adapters
              |        |
          Redis    PostgreSQL
       ephemeral   durable state
              |
       OpenTelemetry + metrics/logs
```

The first deployable backend is a stateless Python service. Its internal boundaries are explicit modules, not network services:

1. **API layer:** versioned HTTP endpoints, request/response validation, correlation headers, and transport error mapping.
2. **Application layer:** request orchestration and use cases; it must not contain provider-specific or SQL details.
3. **Domain layer:** provider capabilities, routing candidates, policy decisions, fallback decisions, and normalized errors.
4. **Infrastructure layer:** provider adapters, Redis/PostgreSQL repositories, configuration, telemetry, and external clients.

The gateway is the system of record for the request contract and routing decision, but it is not a proxy that exposes provider-specific APIs. It exposes a stable normalized contract and translates provider behavior at the adapter boundary.

The backend remains horizontally scalable and should not require local session state. Long-running work, if introduced later, must be added as an explicitly designed asynchronous workflow rather than hidden inside the request path.

## 3. Backend architecture

- **Language/runtime:** Python 3.12+ with FastAPI and Pydantic v2.
- **HTTP client:** `httpx` behind a narrow internal client interface. Provider SDKs are optional and must remain inside adapters if used.
- **Concurrency:** async I/O for outbound provider calls; bounded timeouts and connection pools are mandatory.
- **Dependency direction:** API → application → domain; infrastructure implements application/domain ports. Domain code cannot import FastAPI, Redis, SQLAlchemy, or provider SDKs.
- **Initial persistence approach:** PostgreSQL accessed through a repository/unit-of-work boundary. SQLAlchemy 2.x and Alembic are the selected implementation tools for the persistence layer when Phase 1 begins.
- **Deployment unit:** one backend image initially. Split services only when a measured scaling, isolation, or ownership requirement justifies it.

The application layer owns orchestration. It must be possible to unit-test a routing decision and a fallback decision using in-memory ports without running a database, Redis, or a provider.

## 4. Frontend architecture

- **Stack:** React + TypeScript, built with Vite.
- **Role:** an operator/developer console and gateway client, not a second routing engine.
- **Browser API access:** same-origin, versioned relative URLs (for example `/api/v1/...`). The browser must never call `localhost`, provider endpoints, or provider APIs directly.
- **State:** server state is fetched through a small typed API client; local UI state stays local. Do not duplicate routing or provider-health decisions in the browser.
- **Security:** no provider key, database credential, Redis credential, or privileged policy secret is shipped to the bundle. Route guards may improve UX but are not authorization.
- **Boundary:** frontend work is out of scope for Phase 0. The backend API contract is the source of truth for future generated or hand-written TypeScript types.

## 5. Provider abstraction

The domain defines a normalized provider port, conceptually:

```text
complete(request, model, options, deadline) -> ProviderResult
health() -> ProviderHealth
capabilities() -> CapabilitySet
```

Concrete adapters implement this port and own provider-specific authentication, URL construction, payload mapping, response parsing, usage extraction, and error classification. Provider-specific types must not leak into API or domain models.

The normalized request must preserve the information required for policy decisions: task/model requirements, messages or input, generation parameters, stream preference, timeout budget, tenant/request identity, and correlation ID. The normalized result includes output, finish reason, usage where available, selected provider/model, latency, and a normalized request ID.

Capability declarations are configuration/data, not scattered conditionals. They must cover model/task support, streaming, tool/function calling, context limits, modalities, and known operational constraints. Unknown usage or capability data is represented explicitly; it is never silently invented.

Provider credentials are resolved only in the backend adapter/infrastructure boundary. They come from environment or a secret manager and are never persisted in ordinary configuration rows or returned by an API.

## 6. Request lifecycle

1. The edge terminates TLS and applies coarse request-size, origin, and abuse controls.
2. The API validates the versioned request and establishes a correlation/request ID. An authenticated principal, when present, is accepted from the authentication boundary; the gateway does not trust arbitrary browser-supplied identity headers.
3. The application enforces request limits and creates a deadline budget.
4. The policy engine loads the applicable routing policy and evaluates eligible providers/models using declared capabilities and current health/operational state.
5. The gateway records the selected route decision (without raw sensitive content by default), then invokes exactly one adapter attempt at a time.
6. The adapter translates the normalized request, performs the provider call with bounded timeout, and translates the response or error.
7. A permitted fallback may select the next candidate while budget remains. Fallback is not an unbounded retry loop.
8. The gateway returns a normalized response, including request/correlation ID and usage/provider metadata allowed by the API contract.
9. Durable metadata and metrics are recorded asynchronously or at a bounded point in the path; telemetry failure must not turn a successful provider response into a gateway failure.

Streaming is a separate contract, not an incidental flag: headers and an initial route decision are committed before the stream begins, and fallback after bytes have been emitted is forbidden. The first implementation may defer streaming while keeping this boundary explicit.

## 7. Routing engine concept

Routing is a deterministic, explainable policy evaluation, not an LLM call. The engine receives a request, tenant/policy context, provider capability catalog, and health/operational signals. It produces an ordered list of eligible candidates plus a reason code and policy version.

Candidate eligibility is evaluated before scoring. Typical hard constraints are task/model support, context and modality support, streaming/tool requirements, tenant allow/deny rules, region/data constraints, and current circuit state. Only then may candidates be scored by configured priorities such as:

- policy priority and capability fit
- health/circuit status
- latency and error-rate signals
- cost ceiling and estimated price
- configured provider/model preference
- deterministic tie-breaker

Scores and reasons must be inspectable in logs/audit metadata. Randomness, hidden provider-specific branches, and live unbounded calls to an AI model are prohibited in the critical routing path. Health signals are advisory; a provider call remains authoritative for request success.

## 8. Fallback concept

Fallback is an explicit policy outcome, not automatic retry behavior. A fallback candidate may be used only when the error is classified as transient or capacity-related (for example timeout, rate limit, or provider availability failure), the request is still within its deadline, and policy permits the candidate.

Do not fallback for invalid requests, unsupported capabilities, authentication/permission failures, content/safety refusals, or deterministic client errors unless a policy explicitly defines a semantically safe alternative. Do not retry or fallback after a streaming response has emitted data. Attempt count, total deadline, and per-provider timeout are bounded. A request must not fan out to providers in parallel by default.

The final response must preserve a normalized error and include the request ID. Metadata should identify attempted providers and the terminal classification without exposing secrets or raw prompts.

## 9. PostgreSQL responsibilities

PostgreSQL is the durable source of truth for:

- versioned provider/model capability and pricing metadata
- routing policies, policy versions, and activation history
- tenant/project configuration and durable quotas where required
- request outcome metadata, latency, usage, cost estimates, route/fallback decisions, and audit events
- migration-managed relational constraints and administrative records

Raw prompts, completions, and tool payloads are **not stored by default**. If future requirements need content retention, it requires a separate privacy/data-retention decision, explicit opt-in, encryption, redaction, access controls, and a deletion policy. PostgreSQL is not a high-volume metrics store, distributed lock service, or request cache.

## 10. Redis responsibilities

Redis is an ephemeral acceleration and coordination layer for:

- short-lived provider health/circuit-breaker state
- rate-limit counters and rolling operational signals
- bounded TTL caches for safe, explicitly approved reads
- short-lived distributed coordination where needed

Redis is never the source of truth for policies, credentials, audit history, or completed request records. Every Redis use has a TTL, failure behavior, and stampede/eviction policy. A Redis outage must degrade to a defined safe mode (for example conservative routing and/or fail-closed rate limiting), not silently erase durable state. Response caching is disabled by default because LLM responses may contain sensitive or user-specific data.

## 11. Authentication and authorization boundary

Authentication is owned by the edge/identity boundary (OIDC/JWT validation or an equivalent trusted gateway integration), not by provider adapters or the frontend. The backend verifies signature, issuer, audience, expiry, and required claims; it derives tenant/project identity from verified claims and server-side mapping.

Authorization is enforced server-side at the API/application boundary for every protected operation, including policy changes, provider configuration, usage views, and request execution. Roles/scopes and tenant isolation are separate checks. The frontend only presents authorized actions. Provider credentials are service credentials and are never user credentials.

The exact identity provider and token transport are intentionally deferred until Phase 1 deployment requirements are known; this does not weaken the boundary above.

## 12. Configuration strategy

Configuration is twelve-factor by default:

- non-secret deployment settings come from environment/config files validated at startup;
- secrets come from a secret manager in production, with environment injection permitted for local development;
- routing policy and provider catalog data are versioned durable data, not hidden environment variables;
- configuration is strongly typed, validated once, and exposed only through a settings object;
- missing required secrets/settings fail startup rather than causing partial operation;
- dynamic operational values (health, counters, circuit state) belong in Redis or telemetry, not process globals.

Never log configuration values wholesale. Secret rotation and configuration reload behavior must be specified before production rollout; default behavior is restart-based reload for secrets and explicit version activation for policies.

## 13. Error handling strategy

All backend errors map to a stable, versioned error envelope containing a machine-readable code, safe message, request ID, and optional retryability/details fields. Internal exception text, stack traces, credentials, provider headers, and raw prompts are not returned to clients.

Errors are classified at the adapter boundary into validation, capability, authentication, authorization, rate-limit, timeout, provider-availability, safety/refusal, internal, and cancellation categories. The application decides whether classification allows fallback. HTTP status codes are transport mapping, not the domain error model. Cancellation and client disconnects stop downstream work when possible.

## 14. Observability strategy

Use OpenTelemetry for traces and context propagation; expose Prometheus-compatible metrics and emit structured JSON logs. Every request, provider attempt, fallback decision, and policy activation carries correlation/request ID and tenant-safe dimensions.

Required signals include request volume, success/error rates by normalized category, end-to-end and provider latency, timeout/rate-limit counts, fallback rate, circuit state, token/usage and estimated cost, Redis/PostgreSQL health, and policy version. High-cardinality or sensitive values (raw prompts, completions, authorization headers, API keys) are never labels or log fields. Sampling and retention are environment-specific, with production defaults documented before launch.

Telemetry must be non-blocking where practical and must have bounded exporter timeouts. Monitoring the monitoring path is part of the deployment readiness checklist.

## 15. Testing strategy

The test pyramid is part of the architecture:

- **Unit tests:** routing eligibility/scoring, fallback rules, error classification, policy evaluation, deadline arithmetic, and domain invariants with no external services.
- **Contract tests:** normalized provider port and each adapter against recorded/sanitized fixtures; API schemas and error envelopes.
- **Integration tests:** PostgreSQL migrations/repositories, Redis TTL/circuit/rate-limit behavior, and application orchestration using disposable dependencies.
- **End-to-end tests:** a full gateway request using a fake provider server, including timeout and fallback paths; frontend tests only after its contract exists.
- **Resilience/security tests:** provider slowdowns, malformed responses, Redis/database outages, credential leakage checks, tenant isolation, request-size limits, and dependency vulnerability scanning.

Tests must be deterministic and must not call real paid providers in CI. Load and cost/latency benchmarks are prerequisites for production capacity decisions, not substitutes for correctness tests.

## 16. Security boundaries and non-negotiables

- TLS is required outside local development; secrets are managed outside source control.
- Validate request size, schema, model/tool parameters, and content-type at ingress.
- Treat prompts, completions, tool inputs, and provider responses as untrusted data; redact them from logs and escape them in the UI.
- Apply tenant isolation in authorization and persistence queries; never rely on frontend filtering.
- Use least-privilege database/Redis/provider credentials and rotate them.
- Restrict outbound egress to approved provider endpoints where the deployment platform supports it.
- Protect administrative policy/provider endpoints with stronger scopes and audit every mutation.
- Use timeouts, connection limits, circuit breakers, and rate limits to contain abuse and cost.
- Keep dependency versions, migrations, and API versions reviewable; do not make breaking contract changes silently.

## 17. Decisions not to change casually

1. The backend starts as a modular monolith with clean ports/adapters.
2. The normalized provider interface is the only application-facing provider contract.
3. Routing is deterministic, policy-driven, and explainable; an AI model is not in the critical routing path.
4. PostgreSQL is durable truth; Redis is disposable/ephemeral support state.
5. Browser code never receives provider credentials or calls providers directly.
6. Authentication/authorization is a backend/edge concern, never a frontend-only concern.
7. Raw LLM content is not retained by default.
8. Fallback is bounded, classification-driven, deadline-aware, and prohibited after streaming begins.
9. Versioned API contracts, structured errors, correlation IDs, and redacted observability are mandatory.
10. A real operational or ownership need, backed by measurements and an ADR, is required before introducing microservices, parallel provider fan-out, response caching, or a new persistence system.

## 18. Phase 1 prerequisites (not Phase 1 implementation)

Before implementation begins, the team must:

- confirm the first API use cases and write versioned request/response schemas;
- choose the deployment target, identity provider, supported regions, and secret manager;
- define the initial provider/model catalog, capability vocabulary, pricing source, and policy format;
- define timeout, retry, fallback, rate-limit, data-retention, and streaming semantics with concrete defaults;
- decide the minimum durable request metadata and tenant/audit retention periods;
- choose local development dependencies and disposable PostgreSQL/Redis test strategy;
- establish repository quality gates: formatting, linting, type checking, tests, dependency scanning, and secret scanning;
- create an ADR process and an API compatibility/versioning policy;
- define dashboards, alert thresholds, and redaction rules before enabling production traffic.

No Phase 1 feature work is included in this architecture freeze.
