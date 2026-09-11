# Demo Storyboard

## [0:00–0:15] Intro

**Screen:** Your face (camera on)

**Say this:**

"Hi, I'm Hemang. I'm a second-year student at the Faculty of
Technology, University of Delhi. I've been thinking about this
problem for a while: how do you know if your revenue recovery
system actually works?

Most tools report gross numbers. I wanted to measure incremental
lift. This is what I built."

---

## [0:15–0:35] What This Is

**Screen:** README — top section

**Say this:**

"This is a revenue recovery agent for paytm. It detects failed
payments, decides what to do about each one, executes the action,
and measures whether the recovery actually created value.

Most recovery tools report gross money recovered. We measure
incremental lift using randomized control groups — the same
methodology medicine uses to prove drugs work.

Core result: +49.0 percentage point lift over control. ₹66.58 lakh
incremental recovery. 95% confidence interval. Every number
reproducible."

---

## [0:35–1:10] How It Works

**Screen:** Architecture diagram in README

```
classifier → selector → policy gate → executor
                ↑                          │
                └──── audit trail ◀────────┘
```

**Say this:**

"Five stages. Classifier maps error codes to failure types and picks
a strategy — retry on salary day, send an alternate link, escalate
to humans. Selector picks the channel — WhatsApp, SMS, email, voice —
using a UCB1 bandit that learns what works.

Policy gate is pure functions — quiet hours, attempt caps, opt-outs,
human approval above ₹25K. Deterministic, testable, no side effects.

Executor sends. Audit trail records every decision — append-only,
SHA-256 chained, can't be faked."

---

## [1:10–1:50] Live Walkthrough

**Screen:** Dashboard + terminal side by side

**Run through a case:**

"Let's walk through one case.

Customer tries to pay ₹1,500. Card fails — insufficient funds.
Classifier: 97% confidence. Selector picks WhatsApp — bandit
learned it has the highest recovery rate for this type.

Policy gate checks: quiet hours? No. First attempt? Yes. Under ₹25K?
Yes. Message goes out. Here's the preview — character count,
button layout, what the customer actually sees.

Customer replies 'kal pakka.' Parser catches it, tracks the promise,
pauses the follow-up ladder. Few days later, payment comes in.
Case closed. Nine events logged, every step traceable."

---

## [1:50–2:40] The Numbers

**Screen:** Dashboard hero section

**Go through each metric:**

"So here's what happened. ₹2 crore at risk across 2,000 cases.

The group that got our recovery messages — 70.6% paid. The control
group that got nothing — 21.7% paid. That 49 point gap is value
this agent created.

We're 95% confident the real number is between 45 and 53 points.
That's ₹67 lakh incremental.

Contact cost was ₹472 total. That's ₹0.69 per incremental recovery.

We also captured 279 promises — 'kal pakka' type replies. 59% of
those actually paid. ₹19 lakh came through promises alone.

21 people opted out. All honored immediately. Zero silent failures.

And the honest number — 30% of people we recovered would've paid
anyway. We count that as a cost, not a win. Most tools don't."

---

## [2:40–3:10] Per-Class Strategy

**Screen:** Dashboard — scroll to per-class breakdown

**Say this:**

"Different failures, different strategies. Insufficient funds —
salary-cycle retries work because people get paid on the 1st and 5th.
Hard decline — never re-charge the same card, send an alternate
payment link instead. Invoice overdue — escalate to humans, phone
calls work better than messages here. Highest lift of any class.

The classifier picks the strategy. The strategy determines
the outcome."

---

## [3:10–3:40] What We Learned

**Screen:** Just talk, or show code if you want

**Tell this story:**

"First version sent every failure to an LLM for classification.
Groq Qwen. Seemed like the right thing to do.

Then we measured. 400 milliseconds per case. Fifteen paise each.
Two thousand cases — three hundred rupees. And the LLM gave
contradictory results on similar error codes.

