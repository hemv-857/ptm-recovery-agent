#!/usr/bin/env python
"""Standing claims-consistency audit.

Verifies that every judge-facing claim traces to ground truth, mechanically:

  1. LIVE REPLAY   - a fresh seed-42 batch must byte-match report.json. This is
                     the strongest possible check: it proves the "reproducible
                     with seed 42" claim every surface makes, and catches the
                     case where code has drifted past the committed report.
  2. DOCS          - the human-readable figures in README.md / CLAIM_MATRIX.md
                     must equal the same numbers derived from report.json.
  3. DECK          - the Canva submission deck's text snapshot
                     (report_deck_snapshot.txt) must carry the same figures,
                     and must NOT contain any figure from the stale
                     pre-regeneration batch. Refresh the snapshot after
                     editing the deck in Canva (instructions in its header).
  4. FORMATTER     - fmt_rupees() must render the paise source of truth into
                     exactly the strings the deck/docs quote (the 100x unit bug
                     regressions).

Run:  .venv/bin/python scripts/audit_claims.py        # full audit (~6s)
      .venv/bin/python scripts/audit_claims.py --fast # docs+deck+formatter only

Exit code 0 = every claim consistent; nonzero with a printed punch list of the
exact stale strings otherwise. No arguments, no config: ground truth is always
report.json next to this script.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.measure import fmt_rupees  # noqa: E402

REPORT = ROOT / "report.json"
README = ROOT / "README.md"
MATRIX = ROOT / "CLAIM_MATRIX.md"
DECK_SNAPSHOT = ROOT / "report_deck_snapshot.txt"

# Canonical batch anchor: must match scripts/run_batch.py exactly.
T_START = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)


def _load_report() -> dict:
    return json.loads(REPORT.read_text()).get("report", json.loads(REPORT.read_text()))


def _replay_report() -> dict:
    """Fresh seed-42 batch -> report, using only documented commands."""
    import yaml

    from simulate.batch_generator import generate_batch
    from simulate.engine import run as engine_run
    from app.store import Store
    from app.measure import build_report

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    payments = generate_batch(cfg["simulation"]["batch_size"], T_START, seed=42)
    store = Store(tempfile.mktemp(suffix=".db"))
    try:
        engine_run(payments, cfg, store)
        return build_report(store.all_cases(), store.actions_rows(), cfg)
    finally:
        store.close()


def _fmt(paise: float) -> str:
    return fmt_rupees(paise)


def _fresh() -> dict:
    return json.loads(json.dumps(_load_report(), default=str))  # deep copy


# --------------------------------------------------------------------------
# 1. Live replay
# --------------------------------------------------------------------------

def check_replay(failures: list[str]) -> None:
    fresh = _replay_report()
    disk = _fresh()

    def walk(path: str, a, b) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                walk(f"{path}.{k}", a.get(k), b.get(k))
        elif a != b:
            failures.append(
                f"replay: report.json is stale at {path}\n"
                f"    disk  = {a!r}\n"
                f"    fresh = {b!r}\n"
                f"    fix: .venv/bin/python scripts/run_batch.py --seed 42"
            )

    walk("report", disk, fresh)


# --------------------------------------------------------------------------
# 2. Docs vs ground truth
# --------------------------------------------------------------------------

def check_docs(failures: list[str]) -> None:
    rep = _fresh()
    h, cost, pr = rep["headline"], rep["cost"], rep["promises"]

    expected = [
        ("README.md", f"Rs {h['incremental_money_paise'] / 1e7:.2f} Lakh incremental recovery"),
        ("README.md", f"**Rs {h['incremental_money_paise'] / 1e7:.2f} Lakh**"),
        ("README.md", f"Rs {rep['promises']['money_via_promises_paise'] / 1e7:.2f} Lakh recovered through them"),
        ("README.md", f"Rs {rep['batch']['amount_at_risk_paise'] / 1e7:.2f} Lakh across "
                      f"{rep['batch']['cases']:,} cases"),
        ("README.md", f"{h['recovery_rate_treatment'] * 100:.1f}% treatment vs "
                      f"{h['recovery_rate_control'] * 100:.1f}% control"),
        ("README.md", f"+{h['incremental_recovery_pp']:.1f} pp"),
        ("README.md", f"[+{h['incremental_recovery_ci95_pp'][0]:.1f}, +{h['incremental_recovery_ci95_pp'][1]:.1f}]"),
        ("README.md", f"{pr['received']} captured"),
        ("README.md", f"{pr['keep_rate'] * 100:.0f}% keep rate"),
        ("README.md", f"{cost['contacts_executed']:,} interventions executed"),
        ("README.md", f"{cost['redundant_contact_share'] * 100:.0f}% — reported honestly"),
        ("README.md", f"{cost['opt_outs']}"),
        ("CLAIM_MATRIX.md", f"**{h['recovery_rate_treatment'] * 100:.1f}% treatment vs "
                            f"{h['recovery_rate_control'] * 100:.1f}% control recovery**"),
        ("CLAIM_MATRIX.md", f"**+{h['incremental_recovery_pp']:.1f}pp incremental lift, "
                            f"95% CI [+{h['incremental_recovery_ci95_pp'][0]:.1f}, "
                            f"+{h['incremental_recovery_ci95_pp'][1]:.1f}]**"),
        ("CLAIM_MATRIX.md", f"**₹{h['incremental_money_paise'] / 1e7:.2f} Lakh incremental money recovered**"),
        ("CLAIM_MATRIX.md", f"(0.{int(h['recovery_rate_treatment'] * 10000)} / 0.{int(h['recovery_rate_control'] * 10000)})"),
        ("CLAIM_MATRIX.md", f"({h['incremental_recovery_pp']:.2f}, CI [{h['incremental_recovery_ci95_pp'][0]:.2f}, "
                            f"{h['incremental_recovery_ci95_pp'][1]:.2f}])"),
        ("CLAIM_MATRIX.md", f"({h['incremental_money_paise']:,} paise = ₹{h['incremental_money_paise'] / 1e7:.2f} L)"),
        ("CLAIM_MATRIX.md", f"**{cost['contacts_executed']:,} interventions executed**"),
    ]
    for fname, needle in expected:
        text = (ROOT / fname).read_text()
        if needle not in text:
            failures.append(f"docs: {fname} missing expected claim: {needle!r}")


# --------------------------------------------------------------------------
# 3. Deck snapshot vs ground truth
# --------------------------------------------------------------------------

# Figures from the stale pre-regeneration batch that must never reappear on
# the deck. Substring match, checked against the whole snapshot text.
DECK_STALE_PATTERNS = [
    "49.8pp",            # old lift (now +49.0)
    "67.72",             # old incremental money (now 66.58 L)
    "20.8% control",     # old control rate (now 21.7%)
    "[45.8",             # old CI lower bound (now 44.9)
    "45.8\u201353.6",        # old CI pair
    "4,857",             # old contact count (now 3,000)
    "\u20b91.13",            # old cost/recovery (now \u20b90.69)
    "\u20b9113",             # old mislabelled cost/recovery
    "713 sends",         # old cap-block count (now 273)
    "24 policy blocks",  # old opt-out block count (now 19)
    "\u20b9788",             # old total spend (now \u20b9472)
    "+67.0pp",           # old INVOICE_OVERDUE lift (now +56.5)
]

# Deck presentation order for the channel-spend line.
DECK_CHANNELS = [("whatsapp", "WhatsApp"), ("voice", "voice"), ("sms", "SMS"), ("email", "email")]


def _deck_text() -> str:
    raw = DECK_SNAPSHOT.read_text()
    m = re.search(r"---DECK-START---\n(.*)\n---DECK-END---", raw, re.S)
    if not m:
        raise SystemExit(f"{DECK_SNAPSHOT.name}: missing ---DECK-START---/---DECK-END--- markers")
    return m.group(1)


def check_deck(failures: list[str], text: str | None = None) -> None:
    """Audit the deck's text snapshot against report.json.

    `text` overrides the snapshot file (used by tests to exercise the negative
    path against synthetic stale content).
    """
    deck = text if text is not None else _deck_text()
    rep = _fresh()
    h, cost, pr = rep["headline"], rep["cost"], rep["promises"]
    ci = h["incremental_recovery_ci95_pp"]

    def rupees(paise: int) -> str:
        return f"\u20b9{paise // 100}"

    expected = [
        # Slide 2 — recovery-rate analysis
        f"Treatment recovered {h['recovery_rate_treatment'] * 100:.1f}% vs "
        f"{h['recovery_rate_control'] * 100:.1f}% control \u2014 a +{h['incremental_recovery_pp']:.1f}pp "
        f"incremental lift (95% CI [{ci[0]:.1f}\u2013{ci[1]:.1f}pp]), worth "
        f"\u20b9{h['incremental_money_paise'] / 1e7:.2f} Lakh across "
        f"{h['incremental_recoveries_est']} net incremental recoveries.",
        f"Naive contact-all baseline: just {h['naive_recovery_rate'] * 100:.1f}%.",
        # Slide 5 — compliance numbers
        f"blocked {rep['policy_transparency']['blocked_actions']['attempt_cap_reached']} sends in the pilot",
        f"plus {rep['policy_transparency']['blocked_actions']['customer_opted_out']} policy blocks for opt-out violations",
        f"{cost['opt_outs']} customers opted out mid-campaign",
        # Slide 6 — headline outcome tiles
        f"\u20b9{h['incremental_money_paise'] / 1e7:.2f} L",
        f"+{h['incremental_recovery_pp']:.1f}pp lift vs randomized control \u00b7 "
        f"{h['incremental_recoveries_est']} net incremental recoveries \u00b7 "
        f"\u20b9{cost['cost_per_incremental_recovery_paise'] / 100:.2f} per recovery \u00b7 "
        f"95% CI [{ci[0]:.1f}\u2013{ci[1]:.1f}pp]",
        f"vs {h['recovery_rate_control'] * 100:.1f}% in a randomized control group (n=600)",
        # Slide 7 — per-class top lifts, order and values derived
        f"All {len(rep['per_class'])} failure classes beat control \u2014 biggest lifts: "
        + ", ".join(f"{k} +{v['lift_pp']:.1f}pp" for k, v in
                    sorted(rep["per_class"].items(), key=lambda kv: -kv[1]["lift_pp"])[:3]),
        # Slide 8 — channel spends and promises
        " \u00b7 ".join(f"{label} {rupees(cost['cost_by_channel_paise'][ch])}"
                       for ch, label in DECK_CHANNELS if ch in cost["cost_by_channel_paise"]),
        f"total {rupees(cost['spend_paise'])} across {cost['contacts_executed']:,} contacts",
        f"{pr['received']} spoken promises, {pr['kept']} kept = {pr['keep_rate'] * 100:.1f}% keep rate",
        # Slide 10 — the bridge command judges run
        "scripts/run_batch.py --seed 42 \u2192 report.json",
    ]
    for needle in expected:
        if needle not in deck:
            failures.append(f"deck: missing/stale claim: {needle!r}\n"
                            f"    fix: update the deck in Canva, then refresh report_deck_snapshot.txt")

    for pattern in DECK_STALE_PATTERNS:
        if pattern in deck:
            failures.append(f"deck: stale pre-regeneration figure present: {pattern!r}\n"
                            f"    fix: update the deck in Canva, then refresh report_deck_snapshot.txt")


# --------------------------------------------------------------------------
# 4. Formatter renders ground truth into deck strings
# --------------------------------------------------------------------------

def check_formatter(failures: list[str]) -> None:
    rep = _fresh()
    h, cost = rep["headline"], rep["cost"]

    expected = {
        h["incremental_money_paise"]: f"₹{h['incremental_money_paise'] / 1e7:.2f} L",
        rep["batch"]["amount_at_risk_paise"]: f"₹{rep['batch']['amount_at_risk_paise'] / 1e9:.2f} Cr",
        cost["spend_paise"]: f"₹{cost['spend_paise'] / 100:,.0f}",
        cost["cost_per_incremental_recovery_paise"]: f"₹{cost['cost_per_incremental_recovery_paise'] / 100:.2f}",
        rep["promises"]["money_via_promises_paise"]: f"₹{rep['promises']['money_via_promises_paise'] / 1e7:.2f} L",
    }
    for paise, want in expected.items():
        got = _fmt(paise)
        if got != want:
            failures.append(f"formatter: fmt_rupees({paise}) = {got!r}, deck/docs expect {want!r}")


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fast", action="store_true",
                    help="skip the live seed-42 replay (~6s)")
    args = ap.parse_args()

    failures: list[str] = []
    if not args.fast:
        print("[1/4] live seed-42 replay vs report.json ...")
        check_replay(failures)
        print("      ok" if not failures else "      STALE")
    else:
        print("[1/4] live replay skipped (--fast)")

    print("[2/4] doc figures vs report.json ...")
    n0 = len(failures)
    check_docs(failures)
    print("      ok" if len(failures) == n0 else "      MISMATCH")

    print("[3/4] deck snapshot vs report.json ...")
    n0 = len(failures)
    check_deck(failures)
    print("      ok" if len(failures) == n0 else "      MISMATCH")

    print("[4/4] formatter vs deck strings ...")
    n0 = len(failures)
    check_formatter(failures)
    print("      ok" if len(failures) == n0 else "      MISMATCH")

    if failures:
        print(f"\n{len(failures)} claim(s) inconsistent:\n")
        for f in failures:
            print(f"  - {f}\n")
        return 1
    print("\nAll claims consistent with report.json (seed-42).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
