"""Rate limiter: fixed-window math, middleware 429s, per-IP independence."""
import pytest
import yaml


@pytest.fixture()
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("RECOVERY_DB", str(tmp_path / "rl.db"))
    monkeypatch.setenv("RECOVERY_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(yaml.safe_load(open("config.yaml"))))
    from fastapi.testclient import TestClient

    from app import main as appmod

    return TestClient(appmod.app), appmod


def test_fixed_window_math():
    from app.ratelimit import RateLimiter

    rl = RateLimiter(limit=3, window_s=60)
    t0 = 1000.0
    assert rl.check("ip1", now=t0) == (True, 61)
    assert rl.check("ip1", now=t0 + 1) == (True, 60)
    assert rl.check("ip1", now=t0 + 2) == (True, 59)
    allowed, retry = rl.check("ip1", now=t0 + 3)
    assert not allowed and retry == 58          # 4th hit inside the window
    allowed, _ = rl.check("ip1", now=t0 + 60)   # window rolled
    assert allowed


def test_middleware_429_with_retry_after(api, monkeypatch):
    client, appmod = api
    from app.ratelimit import RateLimiter
    monkeypatch.setattr(appmod, "_limiter", RateLimiter(limit=2))  # fresh window
    assert client.get("/calculator", params={"amount_at_risk_cr": 1}).status_code == 200
    assert client.get("/calculator", params={"amount_at_risk_cr": 1}).status_code == 200
    r = client.get("/calculator", params={"amount_at_risk_cr": 1})
    assert r.status_code == 429
    assert "Retry-After" in r.headers and int(r.headers["Retry-After"]) >= 1
    assert r.json()["detail"] == "rate limit exceeded"


def test_per_ip_independence(api, monkeypatch):
    client, appmod = api
    from app.ratelimit import RateLimiter
    monkeypatch.setattr(appmod, "_limiter", RateLimiter(limit=1))
    assert client.get("/report", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert client.get("/report", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
    assert client.get("/report", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200


def test_eviction_keeps_memory_bounded():
    from app.ratelimit import RateLimiter

    rl = RateLimiter(limit=10, window_s=60)
    for i in range(10_050):
        rl.check(f"ip{i}", now=1000.0)
    rl.check("trigger", now=1061.0)             # crosses threshold -> evict expired
    assert len(rl._hits) < 10_050               # 10,050 expired keys evicted
    assert all(start > 1061.0 - 60 for start, _ in rl._hits.values())  # only live windows remain


def test_dashboard_serves_static_and_cases_endpoint(api, monkeypatch):
    client, _ = api
    r = client.get("/")
    assert r.status_code == 200
    assert "Paytm Revenue Recovery Dashboard" in r.text and "chart.umd.min.js" in r.text

    rc = client.get("/cases/recent?limit=5")
    assert rc.status_code == 200
    body = rc.json()
    assert isinstance(body["cases"], list)
    if body["cases"]:
        c = body["cases"][0]
        assert {"case_id", "failure_class", "amount_paise", "status",
                "recovered_amount_paise"} <= set(c)
    assert client.get("/cases/recent?limit=999").status_code == 200
