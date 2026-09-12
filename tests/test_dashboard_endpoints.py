"""Tests for the Phase 2/3 endpoints: /dashboard/summary, /cases/page,
/analytics/portfolio, /analytics/heatmap, and the /report/print page."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.agent import ingest_failure, plan_and_schedule
from app.main import app
from app.models import Customer, FailedPayment, FailureClass


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOVERY_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("RECOVERY_CONFIG", "config.yaml")
    # Import-time singletons may already exist from other tests; that's fine.
    return TestClient(app)


def _seed_case(tmp_path, amount: int, failure: FailureClass = FailureClass.INSUFFICIENT_FUNDS):
    store = __import__("app.store", fromlist=["Store"]).Store(tmp_path / "t.db")
    cfg = __import__("yaml").safe_load(open("config.yaml"))
    fp = FailedPayment(
        payment_id=f"pay_{failure.value.lower()}_{amount}",
        order_id=f"ord_{failure.value.lower()}_{amount}",
        amount=amount,
        method="upi",
        raw_error_code="insufficient_funds",
        error_description="insufficient funds",
        customer=Customer(customer_id="c1", name="Ravi", phone="+919800000000"),
        source="simulated",
    )
    case = ingest_failure(fp, store, cfg)
    plan_and_schedule(case, cfg, datetime.now(timezone.utc) + timedelta(hours=1), store)
    return case


def test_dashboard_summary_aggregates(client, tmp_path):
    _seed_case(tmp_path, 50_000)
    r = client.get("/dashboard/summary?limit=10")
    assert r.status_code == 200
    d = r.json()
    for key in ("report", "cases", "engine"):
        assert key in d, f"missing section: {key}"
    assert isinstance(d["cases"], list)
    # fail-soft sections must never 500 the aggregate
    assert "engine" in d and isinstance(d["engine"], dict)


def test_dashboard_summary_limit_bounds(client):
    assert client.get("/dashboard/summary?limit=0").status_code == 200
    assert client.get("/dashboard/summary?limit=999").status_code == 200


def test_cases_page_pagination_and_filters(client, tmp_path):
    _seed_case(tmp_path, 50_000)
    _seed_case(tmp_path, 75_000)

    r = client.get("/cases/page?limit=1&offset=0")
    assert r.status_code == 200
    d = r.json()
    assert d["limit"] == 1 and d["offset"] == 0
    assert len(d["cases"]) == 1
    assert d["total"] >= 2

    # filter
    r2 = client.get("/cases/page", params={"failure_class": "INSUFFICIENT_FUNDS"})
    assert r2.status_code == 200
    assert all(c["failure_class"] == "INSUFFICIENT_FUNDS" for c in r2.json()["cases"])

    # search (payment_id prefix)
    r3 = client.get("/cases/page", params={"q": "pay_insufficient"})
    assert r3.status_code == 200
    assert r3.json()["total"] >= 1

    # sort validation
    assert client.get("/cases/page", params={"sort": "bogus"}).status_code == 422
    ok = client.get("/cases/page", params={"sort": "amount_desc"})
    assert ok.status_code == 200
    amounts = [c["amount_paise"] for c in ok.json()["cases"]]
    assert amounts == sorted(amounts, reverse=True)


def test_portfolio_endpoint_shape(client, tmp_path):
    _seed_case(tmp_path, 500_000)  # above the ₹25k auto-action cap
    r = client.get("/analytics/portfolio?capacity_hours=2")
    assert r.status_code == 200
    d = r.json()
    assert "knapsack" in d and "greedy" in d and "pending" in d
    assert client.get("/analytics/portfolio?capacity_hours=0").status_code == 422
    assert client.get("/analytics/portfolio?capacity_hours=99").status_code == 422


def test_heatmap_shape(client, tmp_path):
    _seed_case(tmp_path, 50_000)
    r = client.get("/analytics/heatmap")
    assert r.status_code == 200
    d = r.json()
    assert {"channels", "classes", "matrix"} <= set(d)
    assert isinstance(d["matrix"], list)


def test_fmt_rupees_scales_by_magnitude():
    """Guard against the 100x unit bug: paise must map to the right unit."""
    from app.measure import fmt_rupees

    # 677,171,819 paise = Rs 67,71,718.19 = Rs 67.72 Lakh (NOT Cr)
    assert fmt_rupees(677_171_819) == "₹67.72 L"
    # 1e9 paise = Rs 1 crore
    assert fmt_rupees(1_000_000_000) == "₹1.00 Cr"
    # 78800 paise = Rs 788 (plain rupees, no L/Cr suffix)
    assert fmt_rupees(78_800) == "₹788"
    # 113 paise = Rs 1.13 (deck/docs claim this exact figure)
    assert fmt_rupees(113) == "₹1.13"


def test_report_print_renders_html(client, tmp_path):
    _seed_case(tmp_path, 50_000)
    r = client.get("/report/print")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Incremental Lift Report" in body
    assert "Honest costs" in body
    assert "Audit integrity" in body
    # Money renders via fmt_rupees (unit-correct), not fixed /1e5 division:
    # the hero must not contain a raw number followed by "L incremental"
    # (the stale mislabel), and no double rupee symbol from template + fmt.
    assert "₹67.72 L incremental" not in body
    assert body.count("₹") == body.count("&#8377;") or "&#8377;₹" not in body
