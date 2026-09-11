# Compliance & Data Handling

What this system actually does — every claim below maps to code you can read.

## Messaging compliance (India)

| Practice | Where enforced |
|---|---|
| Recovery nudges are **transactional**, not promotional (TRAI DND exempts transactional service messages; the agent never sends marketing) | `app/copywriter.py` — templates are strictly payment-recovery copy |
| Every message carries a **STOP opt-out footer** (spoken on voice calls too) | `FOOTER` in `copywriter.py`; voice script suffix |
| **STOP / unsubscribe / "band karo"** replies opt the customer out globally, immediately | `promisetopay.py` → `Intent.OPT_OUT` → case written off, customer flagged; `policy.py` BLOCKs any further action for an opted-out customer |
| **Quiet hours**: no customer contact 22:00–08:00 IST; actions due inside the window defer to window open | `policy.py` (`quiet_hours_ist`), unit-tested in `tests/test_policy.py` |
| **Frequency caps**: max 3 interventions per rolling 72h + 4h cooldown between touches — stricter than typical DLT norms require | `policy.py` (`max_attempts_per_case`, `cooldown_minutes`) |
| **High-value guardrail**: automated money actions above ₹25k (configurable) DEFER until a human approves; contacts/escalation are unaffected so receivables never strand | `policy.py` (`auto_action_cap_paise`, `approved_human`), `POST /cases/{id}/approve` |
| **Hard declines are never re-charged** — blocked/fraud-flagged instruments get an alternate-instrument link instead of retries | `selector.py` HARD_DECLINE strategy |
| **Automation always terminates in a human**, never a silent drop: exhausted ladders route to finance ops as audit-logged escalations | `selector.py` ESCALATE_HUMAN → `engine.py` writes off with reason `escalated_to_human_finance_ops` |

## Audit trail

Every state transition is appended to an audit table *before* it happens:
classification (+confidence), treatment/control assignment, selected strategy
(+why), policy verdicts (execute/defer/block with reasons), delivery receipts,
promise lifecycle, human approvals. Retrieve the full chain per case via
`GET /audit/{case_id}`. The dashboard links each recent case to its trail.

## Data handling

- **No card data ever enters this system.** paytm tokenizes instruments;
  the agent sees only payment metadata (ids, amount, error codes) and the
  contact fields your own webhook notes supply (name/phone/email).
- **Storage** is one local SQLite file (`RECOVERY_DB`). Nothing is sent to
  third parties by default.
- **Retention is operator-managed**: cases persist until you delete them
  (delete the DB file or add a purge job). There is *no automatic deletion*
  today — if your policy requires retention windows, that is a deliberate
  extension point, not something to assume exists.
- **Customer replies** are stored with intent labels for dispute resolution.
- **Optional LLM polish** (`OPENAI_API_KEY`): if enabled, failure text and nudge
  copy go to your configured OpenAI-compatible endpoint for classification/copy
  only — never amounts beyond what the prompt needs, and the system is fully
  functional with it off (deterministic fallbacks everywhere).

## DPDP Act 2023 mapping

Engineering documentation of how the architecture maps to the Act's roles —
not legal advice; your DPIA and counsel decide the rest.

- **Roles**: the merchant is the Data Fiduciary (they set the purpose —
  completing a payment the customer already initiated — and supply the contact
  data via their own webhook notes). This agent acts as a **Data Processor**:
  it processes only what the fiduciary's systems push it, only for that stated
  purpose, and sends only transactional recovery messages related to it.
- **Purpose limitation**: contact fields are used exclusively for recovering
  the associated failed payment; nothing is repurposed, profiled for marketing,
  or shared across merchants (multi-tenant mode gives each merchant a separate
  database file).
- **Security safeguards (s.8)**: mutating endpoints require a shared-secret
  token (`AGENT_API_TOKEN`), inbound webhooks are HMAC-verified, storage is a
  local single file with no network exposure by default.
- **Erasure**: the fiduciary owns retention decisions. The agent exposes the
  data plainly (one SQLite file) so a purge job is trivial; automated erasure
  windows are a deliberate extension point, documented as absent rather than
  assumed.

## RBI recovery-guideline boundary

India's RBI fair-practices code for recovery agents governs *loan* collections;
a failed checkout payment is not credit, so those rules do not directly bind
this system. We adopt their practices anyway where they cost nothing: messages
identify the purpose, cadence is capped well below intrusive levels, quiet
hours are honored, opt-outs end contact immediately, and any contested case
(`dispute`, `galat`, refusal) leaves automation with an audit trail and routes
to humans.

## Measurement ethics

Treatment/control assignment is randomized at ingest within each failure class.
Control customers receive **no outreach**; their organic recovery rate is the
counterfactual baseline. The report states redundant-contact share and opt-outs
caused next to the headline lift — the cost of outreach is part of the result,
not hidden behind it.
