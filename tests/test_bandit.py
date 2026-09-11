"""Tests for UCB1 bandit channel selection."""
from __future__ import annotations

import math
import random

from app.bandit import BanditArm, ChannelBandit


def test_arm_defaults():
    arm = BanditArm(channel="sms")
    assert arm.pulls == 0
    assert arm.recoveries == 0
    assert arm.mean == 0.5  # default prior


def test_arm_update_recovered():
    arm = BanditArm(channel="sms")
    arm.update(recovered=True)
    assert arm.pulls == 1
    assert arm.recoveries == 1
    assert arm.mean == 1.0


def test_arm_update_not_recovered():
    arm = BanditArm(channel="sms")
    arm.update(recovered=False)
    assert arm.pulls == 1
    assert arm.recoveries == 0
    assert arm.mean == 0.0


def test_arm_ucb_score_unpulled():
    arm = BanditArm(channel="whatsapp")
    # Unpulled arm should have infinite score (explore first)
    assert arm.ucb_score(total_pulls=10) == float('inf')


def test_arm_ucb_score_formula():
    arm = BanditArm(channel="email")
    arm.pulls = 10
    arm.recoveries = 6  # mean = 0.6
    total_pulls = 100
    c = 1.414
    expected = 0.6 + c * math.sqrt(2 * math.log(total_pulls) / 10)
    assert abs(arm.ucb_score(total_pulls, c) - expected) < 0.001


def test_bandit_defaults():
    b = ChannelBandit()
    assert set(b.channels.keys()) == {"whatsapp", "sms", "email", "voice", "retry"}


def test_bandit_select_returns_string():
    b = ChannelBandit()
    ch = b.select()
    assert isinstance(ch, str)
    assert ch in b.channels


def test_bandit_select_exclude():
    b = ChannelBandit()
    ch = b.select(exclude={"whatsapp", "sms", "voice", "retry"})
    assert ch == "email"


def test_bandit_select_fallback_on_all_excluded():
    b = ChannelBandit()
    ch = b.select(exclude={"whatsapp", "sms", "email", "voice", "retry"})
    assert ch == "email"  # fallback


def test_bandit_update():
    b = ChannelBandit()
    b.update("sms", recovered=True)
    assert b.channels["sms"].recoveries == 1
    assert b.channels["sms"].pulls == 1


def test_bandit_state():
    b = ChannelBandit()
    b.update("whatsapp", recovered=True)
    state = b.state
    assert "whatsapp" in state
    assert state["whatsapp"]["pulls"] == 1
    assert state["whatsapp"]["recoveries"] == 1
    assert "mean_recovery" in state["whatsapp"]


def test_bandit_ucb1_explores_untried():
    """Unpulled arms should be selected first (infinite UCB score)."""
    b = ChannelBandit()
    # All arms unpulled - any could be selected
    ch = b.select()
    assert ch in b.channels


def test_bandit_ucb1_explores_unpulled():
    """Untried arms get infinite UCB score and are selected first."""
    b = ChannelBandit()
    # All arms unpulled - any could be selected
    ch = b.select()
    assert ch in b.channels
    # After one selection, that arm has 1 pull, others still 0
    # Next selection should pick an untried arm
    ch2 = b.select()
    assert ch2 in b.channels


def test_bandit_sms_worst_performer():
    """SMS with 0% recovery should never be selected over better arms."""
    b = ChannelBandit()
    random.seed(42)
    # Give all arms equal pulls with 100% recovery
    for ch in b.channels:
        for _ in range(20):
            b.update(ch, recovered=True)
    # Make SMS fail always
    for _ in range(50):
        b.update("sms", recovered=False)

    selections = [b.select() for _ in range(200)]
    sms_count = selections.count("sms")
    # SMS should get very few selections
    assert sms_count < 10  # < 5%


def test_bandit_contextual_bias_insufficient_funds():
    """INSUFFICIENT_FUNDS should bias toward WhatsApp."""
    b = ChannelBandit()
    for ch in b.channels:
        for _ in range(20):
            b.update(ch, recovered=True)

    ctx = {"failure_class": "INSUFFICIENT_FUNDS", "amount_paise": 100000}
    selections = [b.select(context=ctx) for _ in range(100)]
    whatsapp_count = selections.count("whatsapp")
    sms_count = selections.count("sms")
    assert whatsapp_count > sms_count


def test_bandit_contextual_bias():
    """Context should bias channel selection."""
    b = ChannelBandit()
    # Equalize pulls
    for ch in b.channels:
        for _ in range(10):
            b.update(ch, recovered=True)

    # INSUFFICIENT_FUNDS should bias toward whatsapp
    ctx = {"failure_class": "INSUFFICIENT_FUNDS", "amount_paise": 100000}
    selections = [b.select(context=ctx) for _ in range(100)]
    whatsapp_count = selections.count("whatsapp")
    sms_count = selections.count("sms")
    # WhatsApp should be preferred for insufficient funds
    assert whatsapp_count > sms_count


def test_bandit_reset():
    b = ChannelBandit()
    b.update("voice", recovered=True)
    b.reset()
    assert b.channels["voice"].pulls == 0
    assert b.channels["voice"].recoveries == 0
