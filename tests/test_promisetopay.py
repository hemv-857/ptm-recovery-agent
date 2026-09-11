from datetime import datetime
from zoneinfo import ZoneInfo

from app.promisetopay import Intent, parse_reply

IST = ZoneInfo("Asia/Kolkata")


def test_opt_out_intents():
    for text in ("STOP", "stop karo", "please unsubscribe", "do not call me"):
        assert parse_reply(text).intent is Intent.OPT_OUT, text


def test_already_paid():
    for text in ("paid", "Pay kar diya", "ho gaya bhai", "done"):
        assert parse_reply(text).intent is Intent.ALREADY_PAID, text


def test_promise_kal_tomorrow():
    p = parse_reply("kal karta hoon")
    assert p.intent is Intent.PROMISE
    now = datetime.now(IST)
    assert (p.due.date() - now.date()).days == 1


def test_promise_parso():
    p = parse_reply("parso pakka")
    assert p.intent is Intent.PROMISE
    now = datetime.now(IST)
    assert (p.due.date() - now.date()).days == 2


def test_promise_din_baad():
    p = parse_reply("3 din baad")
    assert p.intent is Intent.PROMISE
    now = datetime.now(IST)
    assert (p.due.date() - now.date()).days == 3


def test_promise_tarikh_rolls_month():
    p = parse_reply("1 tarikh ko karunga")
    assert p.intent is Intent.PROMISE
    now = datetime.now(IST)
    if now.day == 1 and now.hour < 18:
        assert p.due.day == 1
    else:
        assert p.due.day == 1 and p.due > now


def test_promise_weekday_hinglish():
    p = parse_reply("somvar ko bhejta hoon")
    assert p.intent is Intent.PROMISE
    assert p.due.weekday() == 0 and p.due > datetime.now(IST)


def test_refused():
    for text in ("cant pay", "nahi pa rha hun", "wrong bill hai", "dispute"):
        assert parse_reply(text).intent is Intent.REFUSED, text


def test_other_gibberish():
    assert parse_reply("ok").intent is Intent.OTHER
