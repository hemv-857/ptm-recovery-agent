# Pre-Deployment Checklist

## Accessibility (dashboard)

- [ ] Keyboard-only pass: Tab reaches every control; `Cmd/Ctrl+K` opens the command palette; `Esc` closes palette/notifications/modal
- [ ] Focus visible: `:focus-visible` rings render on every interactive element (never `outline: none` without replacement)
- [ ] Reduced motion: with OS "reduce motion" on, the particle canvas is hidden and entrance animations are suppressed (`prefers-reduced-motion` in `dashboard.css`)
- [ ] Color independence: status is never color-only (pills carry text labels; heatmap cells show `n` counts)
- [ ] Icon-only buttons carry `aria-label` (theme toggle, refresh, notifications, hamburger)
- [ ] Contrast: dim text (`--dim`) must stay readable against `--bg` in **both** light and dark themes
- [ ] Mobile: nav drawer opens/closes under 768px; no horizontal scroll on the ledger at 375px width

Everything to verify before pointing this at real customers. Items link to the
command or file that answers them — no vibes, all checks.

## paytm integration

- [ ] Test-mode keys set in `.env` (`PAYTM_KEY_ID/SECRET/WEBHOOK_SECRET`)
- [ ] Preflight passes end to end: `.venv/bin/python scripts/live_check.py`
      (key format → API auth → ₹1 payment link → signed webhook round-trip)
- [ ] Dashboard webhooks subscribed: `payment.failed`, `payment_link.paid`,
      secret = `PAYTM_WEBHOOK_SECRET`
- [ ] Duplicate delivery handled: re-send the same `payment.failed` event,
      confirm `GET /audit/{case_id}` shows one `case.created`

## Compliance review

- [ ] Messaging stance confirmed: transactional-only + STOP footer
      (`app/copywriter.py`); claims mapped line-by-line in `COMPLIANCE.md`
- [ ] Opt-out honored: send `STOP` to `POST /inbound/reply`, verify the case
      writes off and further actions BLOCK with `customer_opted_out`
- [ ] Quiet hours active: `config.yaml → policy.quiet_hours_ist` (default
      22:00–08:00 IST); schedule an action inside the window, watch it DEFER
- [ ] High-value guardrail: ingest a failure above
      `auto_action_cap_paise`, confirm money actions DEFER until
      `POST /cases/{id}/approve` and ops gets the Slack ping

## Monitoring & limits

- [ ] `SLACK_WEBHOOK_URL` set; escalations/opt-outs/refusals arrive
- [ ] Rate limiting sane: `RATE_LIMIT_PER_MIN` (default 120), burst → 429 +
      `Retry-After`
- [ ] Log aggregation in place (uvicorn logs → your stack)

## Data

- [ ] Backup plan for the SQLite file (`recovery.db`, WAL mode) — snapshot on
      your schedule; it is a single file, so file-level backup works
- [ ] Retention window decided (operator-managed by design — see
      `COMPLIANCE.md → Data handling`)
- [ ] Multi-merchant: `X-Merchant-Id` routing verified per tenant DB, or one
      deployment per merchant account

## Deployment

- [ ] `docker compose up --build` serves the dashboard on :8000 and the
      healthcheck reports `healthy`
- [ ] Kubernetes: `helm lint charts/paytm-recovery-agent` clean, image published
      (CI pushes to ghcr.io on main), ticker stays **1 replica**
- [ ] `AGENT_API_TOKEN` set and rotated away from the default empty
- [ ] No secrets in the repo: `grep -rn "paytm_live\|paytm_test" --include="*.py" --include="*.yaml" .`
      returns nothing but docs

## Final gates

- [ ] `.venv/bin/python -m pytest tests -q` — all green
- [ ] `.venv/bin/ruff check .` — clean
- [ ] `./scripts/quickstart.sh` — reproduces the headline numbers exactly
      (the pipeline is seeded; any drift is a bug, file it)
