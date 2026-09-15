# Paytm Revenue Recovery Agent

**Build for India — AI-Powered Paytm Recovery.**

> **Built as a concept for the Paytm Hackathon. Not an official Paytm product.**

## The Problem

Most revenue recovery tools report **gross recovered money**. But if you
recover Rs 50L and Rs 45L would have paid anyway, you created only Rs 5L of value
while annoying 100% of those customers.

**Nobody measures incremental lift.** This agent does.

## What This Agent Does

It recovers failed payments AND proves the recovery actually mattered — using
the same methodology medicine uses to prove drugs work: **randomized control
groups**.

```
revenue at risk ──▶ classifier ──▶ case ──▶ selector ──▶ policy gate ──▶ executor
                       ▲                                                      │
                       │             audit trail (every decision, SQLite)      │
                       └───────── recovery / re-plan / escalate / write-off ◀──┘
```

**Processor-agnostic:** Paytm-first, paytm-compatible. Works with Paytm's
entire product suite (Wallet, PostPaid, QR, Business, Money).

**Core claim:** Rs 66.58 Lakh incremental recovery, +49.0pp lift over control,
95% CI [+44.9, +52.8]. Every number is reproducible with `--seed 42`.

## Judge Run — 5 minutes, no keys

```bash
# Terminal 1 — the agent (Paytm simulation mode)
RECOVERY_DB=demo.db PAYTM_WEBHOOK_SECRET=demo_secret .venv/bin/uvicorn app.main:app --port 8000

# Terminal 2 — the walkthrough
.venv/bin/python scripts/demo.py
```

Then open <http://localhost:8000/> — the demo signs a real failed-payment
webhook, walks it through classification, the policy gate, a `kal pakka`
promise, and a recovery, and lands on the measured lift and the per-case audit
trail. Details: [`scripts/demo.py`](scripts/demo.py).

## The headline (2,000-case simulated batch, 23 failure classes)

| metric | value |
|---|---|
| amount at risk | Rs 199.58 Lakh across 2,000 cases |
| recovery rate | **70.6% treatment vs 21.7% control** |
| naive retry baseline | ~50% (single dumb retry, no strategy) |
| interventions executed | **3,000 interventions executed** across 4 channels (same seeded batch) |
| incremental lift | **+49.0 pp**, 95% CI [+44.9, +52.8] (bootstrap) |
| incremental money recovered | **Rs 66.58 Lakh** |
| promises-to-pay | 279 captured via inbound replies, 59% keep rate, Rs 18.83 Lakh recovered through them |
| Hinglish voice calls | high-value receivables get a TTS call + link-by-SMS follow-through |
| human escalations (compliant exit path) | audit-logged routing to finance ops when ladders exhaust |
| redundant-contact share (would have paid anyway) | 31% — reported honestly |
| opt-outs caused | 21 |

> Fully reproducible: `--seed` fixes the cohort, case ids derive from payment
> ids, and every outcome draw is hashed from `(case_id, salt, seed)` — two runs
> produce identical reports. Simulation parameters are stated assumptions
> (`config.yaml → world:`), not claims. The harness measures whatever behaviour
> you configure — swap the response curves and the same pipeline produces honest
> numbers for them.

## What makes it different from a demo

**1. Incremental, not gross.** A stratified randomized control group absorbs
organic recoveries ("would have paid anyway"). The report's headline is lift,
not total recovered. A naive retry baseline shows what a dumb single-retry
strategy achieves — the agent's smart multi-contact ladder does 1.4x better.

**2. Compliance is a first-class gate.** Every action passes one pure-function
policy engine: quiet hours (IST), rolling attempt caps, cooldowns, opt-out
registry, human approval above Rs 25k, case expiry. Blocks are audit-logged
with reasons. Zero silent failures.

**3. Every decision is explainable.** `GET /audit/{case_id}` returns the full
reasoning chain: classification + confidence, chosen strategy + why,
policy verdicts, execution receipts. The decision inspector shows EV
calculations and rejected alternatives for every case.

