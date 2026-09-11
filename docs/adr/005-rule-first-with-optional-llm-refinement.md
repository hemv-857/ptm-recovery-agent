# ADR-005: Rule-First Architecture with Optional LLM Refinement

## Status
Accepted

## Context
The recovery agent must work reliably without any external API keys, while also benefiting from LLM capabilities when available. The system needs to be deterministic for evaluation (same inputs → same outputs) but flexible enough to use LLMs for copywriting and classification refinement.

## Decision
Every decision path has a deterministic rule-based fallback. LLM calls are optional enhancements that improve quality but are never required for the system to function.

## Consequences
- **Zero-config operation**: The entire system runs without API keys. Judges can evaluate it immediately.
- **Deterministic evaluation**: The same seed produces the same batch, same classifications, same actions, same outcomes. LLM refinement is opt-in and doesn't affect reproducibility.
- **Graceful degradation**: If an LLM API is down, rate-limited, or returns invalid output, the system falls back to rules. The circuit breaker in `llm.py` prevents cascading failures.
- **Cost control**: LLM calls are tracked in unit economics. The system reports exactly how much LLM spend was required per decision.
- **Pluggable LLM backends**: The `llm.py` module supports any OpenAI-compatible endpoint. New backends can be added without changing the core pipeline.
- **Copy quality without LLM**: Hinglish templates in `copywriter.py` provide natural-sounding messages without any LLM. LLM refinement improves quality but the system works without it.

## Alternatives Considered
- **LLM-first with rule fallback**: Start with LLM, fall back to rules. Rejected because it adds latency and cost for every decision, and the fallback path is less tested than the primary path.
- **Hybrid (LLM for some paths, rules for others)**: More complex to maintain and test. Rejected because the rule-first approach with optional LLM enhancement achieves the same result with less complexity.
- **No LLM at all**: Simpler, but misses quality improvements in copywriting and classification refinement that the LLM provides when available.
