"""Claims-consistency: every judge-facing figure must trace to report.json.

Wraps scripts/audit_claims.py as tests. The full live seed-42 replay (~6s) is
marked slow; run it with  pytest -m slow  or `scripts/audit_claims.py`.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _run_audit(*args: str) -> None:
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "audit_claims.py"), *args],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert r.returncode == 0, f"claims audit failed:\n{r.stdout}\n{r.stderr}"


def test_docs_match_report() -> None:
    _run_audit("--fast")


def test_deck_snapshot_matches_report() -> None:
    """The Canva deck's captured text must carry the fresh seed-42 figures."""
    import audit_claims

    failures: list[str] = []
    audit_claims.check_deck(failures)
    assert not failures, "deck claims inconsistent:\n" + "\n".join(failures)


def test_deck_audit_rejects_stale_figures() -> None:
    """Negative path: a deck quoting the old pre-regeneration batch must fail."""
    import audit_claims

    stale_deck = (
        "Treatment recovered 70.6% vs 20.8% control \u2014 a +49.8pp incremental lift "
        "(95% CI [45.8\u201353.6pp]), worth \u20b967.72 Lakh across 697 net incremental recoveries. "
        "caps blocked 713 sends in the pilot. total \u20b9788 across 4,857 contacts."
    )
    failures: list[str] = []
    audit_claims.check_deck(failures, text=stale_deck)
    joined = "\n".join(failures)
    # every stale figure must be named by the audit
    for token in ("49.8pp", "67.72", "4,857", "713 sends", "\u20b9788"):
        assert token in joined, f"audit failed to flag {token!r}"
    # and the correct derived claims must be reported missing
    assert "missing/stale claim" in joined


def test_deck_audit_rejects_drifted_report_figures() -> None:
    """Negative path: if report.json regenerates with new numbers, the deck
    snapshot stops matching and the audit must say exactly which claim drifted."""
    import audit_claims

    real = audit_claims._deck_text()
    drifted = real.replace("+49.0pp", "+51.2pp", 1)
    assert drifted != real
    failures: list[str] = []
    audit_claims.check_deck(failures, text=drifted)
    assert failures, "audit accepted a drifted deck"
    # the drifted composite slide-2 claim must be the one named
    assert any("Treatment recovered" in f and "incremental lift" in f for f in failures)
    # and only claims touching the edited figure should fail, not the whole deck
    assert len(failures) < 5


@pytest.mark.slow
def test_seed42_replay_matches_report() -> None:
    _run_audit()  # full audit incl. the live replay
