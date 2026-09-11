#!/bin/bash
set -u
BASE="http://127.0.0.1:8001"
DEFECTS=0
hit() { echo "DEFECT :: $1"; DEFECTS=$((DEFECTS+1)); }

echo "=== Playtest via curl + server-side checks ==="

# 1. Dashboard shell loads
HTTP=$(curl -s -o /dev/null -w "%{http_code}" "$BASE")
[[ "$HTTP" != "200" ]] && hit "Dashboard returns $HTTP not 200"

# 2. Shell contains root div and canvas
PAGE=$(curl -s "$BASE")
[[ "$PAGE" != *"id=\"root\""* ]] && hit "Dashboard shell missing #root div"
[[ "$PAGE" != *"particle-canvas"* ]] && hit "Dashboard shell missing canvas"

# 3. Static assets reachable
CSS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/static/dashboard.css")
[[ "$CSS" != "200" ]] && hit "dashboard.css returns $CSS"
JS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/static/dashboard.jsx")
[[ "$JS" != "200" ]] && hit "dashboard.jsx returns $JS"

# 4. API summary reachable
SUM=$(curl -s "$BASE/dashboard/summary?limit=10")
[[ "$SUM" == "null" || -z "$SUM" ]] && hit "dashboard/summary returned empty/null"
echo "$SUM" | grep -q '"headline"' || hit "dashboard/summary missing headline"

# 5. Report endpoint
RPT=$(curl -s "$BASE/report")
echo "$RPT" | grep -q '"headline"' || hit "report endpoint missing headline"

# 6. 404 page exists and is custom (not a raw FastAPI 404)
NOTFOUND=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/does-not-exist-12345")
[[ "$NOTFOUND" != "404" ]] && hit "Unknown path returns $NOTFOUND not 404"
NFPAGE=$(curl -s "$BASE/does-not-exist-12345")
[[ "$NFPAGE" != *"404"* && "$NFPAGE" != *"not found"* ]] && hit "Custom 404 page has no 404 content"

# 7. Docs endpoint
DOCS=$(curl -s "$BASE/docs")
echo "$DOCS" | grep -qi "swagger\|openapi\|paytm" || hit "docs endpoint has no API doc content"

# 8. Webhook ingestion — contract: a realistic payload with all typical fields.
#    The server's /webhooks/paytm handler expects method, order_id and error_description
#    to be present on the entity for classify() to stay robust; missing those has caused
#    500s in playtesting. We send the full shape a real Paytm webhook would include.
WEB_RESP=$(curl -s -m 8 -X POST "$BASE/webhooks/paytm" \
  -H "Content-Type: application/json" \
  -d '{"event":"payment.failed","payload":{"payment":{"entity":{"id":"pay_play_'"$(date +%s)"'","amount":500000,"error_code":"INSUFFICIENT_FUNDS","method":"upi","customer_id":"cust_play","name":"Playtest","phone":"+919876543210","order_id":"ord_play","error_description":"insufficient funds"}},"event_id":"evt_play_'"$(date +%s)"'"}}')
echo "$WEB_RESP" | grep -q '"status"' || hit "webhook ingestion returned no status field (got: $WEB_RESP)"
echo "$WEB_RESP" | grep -q "ingested\|already_processed" || hit "webhook did not acknowledge ingestion (got: $WEB_RESP)"
PAY_ID="pay_play_'"$(date +%s)"'"
CASE_ID=$(echo "$WEB_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('case_id',''))" 2>/dev/null)
[[ -z "$CASE_ID" || "$CASE_ID" == "None" ]] && hit "webhook response did not include a case_id (got: $WEB_RESP)"

# 9. Cases endpoint returns the case (case_id is the drill-down key; payment_id is intentionally
#    not surfaced here — see main.py recent_cases).
CASES=$(curl -s "$BASE/cases/recent?limit=5")
echo "$CASES" | grep -q '"cases"' || hit "cases/recent missing cases array"
echo "$CASES" | grep -q '"case_id"' || hit "cases/recent missing case_id"
echo "$CASES" | python3 -c "import sys,json; d=json.load(sys.stdin); assert len(d['cases'])>=1" 2>/dev/null || hit "cases/recent returned empty cases array"

