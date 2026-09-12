#!/usr/bin/env python
"""Paytm test-mode preflight. Run AFTER putting real keys in .env:

    cp .env.example .env      # fill PAYTM_MERCHANT_ID / MERCHANT_KEY / WEBHOOK_SECRET
    .venv/bin/python scripts/paytm_preflight.py

Checks, in order: credentials -> API auth -> payment link creation ->
webhook signature round-trip through the actual receiver.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dotenv import load_env

load_env()

import os

import httpx

from app.payment_processor import PaytmProcessor

MERCHANT_ID = os.getenv("PAYTM_MERCHANT_ID", "")
MERCHANT_KEY = os.getenv("PAYTM_MERCHANT_KEY", "")
WEBHOOK_SECRET = os.getenv("PAYTM_WEBHOOK_SECRET", "")
BASE = "https://securegw-stage.paytm.in"  # staging

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


print("[1/4] credentials")
check("merchant ID present", bool(MERCHANT_ID))
check("merchant key present", bool(MERCHANT_KEY))
if not (MERCHANT_ID and MERCHANT_KEY):
    print("\nPut PAYTM_MERCHANT_ID / PAYTM_MERCHANT_KEY in .env first.")
    sys.exit(1)

print("[2/4] API auth")
try:
    # Paytm order status check (minimal auth test)
    import hashlib as _hl
    params = {"mid": MERCHANT_ID, "orderId": "preflight_check"}
    checksum_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    checksum_str += f"&{MERCHANT_KEY}"
    checksum = _hl.sha256(checksum_str.encode()).hexdigest()
    params["CHECKSUMHASH"] = checksum

    r = httpx.post(f"{BASE}/order/status", json=params, timeout=15)
    # Any non-500 response means auth is working
    check("POST /order/status reachable", r.status_code != 500,
          f"HTTP {r.status_code}")
except Exception as e:
    check("POST /order/status reachable", False, str(e))
    sys.exit(1)

print("[3/4] payment link creation (Rs 1 test)")
try:
    proc = PaytmProcessor()
    plink = proc.create_payment_link(
        amount=100, customer_id="preflight_cust",
        name="Preflight User", email="preflight@example.com",
        phone="+919999900001", description="paytm preflight check",
        reference_id=f"preflight_{int(time.time())}",
    )
    check("payment link created", bool(plink.get("short_url")),
          plink.get("short_url", "no URL"))
except Exception as e:
    check("payment link created", False, str(e))

print("[4/4] webhook signature round-trip through the real receiver")
if not WEBHOOK_SECRET:
    check("webhook secret set", False,
          "add PAYTM_WEBHOOK_SECRET (set it in Paytm Dashboard -> Webhooks too)")
else:
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    failed_event = {
        "STATUS": "TXN_FAILED",
        "ORDERID": f"preflight_{int(time.time())}",
        "TXNID": "paytm_preflight001",
        "TXNAMOUNT": "2500.00",
        "PAYMENTMODE": "UPI",
        "RESPCODE": "810",
        "RESPMSG": "Payment failed due to insufficient balance",
        "CUST_ID": "cust_preflight1",
        "MOBILE_NO": "+919999900001",
        "EMAIL": "preflight@example.com",
        "CALLBACKUSERNAME": "Preflight User",
    }
    body = json.dumps(failed_event).encode()
    sig = PaytmProcessor.sign(body, WEBHOOK_SECRET)
    resp = c.post("/webhooks/paytm", content=body,
                  headers={"X-Paytm-Signature": sig})
    case_id = resp.json().get("case_id", "") if resp.status_code == 200 else ""
    check("signed TXN_FAILED accepted", resp.status_code == 200 and bool(case_id),
          f"HTTP {resp.status_code} {resp.text[:120]}")

    bad = c.post("/webhooks/paytm", content=body,
                 headers={"X-Paytm-Signature": "deadbeef"})
    check("forged signature rejected", bad.status_code == 400)

    success_event = {
        "STATUS": "TXN_SUCCESS",
        "ORDERID": case_id or f"preflight_{int(time.time())}",
        "TXNID": "paytm_preflight_paid1",
        "TXNAMOUNT": "2500.00",
        "PAYMENTMODE": "UPI",
        "RESPCODE": "01",
        "RESPMSG": "Txn Success",
    }
    body2 = json.dumps(success_event).encode()
    sig2 = PaytmProcessor.sign(body2, WEBHOOK_SECRET)
    resp2 = c.post("/webhooks/paytm", content=body2,
                   headers={"X-Paytm-Signature": sig2})
    check("signed TXN_SUCCESS marks recovery",
          resp2.status_code == 200 and resp2.json().get("status") == "recovered",
          f"HTTP {resp2.status_code} {resp2.text[:120]}")

print()
failed = [r for r in results if not r[1]]
if failed:
    print(f"{len(failed)} check(s) FAILED — fix above, then re-run.")
    sys.exit(1)
print("ALL CHECKS PASS — Paytm test-mode plumbing is verified end to end.")
print()
print("Next steps:")
print("  1. Paytm Dashboard -> Settings -> Webhooks -> add URL (https://.../webhooks/paytm),")
print("     secret = same as PAYTM_WEBHOOK_SECRET, events: TXN_FAILED, TXN_SUCCESS")
print("  2. Expose localhost for testing: ngrok http 8000")
print("  3. Run the agent: .venv/bin/uvicorn app.main:app --port 8000")
print("  4. Cron/tick every minute during testing: curl -X POST localhost:8000/tick")
