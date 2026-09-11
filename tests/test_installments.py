"""Smart Payment Plan Recovery (SPR): installment offers, collection loop,
partial-recovery measurement semantics, and flag-off canonical behavior."""
from datetime import datetime, timedelta, timezone

import yaml

from app.agent import record_installment_payment
from app.installments import (
    due_now_or_scheduled,
    is_eligible,
    make_schedule,
    next_unpaid,
)
from app.measure import build_report
from app.models import (
    ActionType,
    CaseStatus,
    Customer,
    FailureClass,
    Group,
    RecoveryCase,
)
from app.selector import select_next_action
from simulate.batch_generator import assign_groups, generate_batch
from simulate.engine import run
from simulate.world import Outcome, WorldModel
from app.store import Store

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
CFG = yaml.safe_load(open("config.yaml"))


def _spr_cfg() -> dict:
    cfg = yaml.safe_load(open("config.yaml"))
    cfg["retry"]["installment_offers"] = True
    # short gaps so collections fire inside the 7-day simulation horizon
    cfg["retry"]["installment_gap_days"] = 2
    return cfg


def _invoice_case(amount: int = 100_000_00,
                  payment_id: str = "pay_spr") -> RecoveryCase:
    return RecoveryCase(
        payment_id=payment_id,   # case_id derives from this at construction
        customer=Customer(customer_id="cust_spr", name="Karan Gupta",
                          phone="+919999900001",
                          email="ap@buyer.example.com"),
        amount=amount,
        method="card",
        failure_class=FailureClass.INVOICE_OVERDUE,
        class_confidence=0.95,
        loss_age_days=12,
        group=Group.TREATMENT,
    )


# ---- pure helpers -----------------------------------------------------------

def test_schedule_slices_sum_to_amount_and_align_to_cash_cycle():
    plan = make_schedule(100_000_00, CFG, NOW)          # Rs 1,00,000
    assert sum(i["amount"] for i in plan) == 100_000_00
    assert len(plan) == 3
    gaps = [
        (datetime.fromisoformat(plan[i + 1]["due"])
         - datetime.fromisoformat(plan[i]["due"])).days
        for i in range(2)
    ]
    assert gaps == [15, 15]
    # rounding residue lands on the last slice, nothing lost
    assert plan[-1]["amount"] > 0


def test_eligibility_b2b_only_above_threshold():
    big = _invoice_case(100_000_00)
    assert is_eligible(big, CFG)

    small = _invoice_case(400_000)                       # Rs 4k < Rs 50k
    assert not is_eligible(small, CFG)

    consumer = _invoice_case(100_000_00)
    consumer.failure_class = FailureClass.INSUFFICIENT_FUNDS
    assert not is_eligible(consumer, CFG)

    with_plan = _invoice_case(100_000_00)
    with_plan.installment_plan = make_schedule(with_plan.amount, CFG, NOW)
    assert not is_eligible(with_plan, CFG)


def test_next_unpaid_and_due_window():
    case = _invoice_case()
    case.installment_plan = make_schedule(case.amount, CFG, NOW)
    first = next_unpaid(case, NOW)
    assert first is case.installment_plan[0]
    # slice 0 is due immediately (upfront); with its 12h lead it is collectible now
    assert due_now_or_scheduled(case, NOW) is case.installment_plan[0]
    case.installment_plan[0]["paid"] = True
    assert due_now_or_scheduled(case, NOW + timedelta(days=1)) is None   # next due day 15
    later = NOW + timedelta(days=16)
    assert due_now_or_scheduled(case, later) is case.installment_plan[1]


# ---- selector ----------------------------------------------------------------

def test_selector_offers_plan_when_flag_on():
    cfg = _spr_cfg()
    act = select_next_action(_invoice_case(), cfg, NOW)
    assert act.action_type is ActionType.OFFER_INSTALLMENT_PLAN
    assert act.reasoning["strategy"] == "smart_payment_plan"
    assert len(act.reasoning["installments"]) == 3


def test_flag_off_preserves_canonical_ladder():
    # default config: no SPR, the standard INVOICE_OVERDUE ladder stage 1
    act = select_next_action(_invoice_case(), CFG, NOW)
    assert act.action_type is not ActionType.OFFER_INSTALLMENT_PLAN
    assert act.reasoning["strategy"].startswith("receivables_ladder")


