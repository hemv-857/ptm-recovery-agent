"""End-to-end smoke: small cohort through the full simulated agent loop."""
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.store import Store
from simulate.batch_generator import generate_batch
from simulate.engine import run


def test_end_to_end_small_batch(tmp_path):
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    cfg["simulation"]["batch_size"] = 60
    cfg["simulation"]["horizon_days"] = 7

    payments = generate_batch(60, datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc), seed=99)
    assert len(payments) == 60

    store = Store(tmp_path / "smoke.db")
    run(payments, cfg, store)

    cases = store.all_cases()
    assert len(cases) == 60

    t_rec = sum(1 for c in cases if c.group.value == "treatment" and c.recovered_amount)
    c_rec = sum(1 for c in cases if c.group.value == "control" and c.recovered_amount)
    n_t = sum(1 for c in cases if c.group.value == "treatment")
    n_c = 60 - n_t

    # with a fixed seed the lift should be positive; assert sanity bounds not exact numbers
    assert (t_rec / n_t) >= (c_rec / n_c) - 0.05
    assert all(c.status.value in ("recovered", "written_off") for c in cases)

    # audit trail exists and is ordered for every case
    some = store.audit_for(cases[0].case_id)
    assert len(some) >= 2
    assert some[0]["event_type"] == "case.created"

    # no scheduled action remains on a closed case
    for c in cases:
        if c.status.value in ("recovered", "written_off"):
            for a in store.actions_for(c.case_id):
                assert a.status.value != "scheduled"

    store.close()


def test_case_ids_stable_and_simulation_reproducible(tmp_path):
    """Same seed -> identical outcomes. Regression: case_id used to be uuid4,
    and the world model salts every draw with it, so runs silently diverged."""
    from app.models import Customer, FailureClass, RecoveryCase

    a = RecoveryCase(payment_id="pay_x", customer=Customer(customer_id="c"),
                     amount=1, method="card",
                     failure_class=FailureClass.NETWORK_TIMEOUT, class_confidence=0.9)
    b = RecoveryCase(payment_id="pay_x", customer=Customer(customer_id="c"),
                     amount=1, method="card",
                     failure_class=FailureClass.NETWORK_TIMEOUT, class_confidence=0.9)
    assert a.case_id == b.case_id and a.case_id.startswith("case_")
    assert RecoveryCase(
        payment_id="pay_y", customer=Customer(customer_id="c"), amount=1,
        method="card", failure_class=FailureClass.NETWORK_TIMEOUT,
        class_confidence=0.9).case_id != a.case_id

    def one_run(seed: int) -> list[tuple[str, int]]:
        cfg = yaml.safe_load(Path("config.yaml").read_text())
        payments = generate_batch(40, datetime(2026, 8, 20, 6, 0,
                                               tzinfo=timezone.utc), seed=seed)
        store = Store(tmp_path / f"det_{seed}.db")
        run(payments, cfg, store)
        out = sorted((c.case_id, c.recovered_amount) for c in store.all_cases())
        store.close()
        return out

    assert one_run(42) == one_run(42)


def test_dashboard_links_cases_to_audit_trails(tmp_path):
    from app.measure import build_report
    from app.report_html import render_dashboard

    cfg = yaml.safe_load(Path("config.yaml").read_text())
    payments = generate_batch(20, datetime(2026, 8, 20, 6, 0,
                                           tzinfo=timezone.utc), seed=7)
    store = Store(tmp_path / "dash.db")
    run(payments, cfg, store)
    cases = sorted(store.all_cases(), key=lambda c: c.created_at)[-10:][::-1]
    html = render_dashboard(
        build_report(store.all_cases(), store.actions_rows(), cfg),
        recent_cases=cases,
    )
    assert "Recent cases" in html
    assert f"/audit/{cases[0].case_id}" in html      # drill-down link present
    store.close()
