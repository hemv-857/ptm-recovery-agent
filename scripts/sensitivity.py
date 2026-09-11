"""Sensitivity analysis: vary the recovery probability by +/-20% and measure
impact on headline lift. Shows the result is not fragile to world-model params.

Usage: .venv/bin/python scripts/sensitivity.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.measure import build_report
from app.store import Store
from simulate.batch_generator import generate_batch
from simulate.engine import run

SEED = 42
BATCH = 1000


def run_with_modifier(modifier: float, cfg: dict) -> dict:
    """Run simulation with world-model probabilities scaled by modifier."""
    import copy
    c = copy.deepcopy(cfg)
    bp = c.get("world", {}).get("base_pay_probability", {})
    for k in bp:
        bp[k] = min(bp[k] * modifier, 0.95)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = Store(Path(db_path))
    payments = generate_batch(
        BATCH, datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc), seed=SEED
    )
    run(payments, c, store)
    report = build_report(store.all_cases(), store.actions_rows(), c)
    store.close()
    Path(db_path).unlink(missing_ok=True)
    for sfx in ("-wal", "-shm"):
        Path(str(db_path) + sfx).unlink(missing_ok=True)

    return {
        "modifier": modifier,
        "lift_pp": report["headline"]["incremental_recovery_pp"],
        "ci95_pp": report["headline"]["incremental_recovery_ci95_pp"],
        "treatment_rate": report["headline"]["recovery_rate_treatment"],
        "control_rate": report["headline"]["recovery_rate_control"],
        "naive_rate": report["headline"]["naive_recovery_rate"],
        "incremental_money_paise": report["headline"]["incremental_money_paise"],
    }


def main() -> None:
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    modifiers = [0.8, 0.9, 1.0, 1.1, 1.2]
    labels = ["-20%", "-10%", "baseline", "+10%", "+20%"]

    print("Sensitivity: varying base_pay_probability by ±20%\n")
    results = []
    for mod, label in zip(modifiers, labels, strict=False):
        r = run_with_modifier(mod, cfg)
        results.append(r)
        print(f"  {label:>10}  lift {r['lift_pp']:+.1f}pp "
              f"[{r['ci95_pp'][0]:+.1f}, {r['ci95_pp'][1]:+.1f}]")

    # check robustness: all lifts positive?
    all_positive = all(r["lift_pp"] > 0 for r in results)
    range_lift = max(r["lift_pp"] for r in results) - min(r["lift_pp"] for r in results)

    out = {
        "description": "Sensitivity analysis: base_pay_probability scaled by modifier",
        "seed": SEED,
        "batch_size": BATCH,
        "results": results,
        "robustness": {
            "all_lifts_positive": all_positive,
            "lift_range_pp": round(range_lift, 1),
            "conclusion": (
                "Robust: lift remains positive across all tested parameter variations."
                if all_positive
                else "Fragile: lift becomes negative under some parameter settings."
            ),
        },
    }

    Path("sensitivity_report.json").write_text(json.dumps(out, indent=2))
    print(f"\n  All positive: {all_positive}")
    print(f"  Lift range:   {range_lift:.1f}pp")
    print("  Written to sensitivity_report.json")


if __name__ == "__main__":
    main()
