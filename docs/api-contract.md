# Gateway API Contract
## Version 1 design

**Status:** Ready for implementation in a future phase
**Scope:** Contract design only. This document does not implement any endpoint.

This is the first public contract for the normalized gateway API. It preserves the Phase 0 modular-monolith and provider-adapter boundaries while leaving routing, provider calls, persistence, Redis, and authentication for later phases.

## 1. Versioning

The API uses a major version in the path:

```text
/api/v1/...
```

The path version changes only for a breaking change to the public contract. Backward-compatible additions (optional response fields, new capability values, and new error details) remain within `v1`. Provider SDK versions, provider model revisions, and internal routing policy versions are not API versions.

Unknown request fields should be rejected in the first implementation so client typos are visible. New request features must therefore be added as optional fields or through a new major version. Responses must be parsed defensively so clients tolerate additional fields.

The health endpoint from Phase 1 remains `GET /health`; it is an operational probe and is not part of the gateway request API.

## 2. Initial endpoint

### `POST /api/v1/chat`

Submits one normalized, non-streaming chat request. The endpoint accepts a provider/model-neutral request and returns a provider-neutral response. It does not expose any provider's native request or response schema.

The Phase 10 implementation exposes this contract through one provider-neutral abstraction with hardened validation, deterministic classification-aware routing, and normalized cost-aware policy. The deterministic mock provider and configured OpenAI, Anthropic, Gemini, and Ollama adapters all translate to normalized provider models. Authentication, authorization, rate limiting, persistence, and fallback remain for later phases.

### Request classification (Phase 8)

Before routing, the application analyzes the latest relevant user message using bounded, deterministic local rules. It makes no provider, model, network, LLM, or machine-learning call and never logs or stores prompt content. The fixed category vocabulary is `question_answer`, `coding`, `debugging`, `summarization`, `translation`, `reasoning`, `architecture_design`, `creative_writing`, `data_analysis`, `conversation`, and `unknown`.

Category precedence is explicit and stable: debugging, coding, architecture design, data analysis, summarization, translation, reasoning, creative writing, conversation, question-answer, then unknown. Complexity is an independent bounded score from 0 to 100: 0–30 is `LOW`, 31–70 is `MEDIUM`, and 71–100 is `HIGH`. Length, structure, questions, code, technical terms, reasoning, multi-step structure, and task signals may contribute.

### Classification-aware routing (Phase 9)

After hard eligibility filtering, `balanced` routing uses policy version `classification-v1`. Declared `coding` capability receives a deterministic category preference for coding and debugging; declared `reasoning` capability receives one for reasoning and architecture-design requests. HIGH complexity gives a smaller preference to declared reasoning capability. These are inspectable policy preferences, not claims about provider quality. A configured default provider receives a smaller preference, and provider ID is the final lexical tie-breaker. Explicit provider and model constraints remain authoritative and never trigger fallback.

### Cost-aware routing (Phase 10)

`routing.objective` may be `cost`, which uses policy version `classification-cost-v1` and selects the lowest estimated-cost eligible provider/model. `routing.max_cost_usd` is a hard ceiling for either objective; candidates that cannot be proven within the ceiling are excluded. Explicit provider, model, capability, and availability constraints are evaluated first.

Provider metadata may declare normalized `ModelPricing` values in USD per 1,000,000 input and output tokens. Pricing is configuration data supplied by the composition root; no live pricing API or provider call is used. Missing pricing is `UNKNOWN`, never zero. A cost objective with no priced eligible candidate returns `cost_unavailable`; a ceiling with known candidates but none within budget returns `cost_limit_exceeded`.

Cost estimates use a bounded standard-library approximation: input characters divided by four, rounded up, and either the caller's bounded `max_output_tokens` or a default of 256 output tokens capped at 4096. The result is explicitly an estimate, not provider billing. Successful routing metadata may include the selected `estimated_cost_usd`; it is not an actual charge.

### Latency-aware routing (Phase 11)

`routing.objective` may also be `latency`. It uses policy version `classification-cost-latency-v1` and selects the lowest configured estimated latency among eligible provider/models. `routing.max_latency_ms` is a hard ceiling even when the objective is `balanced` or `cost`; an exact match is accepted. Cost and latency ceilings are applied as hard filters in that order before objective selection, never as a weighted score.

