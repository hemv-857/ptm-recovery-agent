"""Multi-seed evaluation: runs the full agent pipeline across N seeds,
reports per-seed breakdown and aggregate statistics.

Usage:
    .venv/bin/python scripts/evaluate.py
    .venv/bin/python scripts/evaluate.py --seeds 5 --batch-size 1000
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

from app.measure import bootstrap_lift_ci, build_report, classification_eval
from app.store import Store
from simulate.batch_generator import generate_batch
from simulate.engine import run


def run_seed(seed: int, batch_size: int, cfg: dict) -> dict:
    """Run one seed and return the report."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = Store(Path(db_path))
    payments = generate_batch(
        batch_size,
        datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc),
        seed=seed,
    )
    run(payments, cfg, store)
    report = build_report(store.all_cases(), store.actions_rows(), cfg)
    cls_eval = classification_eval(payments)
    store.close()

    # cleanup
    Path(db_path).unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)

    return {
        "seed": seed,
        "batch_size": batch_size,
        "cases": report["batch"]["cases"],
        "treatment_n": report["batch"]["treatment_n"],
        "control_n": report["batch"]["control_n"],
        "recovery_rate_treatment": report["headline"]["recovery_rate_treatment"],
        "recovery_rate_control": report["headline"]["recovery_rate_control"],
        "incremental_recovery_pp": report["headline"]["incremental_recovery_pp"],
        "ci95_pp": report["headline"]["incremental_recovery_ci95_pp"],
        "incremental_money_paise": report["headline"]["incremental_money_paise"],
        "naive_recovery_rate": report["headline"]["naive_recovery_rate"],
        "spend_paise": report["cost"]["spend_paise"],
        "contacts_executed": report["cost"]["contacts_executed"],
        "classification_accuracy": cls_eval["accuracy"],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Multi-seed evaluation")
    ap.add_argument("--seeds", type=int, default=5, help="Number of seeds")
    ap.add_argument("--batch-size", type=int, default=1000, help="Cases per seed")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text())
    seeds = [42 + i * 7 for i in range(args.seeds)]  # 42, 49, 56, 63, 70

    print(f"Running {args.seeds} seeds x {args.batch_size} cases each\n")
    results = []
    for seed in seeds:
        print(f"  seed {seed} ...", end=" ", flush=True)
        r = run_seed(seed, args.batch_size, cfg)
        results.append(r)
        print(f"lift {r['incremental_recovery_pp']:+.1f}pp "
              f"[{r['ci95_pp'][0]:+.1f}, {r['ci95_pp'][1]:+.1f}]")

    # aggregate
    lifts = [r["incremental_recovery_pp"] for r in results]
    [r["ci95_pp"] for r in results]
    mean_lift = sum(lifts) / len(lifts)
    std_lift = (sum((x - mean_lift) ** 2 for x in lifts) / len(lifts)) ** 0.5

    # aggregate CI: use pooled bootstrap
    all_t, all_c = [], []
    for r in results:
        n_t = r["treatment_n"]
        n_c = r["control_n"]
        rt = r["recovery_rate_treatment"]
        rc = r["recovery_rate_control"]
        # reconstruct binary outcomes from rates
        all_t.extend([1] * int(rt * n_t) + [0] * (n_t - int(rt * n_t)))
        all_c.extend([1] * int(rc * n_c) + [0] * (n_c - int(rc * n_c)))
    pooled_lo, pooled_hi = bootstrap_lift_ci(all_t, all_c, reps=5000, seed=7)

    aggregate = {
        "seeds": args.seeds,
        "batch_size_per_seed": args.batch_size,
        "total_cases": sum(r["cases"] for r in results),
        "mean_lift_pp": round(mean_lift, 2),
        "std_lift_pp": round(std_lift, 2),
        "min_lift_pp": round(min(lifts), 2),
        "max_lift_pp": round(max(lifts), 2),
        "pooled_ci95_pp": [round(pooled_lo, 2), round(pooled_hi, 2)],
        "all_seeds_positive": all(x > 0 for x in lifts),
        "mean_classification_accuracy": round(
            sum(r["classification_accuracy"] for r in results) / len(results), 4
        ),
    }

    out = {"per_seed": results, "aggregate": aggregate}

    # write to file
    out_path = Path("evaluation_report.json")
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\n{'=' * 60}")
    print("AGGREGATE RESULTS")
    print(f"{'=' * 60}")
    print(f"  Seeds:                  {aggregate['seeds']}")
    print(f"  Cases per seed:         {aggregate['batch_size_per_seed']}")
    print(f"  Total cases:            {aggregate['total_cases']}")
    print(f"  Mean lift:              {aggregate['mean_lift_pp']:+.2f} pp")
    print(f"  Std dev:                {aggregate['std_lift_pp']:.2f} pp")
    print(
        f"  Range:                  [{aggregate['min_lift_pp']:+.1f},"
        f" {aggregate['max_lift_pp']:+.1f}] pp"
    )
    ci0 = aggregate['pooled_ci95_pp'][0]
    ci1 = aggregate['pooled_ci95_pp'][1]
    print(f"  Pooled 95% CI:          [{ci0:+.1f}, {ci1:+.1f}] pp")
    print(f"  All seeds positive:     {aggregate['all_seeds_positive']}")
    print(f"  Classification accuracy:{aggregate['mean_classification_accuracy']:.1%}")
    print(f"\n  Written to {out_path}")


if __name__ == "__main__":
    main()