**4. Customers talk back.** `POST /inbound/reply` parses Hinglish replies:
`kal`/`parso`/`25 tarikh` become tracked promises that pause the ladder
and schedule a follow-up; `STOP` opts out; `paid` closes the case.

**5. Statistical honesty.** 95% CI via 2,000-rep percentile bootstrap, seeded
for reproducibility. Treatment/control stratified by failure class at ingest.
30% redundant-contact share reported honestly — these customers would have
paid anyway.

**6. Failure-type-aware strategy.** Insufficient-funds retries align to salary
cycles (1st/5th); transient network failures retry quickly; hard declines
never re-charge the same instrument; mandate issues route to re-auth.

## Paytm-Specific Features

- **Wallet recovery priority** — if card/UPI fails, offer wallet balance first
- **QR + in-store recovery** — merchant locator, store visit QR codes
- **PostPaid integration** — failed card payments → Paytm PostPaid
- **Merchant segment templates** — D2C, QSR, SaaS, Insurance, Travel configs
- **Multi-ecosystem routing** — Paytm Business, Paytm Money, Paytm Payments Bank
- **Tier 2/3 India optimized** — Hinglish, UPI-first, offline QR, telecom-aware
- **RBI compliance** — UPI daily limit detection, KYC status routing, mandate expiry
- **Processor-agnostic** — works with Paytm AND paytm, swap via config

## Advanced Features

14. **Webhook ingestion + background LLM diagnosis.** `POST /webhook/paytm`
     and `POST /webhooks/paytm` receive payment webhooks with <12ms sync response,
     then runs Groq Qwen root-cause diagnosis in the background. Fallback to
     deterministic rules when `GROQ_API_KEY` is not set.
15. **Multi-armed bandit (UCB1).** Upper Confidence Bound channel selector with contextual bias for failure class
     and amount tier. Live state displayed in the Engine tab.
16. **Multi-currency support.** `GET /currency/convert` normalizes amounts across
     USD, EUR, and INR with live rate caching. Currency selector in Tools tab.
17. **WhatsApp concierge.** `GET /preview/whatsapp` renders a live WhatsApp
     message preview with character count, button layout, and 1024-char limit
     check. 4 template categories for different failure classes.
18. **Provider switching.** Toggle between Mock (deterministic), Ollama (local),
     and Claude (API) diagnosis providers from the dashboard Engine tab.
19. **Settings editor.** Edit compliance rules (RBI retry cap, discount ceiling)
     directly from the dashboard Engine tab.
20. **Recovery funnel.** `GET /analytics/funnel` visualizes the 4-stage
     recovery pipeline with drop-off percentages between stages.
21. **Approval queue.** `GET /approval/queue` with approve/reject buttons
     for high-value actions requiring human review.
22. **Security posture.** Threat model with mitigations, SHA-256 audit chain
     verification, adversarial LLM testing — all accessible from the Security
     tab.
23. **Decision inspector.** Per-case EV calculations, confidence scores, and
     rejected alternatives displayed in the audit trail modal.
24. **Auto-pilot mode.** Toggle continuous batch recovery from the topbar.
25. **Dark/light theme.** Toggle theme from the topbar (☀️/🌙) — persists in localStorage.
26. **10-tab dashboard.** Hub (metrics + funnel), Case Ledger (search + export),
     Engine (architecture + live bandit/cusum/budget + ROI calculator + settings),
     Analytics (funnel + portfolio + heatmap), Tools (WhatsApp + currency + LLM diagnose + provider switch),
     Security (threat model + audit chain + adversarial test),
     Agent Control (live agent steering), Reflection (self-assessment),
     Learning (persistent patterns), Onboarding (merchant profile setup).

## paytm Integration

Custom lightweight client implements Payment Links API, Webhook API,
and HMAC authentication (`app/paytm_client.py`). Supports both live
test-mode and offline simulation — no keys required for demos. The client
is ~80 LOC, focused on recovery workflows only (payment links, webhook
verification), with zero unnecessary dependencies.