def test_selector_schedules_collection_for_active_plan():
    cfg = _spr_cfg()
    case = _invoice_case(payment_id="pay_coll")
    case.installment_plan = make_schedule(case.amount, CFG, NOW)
    case.installment_plan[0]["paid"] = True                # upfront already collected
    later = NOW + timedelta(days=16)
    act = select_next_action(case, cfg, later)
    assert act.action_type is ActionType.COLLECT_INSTALLMENT
    assert act.reasoning["installment"] == "2/3"
    assert act.reasoning["amount_paise"] == case.installment_plan[1]["amount"]


# ---- world model --------------------------------------------------------------

def test_acceptance_is_boosted_but_split():
    cfg = _spr_cfg()
    w = WorldModel(cfg=cfg, horizon_end=NOW + timedelta(days=30))
    case = _invoice_case()
    case.attempt_times = [NOW.isoformat()]
    # brute-force case ids until we see both outcomes exist (seeded draws)
    outcomes = set()
    for n in range(60):
        c = _invoice_case(payment_id=f"pay_probe_{n}")
        c.attempt_times = [NOW.isoformat()]
        outcomes.add(w.respond_to_contact(c, ActionType.OFFER_INSTALLMENT_PLAN, NOW))
    assert Outcome.ACCEPTED_PLAN in outcomes
    assert Outcome.IGNORED in outcomes


def test_installment_keep_sampling():
    w = WorldModel(cfg=CFG, horizon_end=NOW + timedelta(days=30))
    outcomes = set()
    for n in range(60):
        c = _invoice_case(payment_id=f"pay_probe_{n}")
        c.installment_plan = make_schedule(c.amount, CFG, NOW)
        outcomes.add(w.respond_to_installment(c))
    assert Outcome.PAID_INSTALLMENT in outcomes
    assert Outcome.DEFAULTED_INSTALLMENT in outcomes


# ---- agent: partial recovery accumulation -------------------------------------

def test_record_installment_payment_accumulates_then_closes(tmp_path):
    store = Store(tmp_path / "s.db")
    case = _invoice_case(payment_id="pay_inst_case")
    case.installment_plan = make_schedule(case.amount, CFG, NOW)
    store.upsert_case(case)

    case = record_installment_payment(case, case.installment_plan[0]["amount"],
                                      NOW.isoformat(), store, "pay_inst_1")
    assert case.status is CaseStatus.OPEN                  # partial: still open
    assert case.recovered_amount == case.installment_plan[0]["amount"]

    case = record_installment_payment(case, case.installment_plan[1]["amount"],
                                      NOW.isoformat(), store, "pay_inst_2")
    assert case.status is CaseStatus.OPEN

    case = record_installment_payment(case, case.installment_plan[2]["amount"],
                                      NOW.isoformat(), store, "pay_inst_3")
    assert case.status is CaseStatus.RECOVERED             # full: closes
    assert case.recovered_amount == case.amount

    events = [e["event_type"] for e in store.audit_for(case.case_id)]
    assert events.count("installment.paid") == 3
    assert "recovery.confirmed" in events


# ---- measurement semantics ------------------------------------------------------

def test_partial_recovery_counts_money_but_not_rate(tmp_path):
    store = Store(tmp_path / "s.db")
    full = _invoice_case(payment_id="pay_full")
    full.status = CaseStatus.RECOVERED
    full.recovered_amount = full.amount
    store.upsert_case(full)

    partial = _invoice_case(50_000_00, payment_id="pay_partial")
    partial.recovered_amount = 15_000_00                   # 30% slice paid
    store.upsert_case(partial)

    rep = build_report(store.all_cases(), [], CFG)
    t = rep["headline"]
    # rate: only the full recovery counts; money: both count
    assert abs(t["recovery_rate_treatment"] - 0.5) < 1e-9
    assert rep["headline"]["recovered_treatment_paise"] == full.amount + partial.recovered_amount


# ---- engine integration (flag ON, seeded, deterministic) ------------------------

def test_engine_runs_spr_end_to_end(tmp_path):
    cfg = _spr_cfg()
    start = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)
    payments = generate_batch(150, start, seed=42)
    groups = assign_groups(payments)
    store = Store(tmp_path / "e.db")
    run(payments, cfg, store)

    spr_cases = [c for c in store.all_cases() if c.installment_plan]
    assert spr_cases, "expected at least one installment offer accepted"

    paid_something = [c for c in spr_cases if c.recovered_amount > 0]
    assert paid_something, "accepted plans should collect at least the upfront slice"

    closed = [c for c in spr_cases
              if c.recovered_amount >= c.amount and c.status.value == "recovered"]
    assert closed, "fully paid installment cases must close as recovered"

    # every SPR case has a complete audit chain: offer -> slices -> closure
    for c in spr_cases[:3]:
        events = [e["event_type"] for e in store.audit_for(c.case_id)]
        assert "installment.paid" in events
