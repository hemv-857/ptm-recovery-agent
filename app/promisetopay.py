"""Promise-to-pay: parse inbound customer replies (Hinglish/English) into intents.

Deterministic regex at the trust boundary — no LLM parsing of customer SMS.
Intents: OPT_OUT | ALREADY_PAID | PROMISE(due) | REFUSED | OTHER

Promise reliability tracking lives in the Store (cross-case, persisted) —
see `Store.promise_reliability`; the selector consumes it for EV adjustment.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
    # Hinglish
    "somvar": 0, "mangalvar": 1, "budhvar": 2, "guruvar": 3, "shukravar": 4,
    "shanivar": 5, "ravivar": 6,
}


class Intent(str, Enum):
    OPT_OUT = "opt_out"
    ALREADY_PAID = "already_paid"
    PROMISE = "promise"
    REFUSED = "refused"
    OTHER = "other"


@dataclass
class ParsedReply:
    intent: Intent
    due: datetime | None = None            # for PROMISE
    note: str = ""                         # matched raw text snippet


def _ist_now() -> datetime:
    return datetime.now(tz=IST)


def _next_weekday(target: int, now: datetime) -> datetime:
    delta = (target - now.weekday()) % 7 or 7      # "monday" means next monday, not today
    day = now + timedelta(days=delta)
    return day.replace(hour=18, minute=0, second=0, microsecond=0)


def _day_of_month(day_num: int, now: datetime) -> datetime:
    """Next occurrence of `day_num` (e.g. '25 tarikh' / '1 tareekh')."""
    day_num = min(day_num, 28)
    candidate = now.replace(day=day_num, hour=18, minute=0, second=0, microsecond=0)
    if candidate <= now:
        y, mth = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
        candidate = datetime(y, mth, day_num, 18, 0, tzinfo=IST)
    return candidate


def parse_reply(text: str) -> ParsedReply:
    t = text.lower().strip()

    if re.search(r"\b(stop|unsubscribe|band karo|opt ?out|do not call)\b", t):
        return ParsedReply(Intent.OPT_OUT, note=text)

    if re.search(r"\b(paid|pay kar (diya|chuka)|done|ho gaya|complete)\b", t):
        return ParsedReply(Intent.ALREADY_PAID, note=text)

    if re.search(r"\b(cant|can'?t|nahi\s+(kar\s+)?pa\s*(?:unga|rha|raha|rahi|rahe)|"
                 r"not possible|dispute|galat|wrong bill)\b", t):
        return ParsedReply(Intent.REFUSED, note=text)

    # ---- promise patterns ---------------------------------------------------
    now = _ist_now()
    m = re.search(r"\b(\d{1,2})\s*(?:tarikh|tareekh|date)\b", t)
    if m:
        due = _day_of_month(int(m.group(1)), now)
        return ParsedReply(Intent.PROMISE, due=due, note=text)

    m = re.search(r"\b(\d{1,2})\s*(?:din|days?)\s*(?:baad|later)?\b", t)
    if m:
        due = (now + timedelta(days=int(m.group(1)))
               ).replace(hour=18, minute=0, second=0, microsecond=0)
        return ParsedReply(Intent.PROMISE, due=due, note=text)

    m = re.search(r"\b(parso)\b", t)
    if m:
        due = (now + timedelta(days=2)).replace(hour=18, minute=0, second=0, microsecond=0)
        return ParsedReply(Intent.PROMISE, due=due, note=text)

    m = re.search(r"\b(kal|tomorrow|tmrw)\b", t)
    if m:
        due = (now + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
        return ParsedReply(Intent.PROMISE, due=due, note=text)

    m = re.search(r"\b([a-z]+(?:var|day|mon|tue|wed|thu|fri|sat|sun))\b", t)
    if m and m.group(1) in _WEEKDAYS:
        due = _next_weekday(_WEEKDAYS[m.group(1)], now)
        return ParsedReply(Intent.PROMISE, due=due, note=text)

    m = re.search(r"\b(next week|agle hafte)\b", t)
    if m:
        due = (now + timedelta(days=7)).replace(hour=18, minute=0, second=0, microsecond=0)
        return ParsedReply(Intent.PROMISE, due=due, note=text)

    m = re.search(r"\b(aaj|today)\b", t)
    if m:
        due = min(now + timedelta(hours=6),
                  now.replace(hour=22, minute=0, second=0, microsecond=0))
        return ParsedReply(Intent.PROMISE, due=due, note=text)

    return ParsedReply(Intent.OTHER, note=text)
