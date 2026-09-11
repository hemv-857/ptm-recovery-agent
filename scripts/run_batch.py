#!/usr/bin/env python
"""End-to-end batch demo: generate cohort -> simulate agent loop -> report.

Usage:
    python scripts/run_batch.py [--n 2000] [--seed 42] [--db recovery.db]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.measure import build_report, classification_eval, fmt_rupees
from app.store import Store
from simulate.batch_generator import assign_groups, generate_batch
from simulate.engine import run


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--db", type=str, default="recovery.db")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text())
    if args.n:
        cfg["simulation"]["batch_size"] = args.n
    n = cfg["simulation"]["batch_size"]

    db_path = Path(args.db)
    if db_path.exists():
        db_path.unlink()

    t_start = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)  # IST noon Aug 20
    print(f"[1/4] generating {n} synthetic failed payments ...")
    payments = generate_batch(n, t_start, seed=args.seed)
    groups = assign_groups(payments)
    n_t = sum(1 for g in groups if g.value == "treatment")
    print(f"      treatment={n_t} control={n - n_t}")

    print("[2/4] scoring deterministic classifier vs ground truth ...")
    ceval = classification_eval(payments)
    print(f"      classification accuracy = {ceval['accuracy']:.1%} "
          f"(confusions: {ceval['top_confusions']})")

    store = Store(db_path)
    print("[3/4] running agent loop (discrete event simulation) ...")
    t0 = time.time()
    run(payments, cfg, store, progress=lambda m: print(f"      {m}"))
    print(f"      done in {time.time() - t0:.1f}s")

    print("[4/4] computing measured outcomes ...")
    rep = build_report(store.all_cases(), store.actions_rows(), cfg)

    hd = rep["headline"]
    print()
    print("=" * 64)
    print("HEADLINE — treatment vs randomized control")
    print("=" * 64)
    print(f"  cases                    : {rep['batch']['cases']} "
          f"({rep['batch']['treatment_n']}T / {rep['batch']['control_n']}C)")
    print(f"  amount at risk           : {fmt_rupees(rep['batch']['amount_at_risk_paise'])}")
    print(f"  recovery rate            : T {hd['recovery_rate_treatment']*100:.1f}% "
          f"vs C {hd['recovery_rate_control']*100:.1f}%")
    ci = hd["incremental_recovery_ci95_pp"]
    print(f"  incremental lift         : {hd['incremental_recovery_pp']:+.1f} pp "
          f"[95% CI {ci[0]:+.1f}, {ci[1]:+.1f}]")
    print(f"  incremental money        : {fmt_rupees(hd['incremental_money_paise'])}")
    cost = rep["cost"]
    cpir = cost["cost_per_incremental_recovery_paise"]
    print(f"  spend / contacts         : {fmt_rupees(cost['spend_paise'])} / "
          f"{cost['contacts_executed']}")
    print(f"  cost per incr. recovery  : {fmt_rupees(cpir) if cpir is not None else 'n/a'}")
    print(f"  redundant-contact share  : {cost['redundant_contact_share']*100:.0f}%")
    print(f"  opt-outs caused          : {cost['opt_outs']}")
    pr = rep["promises"]
    if pr["received"]:
        print(f"  promises-to-pay          : {pr['received']} received, "
              f"keep rate {pr['keep_rate']*100:.0f}%, "
              f"{fmt_rupees(pr['money_via_promises_paise'])} via promises")
    blocked = rep["policy_transparency"]["blocked_actions"]
    if blocked:
        print(f"  policy blocks            : {blocked}")

    out = Path("report.json")
    out.write_text(json.dumps(
        {"report": rep, "classification": ceval}, indent=2, default=str
    ))
    print()
    print(f"full JSON -> {out.resolve()}")
    print("dashboard -> uvicorn app.main:app --port 8000, then open http://localhost:8000/")
    store.close()


if __name__ == "__main__":
    main()
