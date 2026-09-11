"""Live-mode plumbing tests — everything verifiable WITHOUT real keys:
.env loading, request shaping over a mocked transport, signed webhooks through
the real FastAPI receiver, idempotent recovery, API-error containment."""
import hashlib
import hmac
import json

import httpx
import pytest


# ---- .env loader ---------------------------------------------------------
def test_dotenv_loads_and_respects_existing_env(monkeypatch, tmp_path):
    from app.dotenv import load_env
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nFOO_BAR=hello\nQUOTED=\"wrapped\"\nEMPTY=\nEXISTING=keepme\n"
    )
    monkeypatch.setenv("EXISTING", "already")
    loaded = load_env(env_file)
    assert loaded == {"FOO_BAR": "hello", "QUOTED": "wrapped"}
    import os
    assert os.environ["FOO_BAR"] == "hello"
    assert os.environ["EXISTING"] == "already"      # real environment wins
    assert "EMPTY" not in loaded


# ---- client over a mocked transport --------------------------------------
def test_live_client_sends_correct_payload(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization", "")
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "plink_1", "short_url": "https://paytm.me/sim-demo"})

    transport = httpx.MockTransport(handler)
    from app.payment_processor import PaytmProcessor
    c = PaytmProcessor(transport=transport)
    c.key_id, c.key_secret, c.live = "paytm_test_123", "secret", True   # simulate keys

    out = c.create_payment_link(
        amount=250000, customer_id="cust_1", name="A B", email="a@b.com",
        phone="+919999911111", description="Recovery", reference_id="case_x",
    )
    assert "paytm.me" in out["short_url"]
    assert out["id"].startswith("paytm_")
    assert captured["url"].endswith("/order/create")
    body = captured["json"]
    assert float(body["txnAmount"]) == 2500.00
    assert body["orderId"] == "case_x"


def test_simulated_mode_when_no_keys():
    from app.payment_processor import PaytmProcessor
    c = PaytmProcessor()                             # no keys in CI env
    if c.live:
        pytest.skip("real keys present in environment")
    out = c.create_payment_link(amount=1, customer_id="c", name="n", email="e",
                                phone="p", description="d", reference_id="case_y")
    assert out["simulated"] is True and "case_y" in out["short_url"]
    assert c.fetch_payment("pay_any") is None


def test_signature_scheme_matches_paytm():
    from app.payment_processor import PaytmProcessor
    body = b'{"event":"test"}'
    expected = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert PaytmProcessor.sign(body, "s3cret") == expected


# ---- signed webhooks through the real receiver ----------------------------
@pytest.fixture()
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("RECOVERY_DB", str(tmp_path / "webhook.db"))
    monkeypatch.setattr("pathlib.Path", __import__("pathlib").Path)  # no-op guard
    import yaml
    # minimal config for _cfg(): point RECOVERY_CONFIG at the repo config.yaml
    monkeypatch.setenv("RECOVERY_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(
        yaml.safe_load(open("config.yaml"))))
    # set the webhook secret env var and patch the module-level constant
    monkeypatch.setenv("PAYTM_WEBHOOK_SECRET", "whsec_test")
    from app import webhook as _webhook_mod
    monkeypatch.setattr(_webhook_mod, "WEBHOOK_SECRET", "whsec_test")
    from app import main as appmod
    monkeypatch.setattr(appmod.client, "webhook_secret", "whsec_test")
    return ApiClient(appmod)


class ApiClient:
    def __init__(self, appmod):
        from fastapi.testclient import TestClient
        self.c = TestClient(appmod.app)
        self.appmod = appmod

    def signed(self, event: dict):
        body = json.dumps(event).encode()
        sig = hmac.new(b"whsec_test", body, hashlib.sha256).hexdigest()
        return self.c.post("/webhooks/paytm", content=body,
                           headers={"X-paytm-Signature": sig})

    def store(self):
        import os

        from app.store import Store
        return Store(os.environ["RECOVERY_DB"])


def _failed_event(payment_id="pay_hook0001"):
    """Paytm-format TXN_FAILED webhook payload."""
    return {
        "STATUS": "TXN_FAILED",
        "ORDERID": "order_hook001",
        "TXNID": payment_id,
        "TXNAMOUNT": "3000.00",
        "PAYMENTMODE": "card",
        "RESPCODE": "810",
        "RESPMSG": "Payment declined due to insufficient funds",
        "CUST_ID": "cust_hook1",
        "CALLBACKUSERNAME": "Web Hook",
        "MOBILE_NO": "+919999900008",
        "EMAIL": "w@h.com",
    }


