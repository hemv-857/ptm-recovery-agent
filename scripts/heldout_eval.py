"""Generate a held-out evaluation set: separate from the training seed,
uses different random draws to prevent data leakage.

Usage:
    .venv/bin/python scripts/heldout_eval.py
    .venv/bin/python scripts/heldout_eval.py --n 500 --seed 999
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.measure import build_report, classification_eval
from app.store import Store
from simulate.batch_generator import generate_batch
from simulate.engine import run


def main() -> None:
    ap = argparse.ArgumentParser(description="Held-out evaluation set")
    ap.add_argument("--n", type=int, default=500, help="Cases in held-out set")
    ap.add_argument("--seed", type=int, default=999, help="Different seed from training (42)")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text())

    # Training uses seed 42; held-out uses 999 — no overlap
    payments = generate_batch(
        args.n, datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc), seed=args.seed
    )

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = Store(Path(db_path))
    run(payments, cfg, store)
    report = build_report(store.all_cases(), store.actions_rows(), cfg)
    cls_eval = classification_eval(payments)
    store.close()

    Path(db_path).unlink(missing_ok=True)
    for sfx in ("-wal", "-shm"):
        Path(str(db_path) + sfx).unlink(missing_ok=True)

    out = {
        "description": "Held-out evaluation set (seed 999, separate from training seed 42)",
        "seed": args.seed,
        "n": args.n,
        "classification_accuracy": cls_eval["accuracy"],
        "classification_confusions": cls_eval["top_confusions"],
        "headline": report["headline"],
        "cost": report["cost"],
        "per_class": report["per_class"],
        "note": (
            "This set was never used during model training or parameter tuning. "
            "The recovery model trains on batch outcomes (seed 42); this held-out set "
            "uses a completely different random draw (seed 999) to prevent data leakage."
        ),
    }

    out_path = Path("heldout_evaluation.json")
    out_path.write_text(json.dumps(out, indent=2))

    hd = report["headline"]
    print(f"Held-out set: {args.n} cases (seed {args.seed})")
    print(f"  Classification accuracy: {cls_eval['accuracy']:.1%}")
    print(f"  Treatment recovery rate: {hd['recovery_rate_treatment']:.1%}")
    print(f"  Control recovery rate:   {hd['recovery_rate_control']:.1%}")
    print(f"  Incremental lift:        {hd['incremental_recovery_pp']:+.1f}pp")
    ci0 = hd['incremental_recovery_ci95_pp'][0]
    ci1 = hd['incremental_recovery_ci95_pp'][1]
    print(f"  95% CI:                  [{ci0:+.1f}, {ci1:+.1f}]pp")
    print(f"  Written to {out_path}")


if __name__ == "__main__":
    main()
