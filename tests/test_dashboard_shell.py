"""Runtime smoke tests: `/` must serve the dashboard shell with the pieces the
browser needs. A missing marker here means a blank page even when every other
test passes — the exact failure mode the JSX compile test guards against."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_root_serves_dashboard_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOVERY_DB", str(tmp_path / "smoke.db"))
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert 'id="root"' in body, "React mount point missing"
    assert '/static/vendor/react.production.min.js' in body
    assert '/static/vendor/babel.min.js' in body
    assert '/static/dashboard.css' in body, "extracted stylesheet not linked"
    assert '/static/dashboard.jsx' in body, "extracted JSX not referenced"


def test_static_assets_are_served(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOVERY_DB", str(tmp_path / "smoke.db"))
    client = TestClient(app)
    for path, marker in [
        ("/static/dashboard.css", "Paytm"),
        ("/static/dashboard.jsx", "function App()"),
        ("/static/vendor/babel.min.js", "babel"),
    ]:
        r = client.get(path)
        assert r.status_code == 200, f"{path} not served"
        assert marker in r.text, f"{path} missing expected marker {marker!r}"