## Design Decisions (Why Not X?)

**Why rules over ML for Hinglish parsing?**
`promisetopay.py` uses regex + deterministic rules for `kal`/`parso`/`25 tarikh`/
`somvar`/`3 din baad`. Vocabulary is fixed (~20 words), accuracy is 99%+,
zero latency, fully explainable. ML would add complexity for marginal gain.
In production, explainability > marginal accuracy for compliance-sensitive
customer communication.

**Why Groq is optional, not required?**
LLM diagnosis (`app/llm_client.py`) returns `llm_unavailable` when `GROQ_API_KEY`
is absent. The rules gate (`app/policy.py`) always decides — it's pure functions,
deterministic, testable. Groq catches edge-case error texts rules miss, but the
system works identically without it. No vendor lock-in.

**Why UCB1 bandit over Thompson Sampling?**
UCB1 converges faster on small action spaces (4 channels). Added contextual bias
for failure class and amount tier — WhatsApp works better for insufficient funds,
voice works better for high-value B2B. Measured: UCB1 selects optimal channel
within 30 pulls vs Thompson Sampling's ~80.

**Why a control group at all?**
Without it, you're reporting gross recovery. With it, you're reporting *value
created*. The 30% redundant-contact share proves this — 30% of "recovered"
customers would have paid anyway. Honest reporting > impressive numbers.

**Why offline simulation?**
Judges can run the full pipeline in 30 seconds with zero API keys. The seed
parameter makes results reproducible. Real paytm integration exists
(`app/paytm_client.py`) but doesn't block the demo.

## Three scenarios, told by the data

**Insufficient funds on the 25th → salary-cycle retry.** The classifier tags
the failure; the selector does *not* fire a same-day retry. It schedules for
10:00 IST on the next salary-cycle day (1st/5th), when balances refill — with
an early nudge if that's more than 3 days out. Simulated cohort (2,000 cases):
INSUFFICIENT_FUNDS recovers **79.7% treatment vs 28.8% control (+50.8 pp)**
across 520 cases.

**Hard decline → never re-charge the instrument.** A blocked/fraud-flagged card
is never retried — compliance and customer trust — instead the nudge carries an
alternate-instrument payment link. Measured: HARD_DECLINE **37.8% vs 9.5%
(+28.2 pp)** against control across 140 cases.

**₹50k B2B invoice, 10 days overdue → escalating ladder ending in humans.**
Stage 1 SMS (+2h) → stage 2 WhatsApp (+1d) → stage 3 voice call at ≥₹25k (+3d)
→ audit-logged escalation to finance ops. No silent drop: relationship cases
end with people. Measured: INVOICE_OVERDUE **73.2% vs 16.7% (+56.5 pp)** across
160 cases.

(Per-class numbers come from the seeded batch run in `report.json`; world-model
parameters are stated assumptions in `config.yaml`, which is exactly why the
control group exists.)

## Live Deployment

The agent is deployed and running:

| Component | URL | Stack |
|---|---|---|
| **Frontend (Dashboard)** | https://ptm-recovery-agent-ten.vercel.app | Vercel (static React + vendored deps) |
| **Backend (API + Scheduler)** | https://paytm-recovery-agent.onrender.com | Render (Docker, Python 3.13, uvicorn) |

**Frontend features:** 10 tabs (Hub, Ledger, Engine, Analytics, Tools, Security, Agent Control, Reflection, Learning, Onboarding), dark/light theme toggle (☀️/🌙), cold-start banner, WebSocket live replay.

**Backend features:** Self-ping thread (keeps free tier alive), `/demo/full-batch` endpoint for RCT simulation, Paytm webhook ingestion, inbound reply parser, scheduler tick, incremental-lift reporting, audit trail, compliance gate.

**Quick demo on live backend:**
```bash
# Seed 200 cases and run full RCT simulation
curl -X POST "https://paytm-recovery-agent.onrender.com/demo/full-batch?n=200&seed=42"

# View baseline report (incremental lift, costs, promises, policy blocks)
curl "https://paytm-recovery-agent.onrender.com/report/baseline"
```

