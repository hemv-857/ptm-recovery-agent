"""
Paytm webhook ingestion — lightweight stub kept for router compatibility.
"""
from __future__ import annotations

import os

from fastapi import APIRouter

router = APIRouter(prefix="/webhooks", tags=["webhook"])

# paytm webhook secret (used by tests that patch this module)
WEBHOOK_SECRET = os.getenv("PAYTM_WEBHOOK_SECRET", "demo_secret")