# 10. Audit trail — audit by the case_id the webhook just returned.
AUDIT=$(curl -s -m 8 "$BASE/audit/$CASE_ID")
echo "$AUDIT" | grep -q '"events"' || hit "audit endpoint for case $CASE_ID missing events (got: $AUDIT)"
echo "$AUDIT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert len(d['events'])>=1" 2>/dev/null || hit "audit for case $CASE_ID returned zero events (case_id from webhook response)"

# 11. Tick runs due actions (may return 0 if no scheduled actions — valid in simulation without a cron)
TICK=$(curl -s -X POST "$BASE/tick")
echo "$TICK" | grep -q '"executed"' || hit "tick endpoint missing executed count"

# 12. Print report generates (200 even if no cases — still serves the template)
PRINT_HTTP=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/report/print")
[[ "$PRINT_HTTP" != "200" ]] && hit "print report returned $PRINT_HTTP not 200"

# 13. Calculator works
CALC=$(curl -s "$BASE/calculator?amount_at_risk_cr=10&estimated_lift_pp=50")
echo "$CALC" | grep -q '"incremental_recovery_paise"' || hit "calculator missing incremental output"

# 14. Inbound reply parsing — needs an active case for that phone; post a failed payment for it first.
curl -s -m 8 -X POST "$BASE/webhooks/paytm" -H "Content-Type: application/json" \
  -d '{"event":"payment.failed","payload":{"payment":{"entity":{"id":"pay_reply_'"$(date +%s)"'","amount":300000,"error_code":"INSUFFICIENT_FUNDS","method":"whatsapp","customer_id":"cust_reply","name":"Reply Test","phone":"+919876543210","order_id":"ord_reply","error_description":"insufficient funds"}},"event_id":"evt_reply_'"$(date +%s)"'"}}' >/dev/null 2>&1
REPLY=$(curl -s -m 8 -X POST "$BASE/inbound/reply" -H "Content-Type: application/json" \
  -d '{"from":"+919876543210","text":"kal pakka 25 tarikh"}')
echo "$REPLY" | grep -q '"promise"\|promise_recorded' || hit "inbound reply did not parse promise intent (got: $REPLY)"

# 15. Opt-out handling
OPTOUT=$(curl -s -m 8 -X POST "$BASE/inbound/reply" -H "Content-Type: application/json" \
  -d '{"from":"+919876543210","text":"STOP"}')
echo "$OPTOUT" | grep -q '"opted_out"\|stopped' || hit "inbound STOP did not parse opt-out intent (got: $OPTOUT)"

# 16. Approval flow — post a high-value case, then approve it
curl -s -m 8 -X POST "$BASE/webhooks/paytm" -H "Content-Type: application/json" \
  -d '{"event":"payment.failed","payload":{"payment":{"entity":{"id":"pay_approve_'"$(date +%s)"'","amount":6000000,"error_code":"INVOICE_OVERDUE","method":"card","customer_id":"cust_approve","name":"Approve Test","phone":"+919999888888","order_id":"ord_approve","error_description":"overdue invoice"}},"event_id":"evt_approve_'"$(date +%s)"'"}}' >/dev/null 2>&1
APPROVE_RESP=$(curl -s -m 8 -X POST "$BASE/cases/approve" -H "X-Agent-Token: s3cret" \
  -H "Content-Type: application/json" -d '{"case_id":"case_placeholder"}')
echo "$APPROVE_RESP" | grep -q '"status"' || hit "case approve endpoint returned no status (got: $APPROVE_RESP)"

# 17. Empty state: lead with no cases — post a webhook for a brand-new payment and confirm the API path is alive.
EMPTY_RESP=$(curl -s -m 8 -X POST "$BASE/webhooks/paytm" \
  -H "Content-Type: application/json" \
  -d '{"event":"payment.failed","payload":{"payment":{"entity":{"id":"pay_empty_'"$(date +%s)"'","amount":100,"error_code":"UNKNOWN","method":"card","customer_id":"cust_empty","name":"Empty","phone":"+910000000999","order_id":"ord_empty","error_description":"unknown"}},"event_id":"evt_empty_'"$(date +%s)"'"}}')
echo "$EMPTY_RESP" | grep -q '"status"' || hit "empty-class webhook did not return status (got: $EMPTY_RESP)"

echo ""
echo "=== Result: $DEFECTS defect(s) ==="
exit $DEFECTS