def test_signed_webhook_ingests_failure(api):
    r = api.signed(_failed_event())
    assert r.status_code == 200 and r.json()["status"] == "ingested"
    store = api.store()
    case = store.get_case_by_payment("pay_hook0001")
    assert case is not None
    assert case.failure_class.value == "INSUFFICIENT_FUNDS"


def test_duplicate_webhooks_do_not_duplicate_cases(api):
    r1 = api.signed(_failed_event())
    assert r1.json()["status"] == "ingested"
    r2 = api.signed(_failed_event())
    assert r2.json()["status"] == "already_processed"
    store = api.store()
    n = len([c for c in store.all_cases() if c.payment_id == "pay_hook0001"])
    assert n == 1


def test_forged_signature_rejected(api):
    body = json.dumps(_failed_event()).encode()
    bad = api.c.post("/webhooks/paytm", content=body,
                     headers={"X-paytm-Signature": "0" * 64})
    assert bad.status_code == 400


def test_unsigned_rejected_when_secret_configured(api):
    body = json.dumps(_failed_event()).encode()
    none_sig = api.c.post("/webhooks/paytm", content=body,
                          headers={"X-paytm-Signature": ""})
    assert none_sig.status_code == 400


def test_payment_link_paid_marks_recovery(api):
    r = api.signed(_failed_event())
    case_id = r.json()["case_id"]

    paid = {
        "STATUS": "TXN_SUCCESS",
        "ORDERID": "order_hook001",
        "TXNID": "pay_paid_ok1",
        "TXNAMOUNT": "3000.00",
        "PAYMENTMODE": "card",
        "RESPCODE": "01",
        "RESPMSG": "Txn Success",
        "CUST_ID": "cust_hook1",
    }
    r2 = api.signed(paid)
    assert r2.status_code == 200 and r2.json()["status"] == "recovered"

    store = api.store()
    case = store.get_case(case_id)
    assert case.status.value == "recovered"
    assert case.recovered_amount == 300000
    assert case.recovered_payment_id == "pay_paid_ok1"

    # duplicate paid webhook is idempotent
    r3 = api.signed(paid)
    assert r3.status_code == 200
    events = [e for e in store.audit_for(case_id)
              if e["event_type"] == "recovery.confirmed"]
    assert len(events) == 1


def test_recovery_via_payment_entity_shape(api):
    """TXN_SUCCESS webhook marks recovery via order_id reference."""
    r = api.signed(_failed_event("pay_hook0007"))
    case_id = r.json()["case_id"]
    paid = {
        "STATUS": "TXN_SUCCESS",
        "ORDERID": "order_hook001",
        "TXNID": "pay_from_payment_entity",
        "TXNAMOUNT": "3000.00",
        "PAYMENTMODE": "card",
        "RESPCODE": "01",
        "RESPMSG": "Txn Success",
        "CUST_ID": "cust_hook1",
    }
    r2 = api.signed(paid)
    assert r2.json()["status"] == "recovered"
    assert api.store().get_case(case_id).recovered_payment_id == "pay_from_payment_entity"


# ---- API errors are contained ---------------------------------------------
def test_paytm_api_error_blocks_action_without_crashing(tmp_path, monkeypatch):
    import yaml

    from app.executor import ChannelAdapter, execute_action
    from app.models import (
        ActionType,
        Customer,
        FailureClass,
        Group,
        Intervention,
        RecoveryCase,
    )
    from app.store import Store

    cfg = yaml.safe_load(open("config.yaml"))
    store = Store(tmp_path / "err.db")
    case = RecoveryCase(
        payment_id="p_err", customer=Customer(customer_id="c"), amount=100_000,
        method="card", failure_class=FailureClass.NETWORK_TIMEOUT,
        group=Group.TREATMENT, class_confidence=0.9,
    )
    store.upsert_case(case)
    action = Intervention(case_id=case.case_id,
                          action_type=ActionType.RETRY_PAYMENT_LINK,
                          scheduled_at=datetime_now_iso(), reasoning={})
    store.save_action(action)

    def boom(*a, **kw):
        raise RuntimeError("401 Unauthorized from paytm")

    monkeypatch.setattr("app.executor.client.create_payment_link", boom)
    out, case2 = execute_action(action, case, cfg, store, ChannelAdapter(),
                                now_utc())
    assert out.status.value == "blocked"
    assert "payment_api_error" in out.blocked_reason
    assert len(case2.attempt_times) == 0              # attempt NOT consumed
    audit = store.audit_for(case.case_id)
    assert any(e["event_type"] == "action.failed" for e in audit)


def datetime_now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def now_utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