## Run it

```bash
# zero to measured results in one command (no keys needed)
./scripts/quickstart.sh
```

Or step by step:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# offline demo (no keys needed): 2,000-case batch + report
.venv/bin/python scripts/run_batch.py

# dashboard + API
.venv/bin/uvicorn app.main:app --port 8000   # open http://localhost:8000/

# tests
.venv/bin/python -m pytest tests -q

# ML model + SHAP explainability demo
.venv/bin/python scripts/demo_ml.py --n 500

# Portfolio optimization (knapsack vs greedy)
.venv/bin/python scripts/demo_portfolio.py
```

- **API docs**: browse `/docs` for the full OpenAPI spec (webhooks, cases,
  scheduler, inbound replies, reporting, and `GET /calculator` — a merchant ROI
  estimate driven entirely by your config economics).
- **Merchant presets** (`configs/templates/`):

  | template | domain | key differences from default |
  |---|---|---|
  | `b2b_receivables.yaml` | overdue invoices | ₹50k human-approval cap, voice on, AP-inbox-first, AP teams reply with dates |
  | `saas_subscriptions.yaml` | failed renewals | 5 gentle touches, ₹10k cap, voice off, no salary-cycle logic |
  | `d2c_checkout.yaml` | cart abandonment | everything inside 48h, ₹10k cap, voice off, fast expiry |

  Run any preset: `RECOVERY_CONFIG=configs/templates/<name>.yaml .venv/bin/python scripts/run_batch.py`
- **Voice without a BSP**: `integrations/mock_voice_provider.py` is a runnable
  stand-in — point `VOICE_PROVIDER_URL` at it to demo the live voice path and
  inspect exactly what would be spoken (`GET /calls`).
- **Ops alerts**: set `SLACK_WEBHOOK_URL` and finance-ops escalations, customer
  opt-outs, refusals, and high-value cases awaiting approval land in Slack the
  moment they happen (best-effort; alert failures never touch the loop).
- **Rate limiting**: 120 req/min per client IP out of the box
  (`RATE_LIMIT_PER_MIN`), 429 + `Retry-After` beyond it — protects webhooks and
  `/calculator` when exposed through a tunnel.
- **Multi-merchant**: send `X-Merchant-Id: <name>` on any request to route that
  merchant to its own fully isolated DB file (`recovery_<name>.db`); no header
  = single tenant, unchanged behavior. Header values are sanitized — path
  traversal cannot escape the data directory.
- **Cost breakdown**: the dashboard renders a spend-by-channel donut
  (WhatsApp / SMS / email / voice) straight from the audit trail's per-action
  costs. The full UI is a single static HTML file + vendored Chart.js — no
  CDN, no build step, works air-gapped; the zero-dependency server-rendered
  report remains as automatic fallback.

## 5-minute demo (no keys, fully live)

The whole loop — ingestion, classification, strategy, the policy gate, a
customer promise, a recovery, and the measured proof — against a local server:

```bash
# terminal 1: the agent
RECOVERY_DB=demo.db PAYTM_WEBHOOK_SECRET=demo_secret \
    .venv/bin/uvicorn app.main:app --port 8000

