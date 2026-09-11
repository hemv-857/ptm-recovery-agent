"""Mock TTS/BSP voice provider — a runnable stand-in for Exotel/Knowlarity/GCP-TTS.

The agent's VoiceProvider POSTs {to, tts_script, sms_followthrough,
max_duration_s, retry_on_no_answer} to VOICE_PROVIDER_URL. Point it here to
demo the live voice path with zero BSP credentials:

    uvicorn integrations.mock_voice_provider:app --port 9001
    VOICE_PROVIDER_URL=http://localhost:9001/call uvicorn app.main:app --port 8000

GET /calls returns everything received, so demos can show the exact script and
follow-through SMS the agent would have sent.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI

app = FastAPI(title="Mock Voice Provider", version="0.1.0")
CALLS: list[dict] = []


@app.post("/call")
async def place_call(payload: dict) -> dict:
    entry = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "to": payload.get("to"),
        "tts_script": payload.get("tts_script", ""),
        "sms_followthrough": payload.get("sms_followthrough", ""),
        "max_duration_s": payload.get("max_duration_s"),
        "retry_on_no_answer": payload.get("retry_on_no_answer"),
    }
    CALLS.append(entry)
    print(f"[mock-voice] call #{len(CALLS)} -> {entry['to']}")
    return {"provider": "mock", "call_id": f"call_{len(CALLS):06d}", "placed": True}


@app.get("/calls")
def list_calls() -> list[dict]:
    return CALLS


@app.get("/calls/{call_id}/status")
def call_status(call_id: str) -> dict:
    """Pretend ASR: lets a demo close the voice loop end-to-end (the transcript
    is exactly what the agent would hear via POST /inbound/reply)."""
    try:
        idx = int(call_id.removeprefix("call_")) - 1
        entry = CALLS[idx]
    except (ValueError, IndexError):
        from fastapi import HTTPException
        raise HTTPException(404, "unknown call_id") from None
    return {
        "call_id": call_id,
        "to": entry["to"],
        "status": "completed",
        "duration_s": entry["max_duration_s"] or 45,
        "transcript": "Haan, main kal payment kar dunga, link aa gaya hai na?",
    }
