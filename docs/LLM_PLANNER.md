# LLM Planner — Leos Agent Runtime

## ModelClient Contract

```python
class ModelClient(Protocol):
    def generate(self, request: ModelRequest) -> ModelResponse: ...
```

Vendor-neutral. No OpenAI/Anthropic/Gemini SDK bundled. Implement `ModelClient`
to connect any LLM backend.

## ModelRequest / ModelResponse

- `ModelRequest`: prompt, system, schema, model, temperature, metadata
- `ModelResponse`: text, parsed_json, model, usage, raw
- `ModelUsage`: input_tokens, output_tokens, total_tokens, cost_usd

## StructuredLLMPlanner

- Uses a `ModelClient` and `PromptRegistry` to generate `PlanProposal` objects.
- Validates every LLM output against `PLAN_PROPOSAL_SCHEMA`.
- Rejects unknown tools, non-object arguments, and empty rationale.
- Supports `max_retries` on failure.
- Records audit events: `llm.planner.requested`, `response_received`,
  `proposals_validated`, `proposals_rejected`.

## PromptRegistry

- Versioned, hash-addressable prompt templates.
- Built-in: `planner.proposal` (v1).
- Template rendering via `PromptTemplate.render(**kwargs)`.
- Hash: SHA-256 of prompt_id:version:template (first 16 hex chars).

## Untrusted Observations

Observations with `TrustLevel.UNTRUSTED_EXTERNAL` are labeled as DATA in
the planner prompt. The prompt explicitly prohibits treating them as
instructions. The model cannot declare approval — that is handled by the
separate `PolicyEngine`/`ApprovalGate` path.

## Audit Events

| Event | Payload |
|-------|---------|
| `llm.planner.requested` | model, prompt_id, prompt_version, prompt_hash, goal_id |
| `llm.planner.response_received` | model, text_preview (truncated) |
| `llm.planner.proposals_validated` | model, proposal_count |
| `llm.planner.proposals_rejected` | model, error_type, error_message |

## Vendor Integration Guidance

1. Implement `ModelClient` for your LLM backend.
2. Set `request.schema` for JSON mode if your backend supports it.
3. Populate `response.parsed_json` for faster validation.
4. Register custom prompts in `PromptRegistry` as needed.

## Non-goals

- No real LLM SDK bundled.
- No automatic approval through model output.
- No model fine-tuning or training infrastructure.
- No multi-model orchestration.
