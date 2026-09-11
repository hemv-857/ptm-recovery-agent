# Architecture

## Design goals

1. **The policy gate is the product.** Money-moving agents live or die on
   boundedness. One pure-function module (`app/policy.py`) owns every rule; it
   takes `(case, proposed_time, config)` and returns EXECUTE / DEFER(at) /
   BLOCK(reason). No IO, no side effects — trivially testable (see
   `tests/test_policy.py`).
2. **Plan one step, re-plan always.** The selector picks the *next* single best
   action from current state; after every outcome the loop re-plans. No brittle
   scripted sequences.
3. **Measurement is not an afterthought.** Group assignment happens at ingest
   (stratified by failure class), the world model gives control cases organic
   recovery clocks, and `measure.py` computes lift with bootstrap CIs plus the
   cost side (spend, redundant contacts, opt-outs).
4. **Deterministic by default.** LLM is an optional polish layer; without keys
   everything runs offline and reproducibly (seeded).

## Component diagram

```
                    ┌──────────────────────────────────────────────────┐
                    │                  app/main.py (FastAPI)           │
                    │  POST /webhooks/paytm    POST /inbound/reply  │
                    │  POST /tick                 POST /cases/*/approve│
                    │  POST /cases/*/opt_out      GET  /calculator     │
                    │  GET  /report               GET  /audit/{id}     │
                    │  GET  /cases/recent         GET  / (dashboard)   │
                    │  middlewares: rate limit (per-IP) + tenant route  │
                    └───────┬──────────────────────────┬───────────────┘
                            │ payment.failed           │ due actions
                            ▼                          ▼
                     ┌────────────┐            ┌────────────┐
                     │ agent.py   │            │ executor.py│──▶ ChannelAdapter
                     │ ingest     │            │ gate→send  │    (SMS/WA/email)
                     └─────┬──────┘            └─────┬───────┘
                           ▼                         │ receipts
┌──────────────────┐                ▼
                  │ classifier.py    │        ┌──────────────┐
                  │ rules + conf.    │        │ store.py     │◀── audit events
                  └────────┬─────────┘        └──────▲───────┘
                          ▼                         │
                 ┌──────────────────┐                │
                 │ selector.py      │──next action──▶│
                 │ failure-aware    │                │
                 └────────┬─────────┘                │
                          ▼                          │
                 ┌──────────────────┐                │
                 │ policy.py        │──verdict──────▶│
                 │ compliance gate  │                │
                 └──────────────────┘                │

 simulate/engine.py drives the same components over a synthetic cohort:
 heap of (case_ingest | action_due | organic | sweep) events, simulated clock.
 Sidecars: notifier.py (Slack, best-effort) · static/dashboard.html (Chart.js,
 vendored offline; report_html.py is the zero-dependency fallback).
```

## Data model

- **FailedPayment** — normalized webhook payload (paise amounts, UTC timestamps).
- **RecoveryCase** — the aggregate: classification + confidence, treatment/
  control group, attempt history, recovery/write-off state, human-approval flag,
  `written_off_reason`. `case_id` is derived from `payment_id` (stable across
  restarts: webhook redelivery upserts instead of duplicating, and the seeded
  simulation is reproducible because the world model salts draws with case id).
- **Intervention** — one scheduled action: type, time, rendered message,
  reasoning dict (strategy, why, link, delivery receipt), cost, status.
- **AuditEvent** — append-only: actor ∈ {classifier, selector, policy, executor,
  webhook, world, human}, event type, case id, payload JSON.

Every state transition emits an audit event first-class — the audit trail is how
`GET /report` can claim "every number computed from the trail".

## Policy rules (config.yaml → policy:)

| rule | default | outcome when hit |
|---|---|---|
| opt-out registry | per-customer flag | BLOCK |
| quiet hours IST | 22:00–08:00 | DEFER to window open |
| rolling attempt cap | 3 in 72h | BLOCK (write-off path) |
| cooldown between contacts | 240 min | DEFER to cooldown end |
| auto-action amount cap | ₹25,000 — bounds MONEY actions only | DEFER until human approves (contacts/escalation unaffected) |
| case expiry | 14 days | BLOCK |
| final follow-up window | 7 days | write-off |
| ladder exhaustion (B2B receivables) | 3 automated touches | ESCALATE_HUMAN → finance ops queue, audit-logged |

