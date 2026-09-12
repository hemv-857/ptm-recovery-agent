"""Discrete-event simulation engine: runs the full agent loop against the world
model over a synthetic cohort, producing a store that measurement can analyze.

Event kinds: case_ingest | organic | action_due | sweep
"""
from __future__ import annotations

import heapq
from datetime import datetime, timedelta, timezone

from app.agent import (
    ingest_failure,
    mark_recovered,
    plan_and_schedule,
    record_installment_payment,
    write_off,
)
from app.executor import ChannelAdapter, execute_action
from app.installments import make_schedule as make_installment_schedule
from app.models import (
    ActionStatus,
    ActionType,
    AuditEvent,
    Group,
    Intervention,
)
from app.policy import should_write_off

from .batch_generator import assign_groups
from .world import Outcome, WorldModel


def run(payments, cfg: dict, store,
        progress: callable | None = None) -> WorldModel:
    groups = assign_groups(payments)
    starts = [datetime.fromisoformat(p.failed_at) for p in payments]
    t0, latest = min(starts), max(starts)
    horizon_end = latest + timedelta(days=cfg["simulation"]["horizon_days"])

    world = WorldModel(cfg=cfg, horizon_end=horizon_end)
    channels = ChannelAdapter()
    from app.executor import VoiceProvider
    voice = VoiceProvider()
    counter = iter(range(10**9))
    heap: list[tuple[float, int, str, object]] = []

    def push(when: datetime, kind: str, payload) -> None:
        heapq.heappush(heap, (when.timestamp(), next(counter), kind, payload))

    for fp, g in zip(payments, groups, strict=True):
        push(datetime.fromisoformat(fp.failed_at), "case_ingest", (fp, g))

    s = t0 + timedelta(hours=6)
    while s < horizon_end:
        push(s, "sweep", None)
        s += timedelta(hours=6)

    store.begin_batch()
    processed = 0
    action_visits: dict[str, int] = {}
    try:
        while heap:
            ts, _, kind, payload = heapq.heappop(heap)
            now = datetime.fromtimestamp(ts, tz=timezone.utc)

            if kind == "case_ingest":
                fp, g = payload
                case = ingest_failure(
                    fp, store, cfg, assign_group=lambda gg=g: gg,
                )
                lat = world.latent_for(case)
                if lat.organic_at and lat.organic_at <= horizon_end:
                    push(lat.organic_at, "organic", case.case_id)
                # control purity: the control group gets NO agent actions —
                # its organic clock is the counterfactual. Planning/escalating
                # on control cases prematurely closed them and biased the
                # measured lift downward on the control side.
                first_action = (plan_and_schedule(case, cfg, now, store)
                                if case.group is Group.TREATMENT else None)
                if first_action:
                    when = datetime.fromisoformat(first_action.scheduled_at)
                    if when <= horizon_end:
                        push(when, "action_due", first_action.action_id)
                processed += 1
                if progress and processed % 250 == 0:
                    progress(f"ingested {processed}/{len(payments)}")

            elif kind == "action_due":
                action_id = payload
                # Safety: skip if same action fires too many times
                action_visits[action_id] = action_visits.get(action_id, 0) + 1
                if action_visits[action_id] > 6:
                    continue
                action = store.get_action_by_id(action_id)
                if action is None:
                    continue
                case = store.get_case(action.case_id)
                if case is None or case.status.value in (
                    "recovered", "written_off",
                ):
                    continue
                executed, case = execute_action(
                    action, case, cfg, store, channels, now,
                    voice=voice,
                )

                if executed.action_type is ActionType.CHECK_PROMISE:
                    # promise broken (still open past due): resume ladder
                    if case.status.value in ("open", "scheduled"):
                        fresh = plan_and_schedule(case, cfg, now, store)
                        if fresh and datetime.fromisoformat(
                            fresh.scheduled_at
                        ) <= horizon_end:
                            push(
                                datetime.fromisoformat(fresh.scheduled_at),
                                "action_due", fresh.action_id,
                            )
                    continue

                if executed.status is ActionStatus.SCHEDULED:
                    # policy deferred it
                    push(
                        datetime.fromisoformat(executed.scheduled_at),
                        "action_due", executed.action_id,
                    )
                    continue
                if executed.status is not ActionStatus.EXECUTED:
                    continue

                if executed.action_type is ActionType.ESCALATE_HUMAN:
                    store.append_audit(AuditEvent(
                        actor="executor",
                        event_type="case.escalated_human",
                        case_id=case.case_id, payload={},
                    ))
                    write_off(
                        case, "escalated_to_human_finance_ops", store,
                    )
                    continue

                outcome = world.respond_to_contact(
                    case, executed.action_type, now,
                )

                # ---- SPR: installment offer accepted -> lock the schedule,
                # collect the upfront slice, schedule the rest
                if (executed.action_type is ActionType.OFFER_INSTALLMENT_PLAN
                        and outcome is Outcome.ACCEPTED_PLAN):
                    case.installment_plan = make_installment_schedule(
                        case.amount, cfg, now,
                    )
                    first = case.installment_plan[0]
                    case = record_installment_payment(
                        case, first["amount"], now.isoformat(), store,
                        payment_id=f"pay_inst_{case.case_id[-8:]}_0",
                    )
                    if case.status.value != "recovered":
                        store.supersede_scheduled(case.case_id)
                        for idx, inst in enumerate(case.installment_plan[1:], 1):
                            collect = Intervention(
                                case_id=case.case_id,
                                action_type=ActionType.COLLECT_INSTALLMENT,
                                scheduled_at=inst["due"],
                                reasoning={"strategy": "installment_collection",
                                           "installment": f"{idx + 1}/{len(case.installment_plan)}",
                                           "amount_paise": inst["amount"]},
                            )
                            store.save_action(collect)
                            if datetime.fromisoformat(inst["due"]) <= horizon_end:
                                push(datetime.fromisoformat(inst["due"]),
                                     "action_due", collect.action_id)
                        continue
                    continue

                # ---- SPR: an agreed installment comes due
                if executed.action_type is ActionType.COLLECT_INSTALLMENT:
                    res = world.respond_to_installment(case)
                    if res is Outcome.PAID_INSTALLMENT:
                        case = record_installment_payment(
                            case, executed.reasoning["amount_paise"],
                            now.isoformat(), store,
                            payment_id=f"pay_inst_{case.case_id[-8:]}_"
                                       f"{sum(1 for i in case.installment_plan if i.get('paid'))}",
                        )
                    else:
                        # buyer missed a slice: stop trusting the plan,
                        # resume the normal recovery ladder
                        case.installment_defaulted = True
                        case.touch()
                        store.upsert_case(case)
                        store.append_audit(AuditEvent(
                            actor="world", event_type="installment.defaulted",
                            case_id=case.case_id,
                            payload={"total_paid_paise": case.recovered_amount},
                        ))
                    continue

                if outcome is Outcome.RECOVERED:
                    mark_recovered(
                        case, f"pay_rec_{case.case_id[-8:]}",
                        case.amount, now.isoformat(), store,
                        via="simulated",
                    )
                elif outcome is Outcome.PROMISED:
                    due = world.promise_due(case, now)
                    case.promised_at = now.isoformat()
                    case.promise_due = due.isoformat()
                    case.touch()
                    store.upsert_case(case)
                    store.supersede_scheduled(case.case_id)
                    store.append_audit(AuditEvent(
                        actor="world",
                        event_type="promise.received",
                        case_id=case.case_id,
                        payload={
                            "due": case.promise_due,
                            "note": (
                                "(inbound reply captured via"
                                " /inbound/reply in live mode)"
                            ),
                        },
                    ))
                    check = Intervention(
                        case_id=case.case_id,
                        action_type=ActionType.CHECK_PROMISE,
                        scheduled_at=(
                            due + timedelta(hours=6)
                        ).isoformat(),
                        reasoning={
                            "strategy": "promise_to_pay_followup",
                        },
                    )
                    store.save_action(check)
                    if datetime.fromisoformat(
                        check.scheduled_at
                    ) <= horizon_end:
                        push(
                            datetime.fromisoformat(check.scheduled_at),
                            "action_due", check.action_id,
                        )
                    if world.keeps_promise(case):
                        pay_at = max(
                            now + timedelta(minutes=30),
                            due - timedelta(hours=2),
                        )
                        if pay_at <= horizon_end:
                            push(
                                pay_at, "promise_paid",
                                case.case_id,
                            )
                elif outcome is Outcome.OPTED_OUT:
                    write_off(case, "customer_opted_out", store)
                else:
                    fresh = plan_and_schedule(case, cfg, now, store)
                    if fresh:
                        when = datetime.fromisoformat(
                            fresh.scheduled_at
                        )
                        if when <= horizon_end:
                            push(
                                when, "action_due",
                                fresh.action_id,
                            )

            elif kind == "organic":
                case = store.get_case(payload)
                if case and case.status.value in ("open", "scheduled"):
                    mark_recovered(
                        case, f"pay_org_{case.case_id[-8:]}",
                        case.amount, now.isoformat(), store,
                        via="organic",
                    )

            elif kind == "promise_paid":
                case = store.get_case(payload)
                if case and case.status.value in ("open", "scheduled"):
                    mark_recovered(
                        case, f"pay_p2p_{case.case_id[-8:]}",
                        case.amount, now.isoformat(), store,
                        via="promised",
                    )

            elif kind == "sweep":
                for case in store.open_cases():
                    if should_write_off(case, now, cfg):
                        write_off(
                            case, "final_window_elapsed", store,
                        )
    finally:
        store.end_batch()

    # finalize: anything still unresolved at horizon end
    for case in store.open_cases():
        write_off(case, "horizon_end_unresolved", store)
    return world