Provider metadata may declare `ModelLatency` in positive milliseconds. This is configured estimate data, not a live measurement and not a health score. Providers without latency metadata have `UNKNOWN` latency: unknown values are never zero, cannot satisfy a ceiling, and cannot win latency routing. No network probe or provider call is made. `latency_unavailable` means no usable latency metadata exists; `latency_limit_exceeded` means known candidates exist but all exceed the ceiling. Successful latency routing may include `estimated_latency_ms` in safe routing metadata.

### Provider health-aware routing (Phase 12)

`routing.objective` may be `quality`. This means the highest configured normalized health score currently available to the gateway, not subjective answer quality, model intelligence, or benchmarking. Health policy uses version `classification-cost-latency-quality-v1` and ranks eligible models by health score descending, then provider ID and model ID lexically.

Normalized health scores are integers from 0 to 100 with statuses `healthy`, `degraded`, `unavailable`, and `unknown`. The mock provider derives status deterministically: scores at least 80 are healthy, scores from 40 through 79 are degraded, and scores below 40 are unavailable. Unavailable models are excluded from every routing objective. Unknown health remains distinct from perfect health, may participate in balanced/cost/latency routing when otherwise eligible, but cannot win quality routing. If no usable health exists, routing returns `quality_unavailable`; an explicitly requested unavailable provider returns `provider_unhealthy`.

Health values are configured metadata only. There are no live probes, runtime failure tracking, background monitors, retries, or fallback behavior in this phase.

### Provider and routing boundary (Phase 7)

The application calls a provider-neutral `Provider` protocol with normalized `ProviderChatRequest` and `ProviderChatResponse` models. A `ProviderRegistry` performs only provider ID lookup and exposes the explicitly configured default; it does not rank, score, retry, or fallback. The deterministic routing policy filters registered metadata by explicit provider, capability, model, and applicable cost constraints, then applies the configured balanced, cost, latency, or quality policy and stable tie-breakers. It does not use measured latency, health, request content, randomness, or LLM calls; latency policy uses only configured normalized estimates. Real providers are registered only when their required configuration is present; tests use fake clients/transports and the `phase3-mock` implementation remains deterministic and credential-free.

### Request headers

| Header | Required | Meaning |
| --- | --- | --- |
| `Content-Type: application/json` | Yes | JSON request body. |
| `X-Request-ID` | No | Client-supplied correlation ID. If supplied, it must be a bounded printable identifier. The gateway may reject invalid values. If absent, the gateway generates one. This ID is echoed in the response and error body. |
| `X-Request-Timeout-Ms` | No | Relative timeout hint in milliseconds. Optional alternative to the body `timeout_ms`; sending both is invalid. It is bounded by server policy and does not override the server's maximum deadline. |
| `Authorization` | Future | Authentication transport is intentionally deferred. When enabled, it will be validated at the edge/backend boundary and will never be forwarded to a provider. |

A client should set its HTTP transport timeout greater than the requested gateway timeout plus network overhead. A client must not treat `X-Request-ID` as an authentication or idempotency credential.

## 3. Request schema

```json
{
  "messages": [
    { "role": "system", "content": "You are concise." },
    { "role": "user", "content": "Summarize this text." }
  ],
  "model": "optional-model-id",
  "provider": "optional-provider-id",
  "requirements": {
    "capabilities": ["text_generation", "structured_output"]
  },
  "routing": {
    "objective": "balanced",
    "max_cost_usd": 0.05,
    "max_latency_ms": 30000
  },
  "generation": {
    "max_output_tokens": 500,
    "temperature": 0.2,
    "top_p": 0.9
  },
  "timeout_ms": 60000,
  "metadata": {
    "client_tag": "summarization-job"
  },
  "stream": false
}
```

The example is illustrative; the endpoint is not available in Phase 1.

### Fields

