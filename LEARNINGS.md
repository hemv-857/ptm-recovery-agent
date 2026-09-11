# Learnings

## What We Got Wrong

**1. LLM isn't always better.**
First version used Groq Qwen for classification. 400ms per case,
15 paise each. Rules did the same job in 5ms at zero cost. We proved
it by measuring both. Kept LLM for the 5% edge cases only.

**2. Gross recovery is meaningless.**
30% of recovered customers would have paid anyway. Most tools count
that as a win. We count it as a cost. Control groups tell you what
actually worked.

**3. Hinglish is its own language.**
"kal pakka", "ab nahi", "bas kar" — these aren't Hindi or English.
A 20-word vocabulary handles 99% of promise-to-pay responses. Regex
is explainable to compliance. A model isn't.

**4. Policy gates need to be pure functions.**
No side effects. No database calls. Input in, verdict out. That's
why they're testable. 134 tests pass because every gate is a
deterministic function.

**5. Audit trails are the product.**
Not the dashboard. Not the messages. The audit trail is what you show
auditors. SHA-256 chained. Append-only. Every decision logged with a
reason. That's how you pass compliance reviews.

## What Surprised Us

**Timeouts and invoices see the biggest lifts.**
Network timeout leads (86.4% vs 25.8%): the retry link lands while the
customer is still trying to pay. Invoice overdue is right behind
(73.2% vs 16.7%) because it escalates to humans. Phone calls work better
than messages for overdue invoices. The classifier knows when to stop
sending automated messages and start making calls.

**WhatsApp outperforms everything else.**
UCB1 bandit learned this fast. WhatsApp has higher open rates than
SMS or email in India. But SMS is the fallback when WhatsApp fails.
Voice is the nuclear option.

**Promise-to-pay is a goldmine.**
279 promises captured. 59% keep rate. ₹18.83 lakh recovered through
promises alone. "Kal pakka" isn't a brush-off — it's a commitment
the customer types with intent.

**CUSUM caught a drift we didn't expect.**
After minute 20, recovery rate dropped 20pp. The change-point
detector flagged it. Without monitoring, we'd have missed it.

## Design Decisions We'd Defend

| Decision | Why | Tradeoff |
|----------|-----|----------|
| Rules over ML for classification | Speed, cost, explainability | Misses novel patterns |
| UCB1 over Thompson Sampling | Simpler, faster convergence, auditable | Less "AI-sounding" |
| Control groups in the batch | Proves incremental value | 30% overhead on batch |
| SHA-256 audit chain | Tamper-evident, simple | Not a full blockchain |
| Hinglish via regex | 99% accuracy, zero latency | 20-word vocabulary ceiling |
| LLM as fallback only | Edge cases need it, bulk doesn't | LLM cost is non-zero |
| Pure function policy gates | Testable, deterministic | No runtime adaptability |
| Webhook sync <12ms | Fast failure detection | Sync blocks the webhook handler |

## What We'd Do Differently

**Start with measurement, not recovery.**
We built the recovery pipeline first, then added control groups.
Should've done measurement first. The recovery is only as good as
your ability to prove it worked.

**Budget the LLM cost early.**
We didn't track LLM spend until it was already 300 rupees. Should've
had a budget gate from day one. Now we do.

**Test adversarial inputs from the start.**
We added adversarial testing late. Prompt injection, role hijacking,
consent bypass — these should've been in the test suite from day one.

## What We'd Tell Other Builders

1. Measure incrementally or don't measure at all.
2. Rules before ML. Prove rules fail before reaching for models.
3. The audit trail is the product. Everything else is a feature.
4. Control groups are cheap. Insights are expensive.
5. Hinglish parsing is a business problem, not an NLP problem.
