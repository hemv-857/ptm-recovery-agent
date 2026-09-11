# Claim Matrix — Paytm Revenue Recovery Agent

Every public claim backed to a file, test, or run command. Headline numbers are
anchored to the seeded canonical batch (`report.json`, reproducible via
`scripts/run_batch.py --seed 42`).

| Claim | Evidence |
|-------|----------|
| **70.6% treatment vs 21.7% control recovery** (2,000-case seeded batch, 1,400T / 600C) | `report.json → headline` (0.7064 / 0.2167). Reproduce: `.venv/bin/python scripts/run_batch.py --seed 42` |
| **+49.0pp incremental lift, 95% CI [+44.9, +52.8]** | `report.json → headline.incremental_recovery_pp` (48.98, CI [44.86, 52.81]) computed by `app/measure.py:bootstrap_lift_ci()` — 2,000-rep percentile bootstrap, seeded |
| **₹66.58 Lakh incremental money recovered** | `report.json → headline.incremental_money_paise` (665,842,462 paise = ₹66.58 L); `app/measure.py:build_report()` — fixed: README, DEMO_STORYBOARD.md, LEARNINGS.md, and `/report/print` previously overstated this as "Cr" / "₹113 per recovery" by 100×; all now correctly say Lakh, and per-recovery cost comes from the same run (₹0.69) |
| **Naive single-retry baseline ≈ 50%** | `app/measure.py:naive_baseline()` — organic + first-contact lift per class from world-model config; value in `report.json → headline.naive_recovery_rate` (0.5003) |
| **3,000 interventions executed** | `report.json → cost.contacts_executed` (3,000) from the same seeded batch |
| **7-day simulation horizon** | `config.yaml → simulation.horizon_days: 7`; enforced by `simulate/engine.py` (sweep + horizon-end write-off) |
| **Deterministic time in tests** | No wall-clock reads in decision code — `now` is an explicit argument to `policy.evaluate` / `select_next_action`; tests pass fixed datetimes (e.g. `tests/test_policy.py`, `FIXED_NOW` in `app/adversarial.py`) |
| **12 compliance gates** | `app/policy.py:evaluate()` — case-closed, opt-out, money-action human cap, RBI e-mandate pre-debit notice, UPI daily limit, KYC block, wallet-topup route, duplicate-transaction block, attempt cap, cooldown, case expiry, quiet hours. Dedicated tests for the core gates in `tests/test_policy.py` |
| **Failure-aware selector** | `app/selector.py:select_next_action()` — distinct strategies for NETWORK_TIMEOUT, ISSUER_UNAVAILABLE, INSUFFICIENT_FUNDS, HARD_DECLINE, MANDATE_ISSUE, INVOICE_OVERDUE, SUBSCRIPTION_FAILED, LATE_AUTH + Paytm classes. Tested in `tests/test_classifier_selector.py` |
| **ML recovery probability** | `app/recovery_model.py` — HistGradientBoostingClassifier (max_iter=100, depth 4, seed 42), rule-based fallback when untrained or <20 samples. Trained per batch via `scripts/demo_ml.py`; tested in `tests/test_ml_explain_degradation.py` |
| **Explanation reasoning chain** | `app/explain.py:explain_decision()` — failure/amount context, fatigue, prediction, strategy. `test_explain_all_action_types` and `test_explain_all_failure_classes` cover every enum value (`tests/test_ml_explain_degradation.py`) |
| **Degradation detector** | `app/degradation.py:DegradationDetector` — failure rates by global/method/class scope, HEALTHY→WATCH→CONFIRMED. Tested in `tests/test_ml_explain_degradation.py` |
| **Economic stopping rule** | `app/policy.py:economic_stop()` — stops when `expected_recovery < 3x action_cost`. Tested in `tests/test_policy.py::test_economic_stop_*` |
| **Chart lifecycle in dashboard** | `app/static/dashboard.html` — React refs destroy each Chart.js instance before re-creating (`charts.current.*.destroy()`), preventing leaks on re-render. Dashboard smoke-tested in `tests/test_smoke.py` |
| **Input validation** | `POST /inbound/reply` truncates text (1000 chars) / phone (20 chars); `GET /calculator` rejects `amount <= 0` and lift outside 0..100 with 422; `POST /provider` validates the provider enum with 422 (`app/main.py`) |
| **142 tests passing** | `.venv/bin/python -m pytest tests -q` → `142 passed` (count moves as tests are added) |
| **Zero hardcoded keys** | `grep -r "sk_live\|sk_test\|key_live\|key_test" app/` → no matches. All secrets via env vars |
| **React dashboard (vendored, no CDN)** | `app/static/dashboard.html` — React 18.3.1 + Babel + Chart.js vendored in `static/vendor/`, works air-gapped. Served at `/` (falls back to `app/report_html.py` server-rendered dashboard if the bundle is missing) |
| **Custom 404 page** | `app/static/404.html` — animated glitch 404, served via FastAPI 404 exception handler in `app/main.py` |
| **Multi-seed evaluation** | `scripts/evaluate.py` — default 5 seeds (42, 49, 56, 63, 70) × 1,000 cases = 5,000 total, pooled bootstrap CI. Writes `evaluation_report.json` when run |
| **Sensitivity analysis** | `scripts/sensitivity.py` — ±20% sweep on world-model base_pay_probability. Writes `sensitivity_report.json` when run |
| **Held-out evaluation** | `scripts/heldout_eval.py` — held-out seed separate from the training seed. Writes `heldout_evaluation.json` when run |
| **Webhook idempotency** | `app/store.py` — `webhook_events` table, `is_event_processed()` / `mark_event_processed()`, checked at webhook handler top in `app/main.py` |
| **Portfolio optimizer (0/1 knapsack)** | `app/portfolio.py:knapsack_select()` — maximizes EV within human-review hour capacity. Demo in `scripts/demo_portfolio.py`; tested in `tests/test_portfolio.py` |
| **SHAP per-case explainability** | `app/recovery_model.py:RecoveryModel._explain()` — TreeExplainer for per-case signed SHAP values. Falls back to feature_importances_. Tested in `tests/test_shap.py` |
| **India-specific compliance** | `app/policy.py:evaluate()` — RBI e-mandate pre-debit notice (≥₹5000, 24h), RBI UPI daily limit (₹1,00,000), TRAI-style quiet hours **22:00–08:00 IST** (`config.yaml → policy.quiet_hours_ist: [22, 8]`) |
| **Promise-to-pay EV feedback** | `app/store.py:promise_reliability()` — cross-case, persisted share of a customer's promises kept; `app/selector.py:promise_ev_multiplier()` scales candidate EVs (0.5x at 0% reliability → 1.1x at 100%), recorded in the audit reasoning chain. Tested in `tests/test_promise_ev.py` |
| **Provider switching (Mock/Ollama/Claude)** | `app/main.py:/provider` GET/POST endpoints — live toggle |
| **SSE batch progress** | `app/main.py:/batch/run/stream` — Server-Sent Events stream for live batch run progress |
| **23 failure categories** | `app/models.py:FailureClass` — 23 members including Paytm-specific classes (WALLET_INSUFFICIENT, KYC_INCOMPLETE, UPI_LIMIT_EXCEEDED, MANDATE_LAPSED, DUPLICATE_TRANSACTION, OFFLINE_PAYMENT_PENDING, TELECOM_NETWORK, GATEWAY_LATENCY); rule table in `app/classifier.py:_RULES` |
| **Case detail timeline** | `app/main.py:/cases/{case_id}/detail` — full case timeline |
| **Editable compliance settings** | `app/main.py:/settings` GET/POST — max_attempts, quiet hours, DND list, discount_pct, escalation_threshold |
| **Cryptographic hash-chained audit trail** | `app/audit_chain.py:AuditChain` — SHA-256 chain (H_i = SHA256(H_{i-1} \|\| step \|\| payload)); hash columns persisted per event in the SQLite audit table |
| **Payment network degradation detector** | `app/network_health.py:NetworkHealthMonitor` — rolling success rates per method, MODERATE/CRITICAL flags |
| **Recovery funnel with drop-off accounting** | `app/main.py:/analytics/funnel` — 4 stages, drop-offs = policy block reasons (attempt cap, opt-out, expiry, …) + promise_paused |
| **Model calibration view (10-decile)** | `app/main.py:/analytics/calibration` — predicted vs observed recovery rate per decile |
| **Decision inspector with rejected alternatives** | `app/main.py:/cases/{id}/decision` — evaluates all candidate actions with EV, policy verdicts, and rejected alternatives |
| **Explicit NO_ACTION when EV negative** | `app/selector.py` — evaluates all candidates, returns None if max net EV <= 0; promise reliability scales the candidate EVs |
| **Segment breakdown by amount tier** | `app/main.py:/analytics/segments` — tiered recovery-rate breakdown |
| **Audit chain verification endpoint** | `app/main.py:/audit/chain/verify` — chain integrity check |
| **Live_verified vs Demo_verified webhook modes** | `app/agent.py:mark_recovered` + `app/main.py` webhook handler — explicit verification mode (cryptographic vs simulation) |
| **Exponential backoff for external APIs** | `app/main.py:exponential_backoff` — 0.5s base × 2^n with jitter, max 3 retries, 8s ceiling |
| **Rehearsed seed for reproducible demo** | `app/main.py:/batch/run/stream?rehearsed=true` — forces seed 42 so every demo run shows the same cohort (100-case run measures T 70.9% vs C 14.3%) |
| **Handled-gracefully page for hard-decline** | `app/main.py:/handled-gracefully` — deterministically picks a case the agent correctly refused to re-charge |
| **LLM-vs-Rules Gate override contrast** | `app/main.py:/cases/{id}/gate-contrast` — shows LLM diagnosis vs Rules Gate verdict |
| **Probabilistic outcome model (demo mode)** | `app/main.py:_demo_recovery_prob` — transparent heuristic by failure class/amount/attempts |
| **Self-hosted fonts / no external deps** | `app/static/dashboard.html` — system-ui font stack, vendored JS, no CDN |
| **Demo verification endpoint** | `app/main.py:/demo/verify/{case_id}` — simulate payment with demo_verified label |
| **Threat model with mitigations** | `app/main.py:THREAT_MODEL` + `/security/threat-model` — 8 threats, all mitigated |
| **Prompt-injection demo** | `app/main.py:/security/prompt-injection-test` — LLM is advisory-only, rules gate decides (`app/adversarial.py:run_adversarial_test()`) |
| **Human approval queue** | `app/main.py:/approval/queue` + `/approve` + `/reject` — ₹10,000 queue threshold (`_APPROVAL_THRESHOLD_PAISE`); the policy gate's automated-money cap is ₹25,000 (`config.yaml → policy.auto_action_cap_paise`) |
| **CUSUM change-point detector** | `app/cusum.py:CUSUMDetector` — Page's CUSUM for success-rate shifts |
| **Multi-armed bandit channel selection** | `app/bandit.py:ChannelBandit` — UCB1 across WhatsApp/SMS/Email/Voice/Retry (5 arms). Tested in `tests/test_bandit.py` |
| **Late-auth failure class** | `app/models.py:FailureClass.LATE_AUTH` + classifier rule + selector strategy (capture-window retry ladder) |
| **Uplift model** | `app/uplift.py:uplift()` — P(recovery\|A) − P(recovery\|no_action), incremental EV per action |
| **Intervention budget** | `app/policy.py:InterventionBudget` — shared cap, atomic deduction, per-channel budgets |
| **TOCTOU revalidation** | `app/policy.py:revalidate()` — re-checks case state immediately before execution |
| **Incident log** | `app/incidents.py` — `INCIDENTS` list, 7 documented failures with root causes and fixes; served via `GET /incidents` |
| **Adversarial LLM test** | `app/adversarial.py:run_adversarial_test()` — a deliberately malicious strategist cannot violate compliance (gate is structural) |
| **Combined safety report** | `app/main.py:/security/report` — threat model + adversarial test + audit chain in one endpoint |
| **WebSocket live replay** | `app/main.py:/ws/replay` — streams per-case events as JSON for real-time dashboard updates |

## How to reproduce

```bash
# Seeded canonical batch -> report.json (all headline numbers)
.venv/bin/python scripts/run_batch.py --seed 42

# Core demo + API (no keys needed)
RECOVERY_DB=demo.db PAYTM_WEBHOOK_SECRET=demo_secret \
    .venv/bin/uvicorn app.main:app --port 8000
.venv/bin/python scripts/demo.py
.venv/bin/python -m pytest tests/ -q

# Evaluation scripts (each writes its report json on run)
.venv/bin/python scripts/evaluate.py       # 5-seed eval -> evaluation_report.json
.venv/bin/python scripts/sensitivity.py    # ±20% sweep -> sensitivity_report.json
.venv/bin/python scripts/heldout_eval.py   # held-out eval -> heldout_evaluation.json
```

## What is NOT claimed

- No claim of production deployment or live payment processing
- No claim of real Paytm/paytm API integration beyond test mode
- No claim of specific revenue recovered from real customers (simulated data only)
- No claim of model accuracy on unseen production data
- The world model's response probabilities are stated assumptions
  (`config.yaml → world:`) — that is why the control group exists