We tried rules instead. Same accuracy. Five milliseconds. Zero cost.

So we kept the LLM for edge cases only — the 5% of unusual error
texts that rules can't parse. Now the whole system costs ten rupees
instead of three hundred. Provider switching in the dashboard
toggles between mock, Ollama, and Claude — you can test each
provider live.

The lesson: measure the tradeoff. Don't assume AI is better.
Sometimes rules are. We proved it."

---

## [4:10–4:50] Compliance and Security

**Screen:** Security tab + case audit trail

**Say this:**

"Every case has an immutable audit trail. SHA-256 chained —
you can verify the chain hasn't been tampered with from the
dashboard.

webhook received → classified → policy verdict → strategy selected →
message sent → customer replied → promise tracked → follow-up
scheduled → payment confirmed → measured as incremental.

You can pull up any case and trace the full reasoning chain.
In production, this is how you pass audits.

Policy gate blocks are logged with reasons. Opt-outs are honored.
Quiet hours are enforced. Human approval required above ₹25K.
No silent failures.

Security posture: threat model with mitigations, adversarial
LLM testing — prompt injection attempts are classified as
UNKNOWN with low confidence. The LLM has no tool access, no
credentials, no PII. Every money action requires compliance
gate pass."

---

## [4:50–5:00] Close

**Screen:** Back to dashboard hero

**Say this:**

"Revenue recovery is unsolved because most tools optimize for
appearing to recover money, not for creating value.

We built a system that measures what matters, explains every
decision, respects compliance, and is honest about its limitations.

The numbers are reproducible. The audit trail is immutable.
The control group is real."

---

## Notes

- **Total time:** ~5:00
- **Tone:** Direct, technical, no hype. Let the numbers speak.
- **Pacing:** Slow during numbers. Fast during architecture.
- **The 30% line.** Say it, pause. That's the credibility moment.

## Features Mentioned

| Feature | Where | How |
|---------|-------|-----|
| SHAP explainability | Architecture | "Every prediction carries SHAP" |
| UCB1 bandit | Architecture + Walkthrough | "Contextual bias, live scores" |
| Webhook ingestion | Architecture | "<12ms sync, background diagnosis" |
| Audit trail (SHA-256) | Architecture + Security | "Append-only, chained, verifiable" |
| WhatsApp concierge | Walkthrough | "Live preview, character count" |
| CUSUM detector | Walkthrough | "Monitors recovery rate" |
| Decision inspector | Walkthrough | "EV calculations, rejected alternatives" |
| ROI calculator | Numbers | "Merchants plug in own numbers" |
| Recovery funnel | Numbers | "Drop-off tracking" |
| Adversarial testing | Security | "Prompt injection → UNKNOWN" |
| Multi-currency | Q&A | "USD, EUR, INR with live rates" |
| Auto-pilot | (dashboard visible) | Topbar toggle |
| Settings editor | (dashboard visible) | Engine tab |
| Approval queue | Numbers | "21 opt-outs honored" |

## Q&A Prep

**"Why not use paytm's built-in recovery?"**
"paytm doesn't measure incremental. They report gross. We prove
the recovery created value."

**"Is 2,000 cases statistically significant?"**
"The CI tells you. 44.9 to 52.8. Narrow enough to be confident.
Wide enough to be honest. Bootstrap is seeded — reproduce it yourself."

**"What does this cost in production?"**
"₹472 contact spend for ₹66.58 L incremental. ₹0.69 per incremental
recovery. Contact costs come from config — merchants set their own."

**"Why rules over ML for Hinglish?"**
"20-word vocabulary. 99% accuracy. Zero latency. Explainable.
You need to tell compliance why a message went out at 2 AM.
Regex is easier to audit than a model."

**"What about international payments?"**
"Multi-currency support is built in — USD, EUR, INR with live
rate conversion. The measurement framework is currency-agnostic."