| Field | Type | Required | Contract meaning |
| --- | --- | --- | --- |
| `messages` | array of message objects | Yes | Ordered conversation input. At least one message is required. Message content is UTF-8 text in v1. |
| `messages[].role` | `system \| user \| assistant` | Yes | Normalized conversation role. Provider-native roles are adapter concerns. |
| `messages[].content` | string | Yes | Untrusted input text. Size and total context limits are enforced by the gateway. |
| `model` | string or null | No | Opaque normalized model identifier. If absent, policy selects a model. It is a preference/constraint, not a provider SDK name. |
| `provider` | string or null | No | Opaque normalized provider identifier. If absent, policy may choose any eligible provider. A requested provider that is unavailable is not silently treated as an authentication request. |
| `requirements` | object | No | Hard request requirements used for capability eligibility. |
| `requirements.capabilities` | array of capability IDs | No | Every listed capability must be supported by the selected model. Unknown capability IDs are invalid in v1. |
| `routing` | object | No | Caller constraints and a deterministic routing objective. |
| `routing.objective` | `quality \| latency \| cost \| balanced` | No | `balanced`, `cost`, `latency`, and `quality` are implemented policy objectives. Default is `balanced`. |
| `routing.max_cost_usd` | non-negative number or null | No | Optional hard estimated per-request cost ceiling. It is a policy constraint, not a billing guarantee. |
| `routing.max_latency_ms` | positive integer or null | No | Optional hard configured estimated-latency ceiling, bounded to 120,000 ms. It is not a live measurement. |
| `generation` | object | No | Provider-neutral generation controls supported by the selected model. Unsupported controls are rejected or normalized; they are never silently ignored. |
| `generation.max_output_tokens` | positive integer or null | No | Maximum requested output size. |
| `generation.temperature` | number `0..2` or null | No | Sampling preference, when supported. |
| `generation.top_p` | number `(0..1]` or null | No | Nucleus sampling preference, when supported. |
| `timeout_ms` | positive integer or null | No | Relative request deadline requested by the client. Default and maximum are server policy values; the initial target is 60,000 ms default and 120,000 ms maximum. |
| `metadata` | bounded string map | No | Caller correlation/business tags safe for operational metadata. It must not contain prompts, completions, secrets, tokens, or unbounded/high-cardinality data. |
| `stream` | boolean | No | Reserved compatibility field. v1 accepts only `false` or omission; `true` is rejected as unsupported until a streaming contract is implemented. |

The v1 request intentionally does not include provider-native parameters, API keys, arbitrary provider payloads, tool schemas, multimodal parts, or conversation persistence identifiers. Those can be added through explicitly versioned, normalized contracts when requirements are known.

## 4. Response schema

A successful response has HTTP `200 OK` and this normalized shape:

```json
{
  "id": "req_01J...",
  "object": "chat.response",
  "status": "completed",
  "message": {
    "role": "assistant",
    "content": "A concise summary."
  },
  "provider": {
    "id": "provider-id",
    "model": "model-id"
  },
  "finish_reason": "stop",
  "usage": {
    "input_tokens": 42,
    "output_tokens": 18,
    "total_tokens": 60
  },
  "latency_ms": 842,
  "routing": {
    "policy_version": "policy-2026-01",
    "decision_reason": "capability_and_policy_match",
    "fallback_used": false,
    "attempt_count": 1
  },
  "request_id": "req_01J..."
}
```

The example is illustrative. Fields have these meanings:

- `id` is the gateway request/response identifier and is stable across any future provider attempts for that request.
- `object` is a stable discriminator (`chat.response`).
- `status` is `completed` for this synchronous contract. Future asynchronous contracts must use a separate endpoint/schema rather than changing this meaning.
- `message` is normalized assistant output. Provider-specific candidates, raw response objects, and SDK metadata are not returned.
- `provider.id` and `provider.model` identify the normalized selected route. They are opaque identifiers, not credentials or native SDK objects.
- `finish_reason` is normalized (`stop`, `length`, `tool_call`, `content_filter`, or `unknown`). The initial non-tool implementation may return only applicable values.
- `usage` is nullable. Token counts are included only when reliably reported; unknown values are `null`, never guessed.
- `latency_ms` is gateway-observed end-to-end request latency, rounded to an integer.
- `routing` is safe operational metadata. `policy_version`, reason codes, fallback status, and bounded `attempt_count` may be omitted or restricted by tenant policy. Phase 8 classification fields, the Phase 10 bounded `estimated_cost_usd`, the Phase 11 `estimated_latency_ms`, and the Phase 12 selected `health_score` may be included. Candidate rankings, secrets, internal URLs, matched phrases, pricing internals, latency internals, health details, and raw prompts are never exposed.
- Retryable normalized provider failures are limited to timeout, rate limit, and unavailable. Execution allows one same-provider retry after a deterministic 50 ms backoff and at most one deterministic eligible fallback, all sharing the original deadline. Explicit-provider requests never cross providers; non-retryable failures never retry.
- `request_id` duplicates the correlation ID in the body for clients that do not preserve response headers. The same value is returned as `X-Request-ID`.

