# Onboarding: Paytm Revenue Recovery Agent

## What Is This?

An **AI-powered revenue recovery agent** built for the Paytm ecosystem. It recovers failed payments and — critically — **measures incremental lift** using randomized control groups, the same methodology medicine uses to prove drugs work.

**Built for the Paytm Hackathon. Not an official Paytm product.**

---

## The Problem It Solves

### The Industry Lie
Most recovery tools report **gross recovered money**. If you recover ₹50L but ₹45L would have paid anyway, you created only ₹5L of value while annoying 100% of those customers.

**Nobody measures incremental lift.** This agent does.

### The Core Insight
- **Treatment group** gets the full recovery ladder (WhatsApp → SMS → Email → Voice → Human escalation)
- **Control group** gets nothing — only organic recovery (customers who pay on their own)
- **Lift = Treatment recovery rate − Control recovery rate**
- **Incremental money = Lift × Treated count × Mean amount**

This is the only honest way to report recovery performance.

---

## How It Works: The Loop

```
revenue at risk ──▶ classifier ──▶ case ──▶ selector ──▶ policy gate ──▶ executor
                       ▲                                                      │
                       │             audit trail (every decision, SQLite)      │
                       └───────── recovery / re-plan / escalate / write-off ◀──┘
```

### Step by Step

| Step | Component | What It Does |
|------|-----------|--------------|
| 1 | **Ingest** (`agent.py`) | Receives `payment.failed` webhook, normalizes to `FailedPayment` |
| 2 | **Classify** (`classifier.py`) | Maps error code → 1 of 15 `FailureClass` with confidence; optional LLM fallback |
| 3 | **Group Assignment** | Stratified random: ~70% treatment / 30% control by failure class |
| 4 | **Select** (`selector.py`) | Picks next best action for *this* failure class + amount + history |
| 5 | **Policy Gate** (`policy.py`) | Pure-function compliance check → EXECUTE / DEFER / BLOCK |
| 6 | **Execute** (`executor.py`) | Sends via channel adapter (WhatsApp/SMS/Email/Voice/Payment Link) |
| 7 | **Measure** (`measure.py`) | Bootstrap CI, per-class breakdown, cost honesty, redundant-contact share |
| 8 | **Re-plan** | On every outcome (reply, recovery, silence), loop repeats from step 4 |

---

## What Makes It Different

### 1. Incremental, Not Gross
A stratified randomized control group absorbs organic recoveries. The headline is **lift**, not total recovered. A naive single-retry baseline shows what dumb retries achieve — the agent's smart ladder does 1.4x better.

### 2. Compliance Is a First-Class Gate
Every action passes through `policy.py` — pure functions, zero side effects:
- Quiet hours (IST 22:00–08:00)
- Rolling attempt caps (3 in 72h)
- Cooldowns (4h between contacts)
- Opt-out registry (global per-customer)
- Human approval above ₹25k
- Case expiry (14 days)

Blocks are audit-logged with reasons. Zero silent failures.

### 3. Every Decision Is Explainable
`GET /audit/{case_id}` returns the full reasoning chain:
- Classification + confidence
- Chosen strategy + why
- Policy verdicts
- Execution receipts
- EV calculations and rejected alternatives

### 4. Customers Talk Back
`POST /inbound/reply` parses Hinglish replies deterministically (regex, no LLM):
- `kal` / `parso` / `25 tarikh` / `somvar` / `3 din baad` → tracked promises
- `STOP` → global opt-out
- `paid` → case closes

Promises pause the ladder, schedule a follow-up check, and resume if broken.

### 5. Statistical Honesty
- 95% CI via 2,000-rep percentile bootstrap, seeded for reproducibility
- Treatment/control stratified by failure class at ingest
- 30% redundant-contact share reported honestly

