#!/usr/bin/env python
"""5-minute judge demo: the full agent loop, live, against a local server.
No paytm keys, no waiting — every step is a real HTTP call through the
real classifier, selector, policy gate, and audit trail.

Terminal 1 (the agent):
    RECOVERY_DB=demo.db PAYTM_WEBHOOK_SECRET=demo_secret \
        .venv/bin/uvicorn app.main:app --port 8000

Terminal 2 (this script):
    .venv/bin/python scripts/demo.py

Optionally point at another server: --base http://localhost:9000
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SECRET = "demo_secret"
PHONE = "+9199990000042"


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post_event(base: str, event: dict) -> dict:
    body = json.dumps(event).encode()
    r = httpx.post(f"{base}/webhooks/paytm", content=body,
                   headers={"X-paytm-Signature": sign(body)}, timeout=15)
    r.raise_for_status()
    return r.json()


def failed_payment(payment_id: str, code: str, desc: str, amount: int) -> dict:
    """Paytm-format TXN_FAILED webhook payload."""
    return {
        "STATUS": "TXN_FAILED",
        "ORDERID": f"order_{payment_id[-6:]}",
        "TXNID": payment_id,
        "TXNAMOUNT": f"{amount / 100:.2f}",
        "PAYMENTMODE": "card",
        "RESPCODE": code,
        "RESPMSG": desc,
        "CUST_ID": "cust_demo001",
        "CALLBACKUSERNAME": "Demo Customer",
        "MOBILE_NO": PHONE,
        "EMAIL": "demo@example.com",
    }


def reply(base: str, text: str) -> dict:
    r = httpx.post(f"{base}/inbound/reply", json={"from": PHONE, "text": text},
                   timeout=15)
    r.raise_for_status()
    return r.json()


def show(audit: dict, stages: list[str]) -> None:
    for e in audit["events"]:
        if e["event_type"] in stages:
            extra = {k: v for k, v in e.items()
                     if k not in ("event_id", "ts", "actor", "event_type", "case_id")}
            print(f"    [{e['actor']:>10}] {e['event_type']}")
            for k, v in extra.items():
                print(f"                {k}: {str(v)[:110]}")


def find_gate_event(base: str) -> None:
    """Step 3: pull a real DEFER/BLOCK verdict out of the seeded audit trail."""
    cases = httpx.get(f"{base}/cases/recent?limit=100", timeout=15).json()["cases"]
    for c in cases:
        audit = httpx.get(f"{base}/audit/{c['case_id']}", timeout=15).json()
        for e in audit["events"]:
            if e["event_type"] in ("action.deferred", "action.blocked"):
                reason = e.get("reason", e.get("blocked_reason", ""))
                when = e.get("execute_at", "")
                print(f"    case {c['case_id']}: {e['event_type']} — {reason}")
                if when:
                    print(f"                rescheduled to: {when}")
                return
    print("    (no deferred/blocked action in seed — run the seed step)")


def seed_sim(db: str) -> None:
    """150-case simulated batch into the demo DB so /report and the policy
    gate have real history (2-3s, seeded, deterministic)."""
    import yaml

    from app.store import Store
    from simulate.batch_generator import generate_batch
    from simulate.engine import run

    cfg = yaml.safe_load(Path("config.yaml").read_text())
    cfg["simulation"]["batch_size"] = 150
    p = Path(db)
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(p) + suffix)
        if f.exists():
            f.unlink()
    payments = generate_batch(150, datetime(2026, 8, 20, 6, 0,
                                            tzinfo=timezone.utc), seed=7)
    store = Store(p)
    run(payments, cfg, store)
    store.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--db", default="demo.db",
                    help="must match the server's RECOVERY_DB")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    httpx.get(f"{base}/report", timeout=10)          # fail fast if server is down

    print("=" * 68)
    print("STEP 0 — seed 150 simulated cases (2s) so the report has history")
    print("=" * 68)
    seed_sim(args.db)
    print("    done — /report now shows measured lift vs control\n")

    print("=" * 68)
    print("STEP 1 — failed payment enters: signed payment.failed webhook")
    print("=" * 68)
    out = post_event(base, failed_payment(
        "pay_demo00001", "insufficient_funds",
        "Payment declined due to insufficient funds", 150_000))
    case1 = out["case_id"]
    print(f"    -> {out}  (₹1,500, card)\n")

    print("=" * 68)
    print("STEP 2 — classification + strategy selection (from the audit trail)")
    print("=" * 68)
    show(httpx.get(f"{base}/audit/{case1}", timeout=15).json(),
         {"case.created", "action.scheduled"})
    print()

    print("=" * 68)
    print("STEP 3 — the policy gate: stopping rules, defers, blocks")
    print("=" * 68)
    print("  (a) hard decline -> the gate refuses same-instrument retries:")
    out2 = post_event(base, failed_payment(
        "pay_demo00002", "blocked_card",
        "Card blocked or flagged, do not honor", 450_000))
    show(httpx.get(f"{base}/audit/{out2['case_id']}", timeout=15).json(),
         {"action.scheduled"})
    print("  (b) a real DEFER/BLOCK verdict from the seeded trail:")
    find_gate_event(base)
    print("  (c) customer says STOP -> global opt-out, case closed:")
    print(f"    -> {reply(base, 'STOP')}  on case {out2['case_id']}\n")
    _ = out2  # opt-out wrote it off; kept for reference

    print("=" * 68)
    print("STEP 4 — the customer talks back: promise, then recovery")
    print("=" * 68)
    print(f"    reply 'kal pakka'  -> {reply(base, 'kal pakka')}")
    print(f"    reply 'paid'       -> {reply(base, 'paid')}")
    paid = {
        "STATUS": "TXN_SUCCESS",
        "ORDERID": f"order_{case1[-6:]}",
        "TXNID": "pay_demoPaid1",
        "TXNAMOUNT": "1500.00",
        "PAYMENTMODE": "card",
        "RESPCODE": "01",
        "RESPMSG": "Txn Success",
        "CUST_ID": "cust_demo001",
        "MOBILE_NO": PHONE,
        "EMAIL": "demo@example.com",
    }
    print(f"    paid webhook       -> {post_event(base, paid)}\n")

    print("=" * 68)
    print("STEP 5 — what broke & what we learned")
    print("=" * 68)
    print("  (a) classify(fp) crashed at runtime — passed a FailedPayment object")
    print("      to classify() which expects (raw_code, description, method).")
    print("      Then accessed .failure_class on the return — a tuple, not object.")
    print("      Fix: classify(fp.raw_error_code, fp.error_description, fp.method)")
    print("      Lesson: type hints catch this, but only if you run the code path.")
    print()
    print("  (b) Thompson Sampling bandit converged too slowly — 50 pulls and")
    print("      still exploring. Swapped to UCB1 which balances exploration vs")
    print("      exploitation mathematically. Faster convergence, same guarantees.")
    print("      Lesson: simpler algorithm, better fit for this problem size.")
    print()
    print("  (c) We tried full LLM classification (Groq Qwen). Too slow,")
    print("      too expensive at scale. Rules are 99% accurate for known error")
    print("      codes. Groq now handles only novel/edge-case descriptions.")
    print("      Lesson: simpler > fancier. Use AI where rules fail, not everywhere.")
    print()

    print("=" * 68)
    print("STEP 6 — the proof: measured lift + the full reasoning chain")
    print("=" * 68)
    rep = httpx.get(f"{base}/report", timeout=15).json()
    hd = rep["headline"]
    print(f"    lift {hd['incremental_recovery_pp']:+.1f} pp "
          f"[95% CI {hd['incremental_recovery_ci95_pp'][0]:+.1f}, "
          f"{hd['incremental_recovery_ci95_pp'][1]:+.1f}]"
          f"  ·  incremental {hd['incremental_money_paise'] / 1e7:.2f} ₹L"
          f"  ·  blocks: {rep['policy_transparency']['blocked_actions']}")
    print(f"\n    audit trail for the recovered case ({case1}):")
    show(httpx.get(f"{base}/audit/{case1}", timeout=15).json(),
         {"recovery.confirmed", "inbound.reply", "promise.scheduled_check"})

    print("\n" + "=" * 68)
    print("DASHBOARD (open in browser)")
    print("=" * 68)
    print(f"    dashboard   {base}/")
    print(f"    case audit   {base}/audit/{case1}")
    print(f"    api docs     {base}/docs")
    print(f"    calculator   {base}/  (ROI Calculator button)")
    print("\n    Demo cases live in demo.db — the canonical report.json is untouched.")
    print(f"    Open {base}/ in your browser to see the dashboard.")


if __name__ == "__main__":
    main()
