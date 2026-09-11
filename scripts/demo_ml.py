"""Demo: ML recovery model training + SHAP explainability.

Shows the HistGradientBoosting model training on batch outcomes,
then explains individual predictions using SHAP values.

Usage:
    python scripts/demo_ml.py [--n 500] [--seed 42]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.recovery_model import get_model
from app.store import Store
from simulate.batch_generator import generate_batch


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text())
    cfg["simulation"]["batch_size"] = args.n

    # Generate and run batch
    print(f"[1/3] generating {args.n} synthetic cases ...")
    t_start = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)
    payments = generate_batch(args.n, t_start, seed=args.seed)
    print(f"      {len(payments)} cases generated")

    store = Store(Path("demo_ml.db"))
    from simulate.engine import run
    print("[2/3] running simulation ...")
    run(payments, cfg, store)
    print(f"      {len(store.all_cases())} cases processed")

    # Train model on batch outcomes
    model = get_model()
    actions_rows = store.actions_rows()
    trained = model.train(store.all_cases(), actions_rows)
    print(f"[3/3] model trained: {trained}")

    if trained:
        # Show SHAP explanations for a few cases
        print("\n--- SHAP Explanations (top 5 cases) ---")
        import random

        from app.models import ActionType
        random.seed(args.seed)

        cases_with_actions = []
        for case in store.all_cases():
            for row in actions_rows:
                if row.get("case_id") == case.case_id and row.get("status") == "executed":
                    try:
                        action_type = ActionType(row["action_type"])
                        cases_with_actions.append((case, action_type))
                        break
                    except (ValueError, KeyError):
                        continue

        if not cases_with_actions:
            print("  No executed actions found — skipping SHAP demo")
            store.close()
            Path("demo_ml.db").unlink(missing_ok=True)
            return

        sample = random.sample(cases_with_actions, min(5, len(cases_with_actions)))
        for case, action_type in sample:
            pred = model.predict(case, action_type, 0, datetime.now(timezone.utc).isoformat(), cfg)
            print(f"\n  Case {case.case_id}: {case.failure_class.value} / ₹{case.amount/100:.0f}")
            print(f"  Action: {action_type.value}")
            print(f"  P(recovery): {pred.probability:.1%} [{pred.confidence}]")
            print("  Top features:")
            for fname, fval in pred.top_features[:3]:
                direction = "↑" if fval > 0 else "↓"
                print(f"    {fname}: {fval:+.3f} {direction}")

        # Show model internals
        if hasattr(model._model, "feature_importances_"):
            print("\n--- Global Feature Importances (GBM impurity-based) ---")
            from app.recovery_model import _FEATURE_NAMES
            importances = model._model.feature_importances_
            for fname, imp in sorted(
                zip(_FEATURE_NAMES, importances, strict=False),
                key=lambda x: -x[1],
            ):
                print(f"  {fname}: {imp:.4f}")

        # Check if SHAP is available
        import importlib.util
        shap_spec = importlib.util.find_spec("shap")
        if shap_spec:
            print("\n--- SHAP Available: True ---")
        else:
            print("\n--- SHAP Available: False (pip install shap) ---")
    else:
        print("  Model did not train (insufficient data or missing sklearn)")

    store.close()
    Path("demo_ml.db").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