### 6. Failure-Type-Aware Strategy
| Failure Class | Strategy |
|---------------|----------|
| `INSUFFICIENT_FUNDS` | Salary-cycle retry (1st/5th, 10am IST) |
| `NETWORK_TIMEOUT` | Retry after 20 min |
| `ISSUER_UNAVAILABLE` | Retry after 45 min |
| `HARD_DECLINE` | Never re-charge; alternate-instrument link |
| `MANDATE_ISSUE` | Re-auth nudge before any charge |
| `CUSTOMER_ABANDONMENT` | Reminder ladder +1h/+24h/+3d |
| `INVOICE_OVERDUE` | Dunning ladder +2h/+1d/+3d → voice (≥₹25k) → human |
| `SUBSCRIPTION_FAILED` | Grace 6h → re-auth 24h → pause/downgrade offer 2d |

---

## Paytm-Specific Features

- **Wallet recovery priority** — card/UPI fails → offer wallet balance first
- **QR + in-store recovery** — merchant locator, store visit QR codes
- **PostPaid integration** — failed card → Paytm PostPaid
- **Merchant segment templates** — D2C, QSR, SaaS, Insurance, Travel configs
- **Multi-ecosystem routing** — Paytm Business, Paytm Money, Paytm Payments Bank
- **Tier 2/3 India optimized** — Hinglish, UPI-first, offline QR, telecom-aware
- **RBI compliance** — UPI daily limit detection, KYC status routing, mandate expiry
- **Processor-agnostic** — works with Paytm AND paytm, swap via config

---

## Architecture at a Glance

### Core Modules (`app/`)
| File | Responsibility |
|------|----------------|
| `agent.py` | State machine: ingest → plan → recover/write-off |
| `classifier.py` | Error code → FailureClass + confidence; LLM fallback |
| `selector.py` | Failure-aware next-best-action choice |
| `executor.py` | Runs actions through policy gate; channel adapters |
| `policy.py` | Compliance/stopping rules — pure functions, unit-tested |
| `copywriter.py` | Hinglish templates + TTS scripts + opt-out footer |
| `promisetopay.py` | Inbound-reply intent parser (kal/parso/tarikh/STOP/paid) |
| `bandit.py` | UCB1 multi-armed bandit for channel selection |
| `portfolio.py` | 0/1 knapsack optimizer for human-review capacity |
| `recovery_model.py` | HistGradientBoosting + SHAP per-case explanations |
| `models.py` | Domain models (money = integer paise everywhere) |
| `store.py` | SQLite persistence + append-only audit log |
| `measure.py` | Incremental-lift math, bootstrap CI, per-class breakdown |
| `main.py` | FastAPI: webhooks, inbound, approvals, tick, audit, report, dashboard |

### Simulation (`simulate/`)
| File | Responsibility |
|------|----------------|
| `world.py` | Latent customer-behaviour model (organic + response + promises) |
| `batch_generator.py` | Synthetic cohort with realistic Indian failure mix |
| `engine.py` | Discrete-event simulation of the full loop |

### Config-Driven Merchant Segments (`configs/templates/`)
| Template | Domain | Key Differences |
|----------|--------|-----------------|
| `b2b_receivables.yaml` | Overdue invoices | ₹50k human-approval cap, voice on, AP-inbox-first |
| `saas_subscriptions.yaml` | Failed renewals | 5 gentle touches, ₹10k cap, voice off, no salary-cycle |
| `d2c_checkout.yaml` | Cart abandonment | Everything inside 48h, ₹10k cap, voice off |
| `insurance_lending.yaml` | Insurance/lending | 2 retries, compliance-first, SMS+Voice |
| `travel_flights.yaml` | Travel/flights | Time-critical, WhatsApp+Voice, ₹10L escalation |

---

## How to Run It

### Zero to Measured Results (No Keys Needed)
```bash
./scripts/quickstart.sh
```

### Step by Step
```bash
# Setup
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Offline demo: 2,000-case batch + report.json
.venv/bin/python scripts/run_batch.py

# Dashboard + API
.venv/bin/uvicorn app.main:app --port 8000   # open http://localhost:8000/

# Tests
.venv/bin/python -m pytest tests -q

# ML model + SHAP explainability demo
.venv/bin/python scripts/demo_ml.py --n 500

# Portfolio optimization (knapsack vs greedy)
.venv/bin/python scripts/demo_portfolio.py
```

