"""Ops notifications (Slack), multi-tenant DB routing, spend-breakdown report."""
import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest
import yaml


@pytest.fixture()
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("RECOVERY_DB", str(tmp_path / "multi.db"))
    monkeypatch.setenv("RECOVERY_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("PAYTM_WEBHOOK_SECRET", "whsec_test")
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(yaml.safe_load(open("config.yaml"))))
    from fastapi.testclient import TestClient

    from app import webhook as _webhook_mod
    monkeypatch.setattr(_webhook_mod, "WEBHOOK_SECRET", "whsec_test")
    from app import main as appmod
    monkeypatch.setattr(appmod.client, "webhook_secret", "whsec_test")
    return TestClient(appmod.app), monkeypatch, tmp_path


def _failed_event(payment_id="pay_mt0001"):
    """Paytm-format TXN_FAILED webhook payload."""
    return {
        "STATUS": "TXN_FAILED",
        "ORDERID": "order_x",
        "TXNID": payment_id,
        "TXNAMOUNT": "3000.00",
        "PAYMENTMODE": "card",
        "RESPCODE": "810",
        "RESPMSG": "Payment declined due to insufficient funds",
        "CUST_ID": "cust_mt",
        "MOBILE_NO": "+919999900001",
    }


def _signed_post(client, event, extra_headers=None):
    body = json.dumps(event).encode()
    sig = hmac.new(b"whsec_test", body, hashlib.sha256).hexdigest()
    headers = {"X-paytm-Signature": sig, **(extra_headers or {})}
    return client.post("/webhooks/paytm", content=body, headers=headers)


def test_merchant_header_routes_to_isolated_dbs(api):
    client, _, tmp_path = api
    assert _signed_post(client, _failed_event("pay_mt_a")).status_code == 200
    assert _signed_post(client, _failed_event("pay_mt_b"),
                        {"X-Merchant-Id": "acme"}).status_code == 200

    default_db = tmp_path / "multi.db"
    acme_db = tmp_path / "multi_acme.db"
    assert default_db.exists() and acme_db.exists()

    from app.store import Store
    d, a = Store(default_db), Store(acme_db)
    assert [c.payment_id for c in d.all_cases()] == ["pay_mt_a"]
    assert [c.payment_id for c in a.all_cases()] == ["pay_mt_b"]
    d.close()
    a.close()


def test_merchant_header_is_sanitized(api):
    """Path traversal via the header must not escape the data dir."""
    client, _, tmp_path = api
    r = _signed_post(client, _failed_event("pay_evil1"),
                     {"X-Merchant-Id": "../../etc/passwd"})
    assert r.status_code == 200
    names = sorted(p.name for p in tmp_path.glob("multi*"))
    # traversal stripped ("../../etc/passwd" -> "etcpasswd"); nothing escaped tmp_path
    assert "multi_etcpasswd.db" in names
    assert not any(".." in n or "/" in n for n in names)


# ---- Slack notifier ---------------------------------------------------------
def test_notifier_fires_on_escalation_and_survives_errors(monkeypatch, tmp_path):
    from app import notifier
    from app.agent import write_off
    from app.models import CaseStatus, Customer, FailureClass, Group, RecoveryCase
    from app.store import Store

    calls = []
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "http://slack.local/hook")

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json["text"]))
        raise RuntimeError("slack down")          # alerting must never break recovery

    monkeypatch.setattr(notifier.httpx, "post", fake_post)

    store = Store(tmp_path / "n.db")
    case = RecoveryCase(
        payment_id="p_slack", customer=Customer(customer_id="c"), amount=5_000_000,
        method="card", failure_class=FailureClass.INVOICE_OVERDUE,
        group=Group.TREATMENT, class_confidence=0.9,
    )
    store.upsert_case(case)
    write_off(case, "escalated_to_human_finance_ops", store)

    assert len(calls) == 1 and "escalated_to_human_finance_ops" in calls[0][1]
    assert case.status is CaseStatus.WRITTEN_OFF   # state change survived slack dying
    assert case.case_id in calls[0][1]

    monkeypatch.delenv("SLACK_WEBHOOK_URL")
    calls.clear()
    write_off(case, "customer_opted_out", store)
    assert calls == []                             # unset URL = silent no-op


# ---- spend breakdown --------------------------------------------------------
def test_report_has_cost_by_channel(tmp_path):
    from app.measure import build_report
    from app.store import Store
    from simulate.batch_generator import generate_batch
    from simulate.engine import run

    cfg = yaml.safe_load(open("config.yaml"))
    payments = generate_batch(30, datetime(2026, 8, 20, 6, 0,
                                           tzinfo=timezone.utc), seed=3)
    store = Store(tmp_path / "c.db")
    run(payments, cfg, store)
    rep = build_report(store.all_cases(), store.actions_rows(), cfg)
    by_ch = rep["cost"]["cost_by_channel_paise"]
    assert sum(by_ch.values()) == rep["cost"]["spend_paise"]
    assert all(v > 0 for v in by_ch.values())
    store.close()


def test_dashboard_renders_spend_pie(tmp_path):
    from app.measure import build_report
    from app.report_html import render_dashboard
    from app.store import Store
    from simulate.batch_generator import generate_batch
    from simulate.engine import run

    cfg = yaml.safe_load(open("config.yaml"))
    payments = generate_batch(30, datetime(2026, 8, 20, 6, 0,
                                           tzinfo=timezone.utc), seed=3)
    store = Store(tmp_path / "pie.db")
    run(payments, cfg, store)
    html = render_dashboard(build_report(store.all_cases(), store.actions_rows(), cfg))
    assert "<svg" in html and "whatsapp" in html   # pie present with channel labels
    store.close()