Optional response fields may be added within v1. Clients must ignore fields they do not understand.

## 5. Error contract

Every error uses `application/json` and the same envelope:

```json
{
  "error": {
    "code": "provider_timeout",
    "message": "The gateway timed out while waiting for an eligible provider.",
    "request_id": "req_01J...",
    "retryable": true,
    "details": {
      "stage": "provider_call"
    }
  }
}
```

This is an example only. `message` is safe for clients and must not contain stack traces, provider response bodies, authorization headers, API keys, or raw prompt/completion content. `details` is optional, bounded, and machine-readable; clients must not depend on undocumented detail keys. Phase 10 cost routing may return `cost_unavailable` when no eligible candidate has configured pricing, or `cost_limit_exceeded` when known candidates exceed `max_cost_usd`; both are non-retryable validation/policy outcomes. Quality routing may return `quality_unavailable` when no eligible model has usable health metadata, or `provider_unhealthy` for an explicitly requested unavailable provider; these are also non-retryable policy outcomes.

### Initial status and code mapping

| HTTP status | Error code | Meaning | Retry guidance |
| --- | --- | --- | --- |
| `400` | `invalid_request` | Malformed JSON, invalid field, conflicting timeout headers/body, or unsupported request value. | No; fix the request. |
| `401` | `authentication_required` | Authentication is enabled and the request has no valid identity. | No; obtain valid credentials. |
| `403` | `not_authorized` | Verified identity cannot use the requested tenant/model/provider/policy. | No unless authorization changes. |
| `408` | `client_timeout` | The client-side deadline was already exceeded or the client cancelled before completion. | Depends on caller semantics. |
| `422` | `unsupported_capability` | No eligible model satisfies a declared hard capability or generation requirement. | No unless requirements change. |
| `422` | `model_not_supported` | The explicitly selected provider does not advertise the requested model. | No unless the model or provider changes. |
| `429` | `rate_limited` | Gateway or provider rate limit was reached. | Yes, after `Retry-After` when present. |
| `502` | `provider_error` | An eligible provider returned a non-transient or unmappable failure. | Usually no; policy may classify a specific case differently. |
| `503` | `all_providers_unavailable` | No eligible provider can currently serve the request, including permitted fallback candidates. | Yes, with backoff. |
| `504` | `gateway_timeout` | The gateway deadline expired before a complete response. | Yes only if caller has a new deadline; fallback stops for the expired request. |
| `501` | `streaming_not_supported` | The reserved `stream: true` mode is not available in the initial v1 implementation. | No; use non-streaming v1 or a future streaming contract. |
| `500` | `internal_error` | Unexpected gateway failure. | Retry cautiously with a new request ID. |

A provider authentication failure is normalized as `provider_error` or `all_providers_unavailable` according to policy and is never returned with provider secrets. A future contract revision may add more specific stable codes without exposing native provider codes.

## 6. Timeout and deadline semantics

- `timeout_ms` is a relative client hint. The server validates it against configured minimum/maximum values and creates one monotonic gateway deadline when request processing starts.
- If no value is supplied, the initial documented default is 60 seconds. The initial maximum is 120 seconds. Deployment policy may lower these values, but must not silently extend a caller's requested deadline.
- `X-Request-Timeout-Ms` is an equivalent header form for clients that prefer transport metadata. Supplying both header and body values is invalid; there is no ambiguous precedence.
- The gateway's own client/edge timeout must exceed the gateway deadline enough to return a structured error. Client transport timeouts should exceed that total by network overhead.
- Each provider attempt receives a shorter derived timeout bounded by the remaining gateway deadline. A future fallback attempt consumes the same total budget; it never receives a fresh full timeout.
- When the deadline expires before any response bytes are returned, the gateway stops/cancels downstream work where possible and returns `504 gateway_timeout`. It must not begin another fallback attempt after the deadline.
- A response after the gateway deadline is discarded. Telemetry may record the late attempt without changing the public result.
- Retries and fallback are future application behavior, not client-visible retry instructions. This deadline model is deliberately compatible with bounded, sequential fallback.