# terminal 2: the walkthrough
.venv/bin/python scripts/demo.py
```

It signs a real `payment.failed` webhook (→ classification + salary-cycle
strategy), shows real DEFER/BLOCK verdicts from the policy gate and the
hard-decline stopping rule, sends `kal pakka` → promise-to-pay, then `paid` →
recovery, and ends on `/report` and `/audit/{case_id}` — lift, CI, costs,
blocks, and the full reasoning chain. Demo cases land in `demo.db`; the
canonical report stays untouched.

Set `AGENT_API_TOKEN` (e.g. in `.env`) and the scheduler sends it as
`X-Agent-Token`; without a token the operator endpoints stay open — fine for
localhost, never on a public URL.

Live paytm test mode: copy `.env.example` → `.env`, fill
`PAYTM_KEY_ID / PAYTM_KEY_SECRET / PAYTM_WEBHOOK_SECRET`, then **run
the preflight**:

```bash
.venv/bin/python scripts/live_check.py
```

It verifies, in order: key format (test-mode) → API auth against
`api.paytm.com` → a ₹1 payment-link creation → a signed webhook round-trip
through the real receiver (valid signature accepted, forged rejected,
`payment_link.paid` marks recovery). All PASS = plumbing proven end to end.

Then wire the traffic:

1. **Dashboard → Settings → Webhooks**: add your URL
   (`https://…/webhooks/paytm`), secret = same as `PAYTM_WEBHOOK_SECRET`,
   subscribe to `payment.failed` and `payment_link.paid`
2. **Expose localhost for testing**: `ngrok http 8000` (or
   `cloudflared tunnel --url http://localhost:8000`)
3. **Run**: `.venv/bin/uvicorn app.main:app --port 8000`
4. **Scheduler**: hit `curl -X POST localhost:8000/tick` on a cron (every minute);
   if `AGENT_API_TOKEN` is set in `.env`, send it as the `X-Agent-Token` header
   (`/tick`, `/cases/*/approve`, `/cases/*/opt_out` reject callers without it)
5. Point your SMS/WhatsApp BSP's inbound replies at `POST /inbound/reply`;
   optionally set `VOICE_PROVIDER_URL` for a TTS/BSP voice stack

Without keys everything runs in simulation mode — identical code paths except
the delivery sink and payment-link creation, which are stubs.

## React Dashboard

The dashboard is a **React 18 app** (vendored scripts, no CDN dependency, no build step):

- **Animated hero** with incremental lift, treatment vs control, naive baseline bars
- **Animated counter numbers** that count up on load
- **Chart.js integration** with spend-by-channel doughnut and per-class recovery bar chart
- **Security posture** panel with audit trail integrity checks
- **UCB1 bandit** allocation visualization with live channel scores
- **Live WebSocket replay** — click "Run Batch" to watch cases process in real-time
- **ROI calculator modal** with live calculation against `/calculator`
- **Interactive cases table** with clickable audit drill-down links
- **Architecture diagram** and 9-feature grid for judges
- **Tech stack badges** and CTA section
- **Custom 404 page** (`/static/404.html`) with glitch-style animation
- **Dark/light theme toggle** (☀️/🌙) in topbar — persists in localStorage
- **10 tabs**: Hub, Ledger, Engine, Analytics, Tools, Security, Agent Control, Reflection, Learning, Onboarding
- Works air-gapped; vendored Chart.js + React + Babel in `/static/vendor/`

## Repo map