Compliance stance: recovery messages are transactional (TRAI DND does not bar
them) but every message carries a STOP footer, opt-outs are honored globally on
the customer, frequency caps are stricter than DLT norms typically require, and
the automated path always terminates in a human handoff rather than silent drop.

## Strategy table (selector.py)

| FailureClass | strategy | rationale |
|---|---|---|
| INSUFFICIENT_FUNDS | salary-cycle-aligned retry/nudge (1st/5th, 10am IST) | balances recover best after salary credit |
| NETWORK_TIMEOUT | retry after 20 min (auto-collect if mandate) | transient; short backoff suffices |
| ISSUER_UNAVAILABLE | retry after 45 min | bank-side outage needs longer backoff |
| HARD_DECLINE | never re-charge instrument; alternate-instrument link | blocked/fraud-flagged instruments must not be retried |
| MANDATE_ISSUE | re-auth nudge before any charge | charging a dead mandate wastes attempts & annoys |
| CUSTOMER_ABANDONMENT | reminder ladder +1h/+24h/+3d | intent exists; decay is fast early |
| INVOICE_OVERDUE (B2B) | dunning ladder +2h/+1d/+3d (voice call at final notice if ≥₹25k), then ESCALATE_HUMAN | escalating tone per etiquette; high-value cases earn a human-voice touch; relationship cases end with humans, not silent drop |
| SUBSCRIPTION_FAILED | grace retry 6h → re-auth 24h → pause/downgrade offer 2d | re-presentment often succeeds; churn path must offer retention, not just nag |
| UNKNOWN | email only, conservative | low confidence ⇒ lowest-cost channel |

Channel ladder escalates WhatsApp → SMS → email by contact index (configurable),
with per-channel costs feeding the spend metric. `NUDGE_VOICE` is a special
contact: a TTS script spoken on an outbound call plus the payment link delivered
by SMS in the same breath (`VOICE_PROVIDER_URL` posts `{to, tts_script,
sms_followthrough}` to any BSP/TTS stack; simulator by default).

## Promise-to-pay loop

```
nudge sent ──▶ customer replies "kal pakka" ──▶ POST /inbound/reply
                                                 │ deterministic parser (regex, no LLM)
                                 PROMISE ◀───────┘  (STOP→opt-out, paid→recover, refuse→close)
                                    │
              ladder paused (scheduled actions superseded)
                                    │
                    CHECK_PROMISE action scheduled at due + 6h
                                    │
              kept: recovery webhook lands first → case closes
              broken: check fires → audited → ladder resumes where it left off
```

The same CHECK_PROMISE action type serves simulation and live mode; in
simulation the world model samples promise replies (probability) and honoring
(keep probability), so keep-rate and money-via-promises are measurable.

## Measurement methodology

1. Stratified split at ingest: within each failure class, cases alternate into
   ~70% treatment / 30% control (`assign_groups`).
2. Control cases get no interventions but do have organic recovery clocks in the
   world model; their observed rate is the counterfactual estimate.
3. Headline = `rate_t − rate_c` in pp, with 95% percentile-bootstrap CI
   (2,000 resamples, seeded).
4. Incremental money = lift × treated count × mean amount.
5. Cost honesty: total contact spend (per-channel unit costs), cost per
   incremental recovery, redundant-contact share (control-rate ÷ treatment-rate),
   opt-outs caused.

## Simulation engine

Discrete-event loop (`simulate/engine.py`) with a monotonic-counter heap:
`case_ingest` → classify + latent clock + first plan; `action_due` → execute
through the real policy gate and executor; outcomes sampled from the world
model (channel effectiveness × fatigue × salary alignment × quiet-hour penalty);
`organic` fires independent of interventions; `sweep` writes off expired cases
every 6h. The exact same `policy.evaluate` and `execute_action` run in
simulation and in the live FastAPI path — the simulator tests the real system.

## Deliberate simplifications

- SQLite + single process by default: batch-harness right-sized. `DATABASE_URL` selects the
  Postgres backend (`app/pg_store.py`), whose audit chain serializes on
  `pg_advisory_xact_lock` + `chain_head` — linear across concurrent writers (ADR-006).
- Payment-link completion assumed via `payment_link.paid`; subscription
  auto-collect modelled as RETRY_CHARGE (world decides success).
- No retries-on-retry (webhook delivery is assumed reliable); idempotency keys
  would come from paytm's API in production.
