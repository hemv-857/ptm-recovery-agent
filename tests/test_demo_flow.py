"""The 5-minute judge demo, executed for real: server + scripts/demo.py.
If this passes, the demo cannot be broken quietly."""
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def server():
    port = 8157
    env = {**os.environ,
           "RECOVERY_DB": "demo_test.db",
           "PAYTM_WEBHOOK_SECRET": "demo_secret"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    import time

    import httpx
    for _ in range(40):
        try:
            httpx.get(f"http://localhost:{port}/report", timeout=2)
            break
        except Exception:
            time.sleep(0.25)
    yield f"http://localhost:{port}"
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)
    for f in ("demo_test.db", "demo_test.db-wal", "demo_test.db-shm"):
        Path(f).unlink(missing_ok=True)


def test_demo_script_full_flow(server):
    r = subprocess.run(
        [sys.executable, "scripts/demo.py", "--base", server,
         "--db", "demo_test.db"],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    for marker in ("STEP 1", "STEP 2", "STEP 3", "STEP 4", "STEP 5",
                   "classified_as: INSUFFICIENT_FUNDS",
                   "strategy: alternate_instrument",
                   "action.deferred", "promise_recorded",
                   "recovery.confirmed"):
        assert marker in r.stdout, f"missing: {marker}\n{r.stdout}"