## 7. Streaming semantics

Streaming is **reserved for a later contract extension**. v1 is synchronous and non-streaming:

- `stream` may be omitted or set to `false`.
- `stream: true` is not supported in the initial implementation and must produce `501 streaming_not_supported` once the endpoint exists.
- A future streaming contract must define its media type, event schema, terminal event, disconnect behavior, and usage reporting before activation.
- The future contract must commit the route before the first output event. Once output bytes/events have been emitted, provider fallback is forbidden, as required by the architecture freeze.
- Streaming must be additive or use a clearly negotiated representation; it must not reinterpret the existing JSON `200` response.

## 8. Provider, model, and capability vocabulary

These are normalized domain concepts represented by stable opaque identifiers:

- **Provider:** an adapter-backed service identifier such as `provider-id`. It identifies an operational provider, not its SDK or endpoint URL.
- **Model:** a provider-independent catalog identifier such as `model-id`. Provider/model mapping, limits, and pricing live behind the catalog and are not encoded in request payloads.
- **Capability:** a catalog-controlled identifier describing a supported behavior. Initial vocabulary:
  - `text_generation`
  - `reasoning`
  - `coding`
  - `structured_output`
  - `tool_use`
  - `streaming`

Capability values are eligibility requirements, not quality claims. The vocabulary is extensible, but adding a capability requires catalog and compatibility review. Provider-native feature names and SDK types must not appear in this API.

## 9. Request identification and observability

- The gateway generates a cryptographically strong opaque request ID when `X-Request-ID` is absent. The exact format is an implementation detail; clients must treat it as an opaque string.
- A valid supplied `X-Request-ID` is propagated as the correlation ID. It must be bounded and sanitized to prevent log injection. The gateway may replace invalid values.
- The same ID is used across API logs, traces, routing decisions, provider attempts, metrics exemplars, and future database records.
- Provider attempt IDs, if needed internally, are separate child identifiers and must not replace the public request ID.
- Observability may retain method, API version, status, normalized error code, policy version, selected provider/model, capability requirements, latency buckets, usage, fallback flag, and bounded metadata tags.
- Raw prompts, completions, authorization headers, API keys, cookies, and provider payloads/responses are excluded by default. Metadata is redacted, bounded, and subject to tenant privacy policy.
- Request IDs are correlation identifiers, not secrets, authorization credentials, or idempotency keys.

## 10. Security considerations

- Authentication and authorization are future edge/backend responsibilities. This contract does not imply anonymous production access.
- Provider credentials are resolved only inside backend infrastructure adapters and are never accepted in this request, response, frontend configuration, logs, or error details.
- Input sizes, message counts, metadata keys/values, numeric ranges, and total context must be bounded at validation time.
- Prompt, completion, metadata, and provider output are untrusted data. They must be redacted from logs and safely rendered by future frontend code.
- Tenant identity and policy context come from verified server-side identity, not from arbitrary request metadata.
- Error details and routing metadata are safe allowlisted fields; native provider errors, URLs, headers, and credentials remain internal.

## 11. Future compatibility rules

1. Keep `/api/v1/chat` provider-neutral; add provider features only as normalized optional fields.
2. Do not expose provider-native payloads, SDK types, or provider-specific error contracts.
3. Preserve the distinction between caller preferences (`routing`) and hard capability requirements (`requirements`).
4. Preserve request identity across all future routing and fallback attempts.
5. Keep deadline semantics as one total request budget; do not let fallback reset it.
6. Treat usage as nullable and provider/model/route metadata as safe, policy-controlled output.
7. Add streaming through explicit negotiation or a separate contract; never change the meaning of the existing JSON response.
8. Add asynchronous submission, conversation persistence, tool calls, multimodal content, or idempotency only with a dedicated schema/ADR when their semantics are defined.
9. Add fields as optional where possible. Use a new major path version for incompatible role/content, error-envelope, timeout, or response-shape changes.
10. Keep the public contract independent of PostgreSQL, Redis, authentication vendor, observability vendor, and deployment platform choices.