### 5-Minute Live Demo (No Keys)
```bash
# Terminal 1: the agent
RECOVERY_DB=demo.db PAYTM_WEBHOOK_SECRET=demo_secret \
    .venv/bin/uvicorn app.main:app --port 8000

# Terminal 2: the walkthrough
.venv/bin/python scripts/demo.py
```
Opens `http://localhost:8000/` — walks a real failed-payment webhook through classification, policy gate, `kal pakka` promise, recovery, and lands on measured lift + per-case audit trail.

---

## Key Concepts to Understand

### Money = Integer Paise
Everywhere. No floating-point bugs. `₹1.00 = 100 paise`.

### Case ID = Stable Hash of Payment ID
Webhook redelivery upserts instead of duplicating. Seeded simulation is reproducible because the world model salts draws with case ID.

### Audit Trail Is Append-Only
Every state transition emits an `AuditEvent` first. The audit trail is how `/report` can claim "every number computed from the trail."

### Control Group = The Counterfactual
Control cases get no interventions but DO have organic recovery clocks in the world model. Their observed rate = what would have happened anyway.

### UCB1 Bandit for Channel Selection
Upper Confidence Bound with contextual bias for failure class + amount tier. WhatsApp works better for insufficient funds; voice works better for high-value B2B. Converges in ~30 pulls vs Thompson Sampling's ~80.

### LLM Is Optional Polish
`GROQ_API_KEY` absent → LLM returns `llm_unavailable` → rules gate always decides. System works identically without it. No vendor lock-in.

---

## Design Decisions (Why Not X?)

### Why Rules Over ML for Hinglish Parsing?
`promisetopay.py` uses regex + deterministic rules for ~20 words. Accuracy 99%+, zero latency, fully explainable. ML adds complexity for marginal gain. In production, explainability > marginal accuracy for compliance-sensitive communication.

### Why Groq Optional, Not Required?
LLM diagnosis returns `llm_unavailable` when key absent. Rules gate (`policy.py`) always decides — pure functions, deterministic, testable. Groq catches edge-case error texts rules miss, but system works identically without it.

### Why UCB1 Over Thompson Sampling?
UCB1 converges faster on small action spaces (4 channels). Added contextual bias for failure class and amount tier. Measured: UCB1 selects optimal channel within 30 pulls vs Thompson's ~80.

### Why a Control Group at All?
Without it, you're reporting gross recovery. With it, you're reporting *value created*. The 30% redundant-contact share proves this — 30% of "recovered" customers would have paid anyway. Honest reporting > impressive numbers.

### Why Offline Simulation?
Judges run full pipeline in 30 seconds with zero API keys. Seed parameter makes results reproducible. Real Paytm integration exists (`app/paytm_client.py`) but doesn't block the demo.

---

## The Headline Numbers (2,000-Case Seeded Batch)

| Metric | Value |
|--------|-------|
| Amount at risk | ₹199.58 Lakh across 2,000 cases |
| Recovery rate | **70.6% treatment vs 21.7% control** |
| Naive retry baseline | ~50% (single dumb retry) |
| Interventions executed | **3,000** across 4 channels |
| Incremental lift | **+49.0 pp**, 95% CI [+44.9, +52.8] |
| Incremental money recovered | **₹66.58 Lakh** |
| Promises-to-pay | 279 captured, 59% keep rate, ₹18.83 Lakh via promises |
| Redundant-contact share | 31% (would have paid anyway) |
| Opt-outs caused | 21 |

> Fully reproducible: `--seed 42` fixes the cohort. Every outcome draw is hashed from `(case_id, salt, seed)`. Two runs produce identical reports.

---

## Per-Class Results (From Seeded Batch)

| Failure Class | Treatment | Control | Lift (pp) | Cases |
|---------------|-----------|---------|-----------|-------|
| INSUFFICIENT_FUNDS | 79.7% | 28.8% | +50.8 | 520 |
| INVOICE_OVERDUE | 73.2% | 16.7% | +56.5 | 160 |
| NETWORK_TIMEOUT | 82.1% | 41.2% | +40.9 | 180 |
| HARD_DECLINE | 37.8% | 9.5% | +28.2 | 140 |
| MANDATE_ISSUE | 68.4% | 22.1% | +46.3 | 110 |
| SUBSCRIPTION_FAILED | 65.3% | 18.9% | +46.4 | 150 |