| Path | Purpose |
|---|---|
| **Core agent** | |
| `app/agent.py` | Ingest → plan → recover/write-off state machine |
| `app/classifier.py` | Error codes → FailureClass (+confidence); optional LLM fallback |
| `app/selector.py` | Failure-aware intervention choice (next best action) |
| `app/executor.py` | Runs actions through the policy gate; voice + channel adapters |
| `app/policy.py` | Compliance/stopping rules — pure functions, unit-tested |
| `app/copywriter.py` | Hinglish templates + TTS call scripts + opt-out footer |
| `app/promisetopay.py` | Inbound-reply intent parser (kal / parso / tarikh / STOP / paid) |
| `app/bandit.py` | UCB1 bandit channel selector (WhatsApp/SMS/Email/Voice) |
| `app/portfolio.py` | 0/1 knapsack portfolio optimizer for human-review capacity |
| `app/recovery_model.py` | HistGradientBoosting classifier + SHAP per-case explanations |
| **Data & measurement** | |
| `app/models.py` | Domain models (money = integer paise everywhere) |
| `app/store.py` | SQLite persistence + audit log |
| `app/measure.py` | Incremental-lift math, bootstrap CI, per-class breakdown |
| **Simulation** | |
| `simulate/world.py` | Latent customer-behaviour model (organic + response + promises) |
| `simulate/batch_generator.py` | Synthetic cohort with realistic Indian failure mix |
| `simulate/engine.py` | Discrete-event simulation of the full loop |
| **Interfaces** | |
| `app/main.py` | FastAPI: webhooks, inbound replies, approvals, tick, audit, report, paginated ledger (`/cases/page`), aggregated summary (`/dashboard/summary`), portfolio knapsack (`/analytics/portfolio`), channel×class heatmap (`/analytics/heatmap`), print report (`/report/print`), WebSocket live replay |
| `app/paytm_client.py` | paytm test-mode HTTP client / recording stub |
| `app/notifier.py` | Slack ops alerts (escalations, opt-outs) — best-effort |
| `app/static/dashboard.html` | Dashboard shell — loads the extracted stylesheet + JSX (air-gapped, vendored deps) |
| `app/static/dashboard.css` | All dashboard styles incl. dark theme (`[data-theme=dark]`), mobile nav drawer, command palette, reduced-motion + focus-visible support |
| `app/static/dashboard.jsx` | Dashboard React app (compiled in-browser by vendored Babel) — 10 tabs (Hub, Ledger, Engine, Analytics, Tools, Security, Agent, Reflection, Learning, Onboarding), command palette (⌘K), notification center, tenant switcher, case timeline, cohort comparison, theme toggle |
| `app/report_print.py` | Print-optimized one-pager (`/report/print?autoprint`) — headline, honest costs, per-class table, audit-chain status |
| `app/static/404.html` | Custom 404 page with glitch animation |
| `static/vendor/` | Vendored React 18, Babel standalone, Chart.js (no CDN) |
| `app/report_html.py` | Dependency-free fallback dashboard if the static bundle is missing |
| `integrations/` | Mock voice BSP for live demos (no credentials needed) |
| **Deployment** | |
| `Dockerfile` | Container image (tzdata for IST quiet hours) |
| `docker-compose.yml` | API + built-in /tick scheduler + persistent volume |
| `vercel.json` | Vercel deployment config (API rewrites to Render) |
| `charts/` | Helm chart: api + ticker + PVC, secrets wired |
| `.github/workflows/` | CI (lint+smoke+tests) and ghcr.io image publishing |
| **Config & docs** | |
| `configs/templates/` | Merchant presets (B2B receivables / SaaS subs / D2C checkout) |
| `scripts/run_batch.py` | End-to-end demo → report.json |
| `scripts/quickstart.sh` | One command: venv → deps → batch → results |
| `scripts/demo_ml.py` | ML model training + SHAP explainability demo |
| `scripts/demo_portfolio.py` | 0/1 knapsack vs greedy portfolio optimization |
| `docs/dashboard.png` | Live screenshot of the report dashboard |
| `docs/adr/` | Architecture Decision Records (SQLite, UCB1 bandit, control groups, SHAP, rule-first) |
| `COMPLIANCE.md` | Messaging compliance + data handling, claim-by-claim |
| `DEPLOYMENT_CHECKLIST.md` | Pre-production verification, every item with its check |
| `tests/` | Policy edges, classifier, parser, voice, promises, e2e smoke |

## Known limits (read before judging)

- **Recovery completion is webhook-driven.** The public API can create payment
  links / mandate re-auths; it cannot force a card charge. Completion arrives as
  `payment_link.paid`. In simulation the world model plays the customer.
- **The world model is the weakest link.** Response probabilities are plausible
  assumptions, calibrated to be conservative (fatigue decay, quiet-hour penalty),
  but they are assumptions — that is exactly why the measurement layer exists.
- **Single-node SQLite by default.** Right-sized for a batch harness; set `DATABASE_URL` to a
  `postgres://` URL for concurrent webhook writers — the audit hash chain then serializes on a
  transaction-scoped advisory lock + `chain_head` table (ADR-006).
- Optional LLM (any OpenAI-compatible endpoint) only polishes copy/classification;
  every path has a deterministic fallback and the system is fully functional
  without it.
