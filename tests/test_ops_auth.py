"""Operator-endpoint auth (AGENT_API_TOKEN) and tick due-filtering."""
import os

import pytest
import yaml


@pytest.fixture()
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("RECOVERY_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("RECOVERY_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(yaml.safe_load(open("config.yaml"))))
    from fastapi.testclient import TestClient

    from app import main as appmod
    return TestClient(appmod.app), monkeypatch


def test_tick_open_when_no_token_configured(api):
    client, _ = api
    assert client.post("/tick").status_code == 200


def test_roi_calculator_config_driven_and_validated(api):
    client, monkeypatch = api
    monkeypatch.setenv("AGENT_API_TOKEN", "s3cret")   # read-only endpoint: no token needed

    r = client.get("/calculator", params={"amount_at_risk_cr": 2.0})
    assert r.status_code == 200
    body = r.json()
    assert body["inputs"]["cases"] == round(2.0 * 1e9 / 90_000)
    # lift math: incremental = at-risk * lift%
    assert body["incremental_recovery_paise"] == round(2.0 * 1e9 * 0.5)
    # spend derived from config channel costs x attempt cap
    import yaml
    cfg = yaml.safe_load(open("config.yaml"))
    ladder = [cfg["channels"][c]["cost_paise"]
              for c in ("whatsapp", "sms", "email") if cfg["channels"][c]["enabled"]]
    expected_spend = body["inputs"]["cases"] * cfg["policy"]["max_attempts_per_case"] \
        * sum(ladder) / len(ladder)
    assert body["projected_contact_spend_paise"] == round(expected_spend)

    bad = client.get("/calculator", params={"amount_at_risk_cr": -1})
    assert bad.status_code == 422
    bad_lift = client.get("/calculator",
                          params={"amount_at_risk_cr": 1, "estimated_lift_pp": 150})
    assert bad_lift.status_code == 422


def test_tick_requires_token_when_configured(api):
    client, monkeypatch = api
    monkeypatch.setenv("AGENT_API_TOKEN", "s3cret")
    assert client.post("/tick").status_code == 401
    assert client.post("/tick", headers={"X-Agent-Token": "wrong"}).status_code == 401
    r = client.post("/tick", headers={"X-Agent-Token": "s3cret"})
    assert r.status_code == 200 and "executed" in r.json()


def test_approve_and_opt_out_require_token_too(api):
    client, monkeypatch = api
    monkeypatch.setenv("AGENT_API_TOKEN", "s3cret")
    assert client.post("/cases/whatever/approve").status_code == 401
    assert client.post("/cases/whatever/opt_out",
                       headers={"X-Agent-Token": "s3cret"}).status_code == 404
    # 404 (not 401) proves the token check passed before the lookup


def test_tick_supersedes_stale_actions_of_closed_cases(api):
    """A recovered case's leftover scheduled action is cleaned up, not skipped forever."""
    client, _ = api
    from datetime import datetime, timedelta, timezone

    from app.models import (
        ActionType,
        CaseStatus,
        Customer,
        FailureClass,
        Group,
        Intervention,
        RecoveryCase,
    )
    from app.store import Store

    store = Store(os.environ["RECOVERY_DB"])
    case = RecoveryCase(
        payment_id="p_stale", customer=Customer(customer_id="c"), amount=100_000,
        method="card", failure_class=FailureClass.NETWORK_TIMEOUT,
        group=Group.TREATMENT, class_confidence=0.9,
        status=CaseStatus.RECOVERED,
    )
    store.upsert_case(case)
    stale = Intervention(
        case_id=case.case_id, action_type=ActionType.NUDGE_SMS,
        scheduled_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        reasoning={},
    )
    store.save_action(stale)

    r = client.post("/tick")
    assert r.status_code == 200
    rows = store.actions_for(case.case_id)
    assert all(a.status.value != "scheduled" for a in rows)