---

## Live Deployments

| URL | Purpose |
|-----|---------|
| `paytm-recovery-agent.vercel.app` | React dashboard (API proxied via Vercel rewrites) |
| `paytm-recovery-agent.onrender.com` | FastAPI backend + API docs at `/docs` |

---

## Deployment

### Docker (All-in-One)
```bash
docker compose up --build    # dashboard on http://localhost:8000/
```

### Kubernetes (Helm)
```bash
helm install paytm charts/paytm-recovery-agent \
  --set image.repository=ghcr.io/OWNER/paytm-recovery-agent \
  --set agentToken=$(openssl rand -hex 16)
```
Deploys API + single-replica ticker (never scale — two tickers = double-contact), backed by PVC for SQLite. Set `DATABASE_URL` to Postgres before scaling replicas.

### Production Checklist
See `DEPLOYMENT_CHECKLIST.md` — every item with its verification command.

---

## Repository Map

```
├── app/                    # Core agent (FastAPI + logic)
│   ├── agent.py           # State machine
│   ├── classifier.py      # Failure classification
│   ├── selector.py        # Next-best-action
│   ├── executor.py        # Policy-gated execution
│   ├── policy.py          # Pure-function compliance gate
│   ├── copywriter.py      # Hinglish + TTS templates
│   ├── promisetopay.py    # Inbound reply parser
│   ├── bandit.py          # UCB1 channel selector
│   ├── portfolio.py       # Knapsack optimizer
│   ├── recovery_model.py  # ML + SHAP
│   ├── models.py          # Domain models (paise)
│   ├── store.py           # SQLite + audit log
│   ├── measure.py         # Lift math + bootstrap CI
│   ├── main.py            # FastAPI endpoints
│   ├── paytm_client.py    # Paytm test-mode client
│   ├── static/            # Dashboard (vendored React, no build)
│   └── ...
├── simulate/              # Simulation engine
│   ├── world.py           # Customer behaviour model
│   ├── batch_generator.py # Synthetic cohort
│   └── engine.py          # Discrete-event loop
├── configs/templates/     # Merchant segment presets
├── scripts/               # Runnable entrypoints
├── tests/                 # Policy edges, classifier, parser, e2e
├── charts/                # Helm chart
├── docs/adr/              # Architecture Decision Records
├── config.yaml            # Default configuration
└── requirements.txt
```

---

## Known Limits (Read Before Judging)

1. **Recovery completion is webhook-driven.** The API creates payment links / mandate re-auths; it cannot force a card charge. Completion arrives as `payment_link.paid`. In simulation, the world model plays the customer.

2. **The world model is the weakest link.** Response probabilities are plausible assumptions, calibrated to be conservative (fatigue decay, quiet-hour penalty) — that's exactly why the measurement layer exists.

3. **Single-node SQLite by default.** Right-sized for batch harness. Set `DATABASE_URL` to `postgres://` for concurrent webhook writers — audit chain then serializes on transaction-scoped advisory lock + `chain_head` table.

4. **Optional LLM only polishes copy/classification.** Every path has deterministic fallback. System fully functional without it.

---

## Where to Start Exploring

1. **Run the demo** — `./scripts/quickstart.sh` → read `report.json`
2. **Read the policy gate** — `app/policy.py` (pure functions, 100% test coverage)
3. **Trace a case** — `scripts/demo.py` shows the full loop
4. **Check the audit trail** — `GET /audit/{case_id}` on a real case
5. **Look at the measurement** — `app/measure.py` (bootstrap CI, cost honesty)
6. **Review ADRs** — `docs/adr/` for every major architectural decision

---

## Questions?

- **Architecture decisions** → `docs/adr/`
- **Compliance details** → `COMPLIANCE.md`
- **Deployment verification** → `DEPLOYMENT_CHECKLIST.md`
- **Claim verification** → `CLAIM_MATRIX.md` (every number traced to code)
- **Learning log** → `LEARNINGS.md` (what we tried, what failed, what worked)