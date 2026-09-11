"""Minimal dependency-free HTML dashboard from the report dict."""
from __future__ import annotations

import html
from math import cos, radians, sin
from typing import Any

from .measure import fmt_rupees
from .models import RecoveryCase

# Paytm brand palette: navy, cyan, light-cyan, then status colors
_PIE_COLORS = ["#00BAF2", "#002970", "#7FDBFF", "#fbbf24", "#f87171", "#94a3b8"]


def _pie_svg(spend_by_channel: dict[str, int]) -> str:
    """Dependency-free SVG pie: one slice per channel's share of spend."""
    total = sum(spend_by_channel.values())
    if total <= 0:
        return ""
    h = html.escape
    cx, cy, r = 80, 82, 60
    parts = ["<svg width='360' height='164' role='img' "
             "aria-label='Spend by channel'>"]
    angle = -90.0
    for i, (ch, paise) in enumerate(sorted(spend_by_channel.items(),
                                           key=lambda kv: -kv[1])):
        frac = paise / total
        sweep = frac * 360
        x1, y1 = cx + r * cos(radians(angle)), cy + r * sin(radians(angle))
        x2, y2 = cx + r * cos(radians(angle + sweep)), cy + r * sin(radians(angle + sweep))
        large = 1 if sweep > 180 else 0
        color = _PIE_COLORS[i % len(_PIE_COLORS)]
        if len(spend_by_channel) == 1 or sweep >= 359.999:
            parts.append(f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='{color}'/>")
        else:
            parts.append(
                f"<path d='M{cx},{cy} L{x1:.2f},{y1:.2f} "
                f"A{r},{r} 0 {large} 1 {x2:.2f},{y2:.2f} Z' fill='{color}'/>")
        share = f"{frac*100:.0f}%"
        lx = cx + (r + 14) * cos(radians(angle + sweep / 2))
        ly = cy + (r + 14) * sin(radians(angle + sweep / 2))
        parts.append(f"<text x='{lx:.0f}' y='{ly:.0f}' font-size='11' "
                     f"fill='#e2e8f0' text-anchor='middle'>{h(ch)} {share}</text>")
        angle += sweep
    parts.append("</svg>")
    return "".join(parts)


def render_dashboard(rep: dict[str, Any],
                     recent_cases: list[RecoveryCase] | None = None) -> str:
    h = html.escape
    b = rep["batch"]
    hd = rep["headline"]
    cost = rep["cost"]

    ci = hd["incremental_recovery_ci95_pp"]
    per_class_rows = "".join(
        f"<tr><td>{h(k)}</td><td>{v['n']}</td>"
        f"<td>{v['treatment_rate']*100:.1f}%</td>"
        f"<td>{v['control_rate']*100:.1f}%</td>"
        f"<td class='{'pos' if v['lift_pp'] > 0 else 'neg'}'>{v['lift_pp']:+.1f} pp</td></tr>"
        for k, v in sorted(rep["per_class"].items(), key=lambda kv: -kv[1]["n"])
    )
    blocked_rows = "".join(
        f"<tr><td>{h(k or 'unknown')}</td><td>{v}</td></tr>"
        for k, v in rep["policy_transparency"]["blocked_actions"].items()
    ) or "<tr><td colspan='2'>none</td></tr>"

    t_rate = hd["recovery_rate_treatment"] * 100
    c_rate = hd["recovery_rate_control"] * 100
    keep_rate = rep["promises"]["keep_rate"]
    keep_txt = f"{keep_rate * 100:.0f}%" if keep_rate is not None else "—"

    pie = _pie_svg(cost.get("cost_by_channel_paise") or {})
    pie_html = (f"<div class='card'><h2>Where the contact spend went</h2>{pie}</div>"
                if pie else "")

    cases_html = ""
    if recent_cases:
        parts = []
        for c in recent_cases:
            status_cls = ("pos" if c.status.value == "recovered"
                          else "neg" if c.status.value == "written_off" else "")
            reason = f" — {h(c.written_off_reason)}" if c.written_off_reason else ""
            parts.append(
                f"<tr><td><a href='/audit/{h(c.case_id)}'>{h(c.case_id)}</a></td>"
                f"<td>{h(c.failure_class.value)}</td>"
                f"<td>{fmt_rupees(c.amount)}</td>"
                f"<td class='{status_cls}'>{h(c.status.value)}{reason}</td>"
                f"<td>{fmt_rupees(c.recovered_amount) if c.recovered_amount else '—'}</td></tr>"
            )
        rows = "".join(parts)
        cases_html = (
            "<div class='card'><h2>Recent cases (click for the full audit trail)</h2>"
            "<table><tr><th>case</th><th>class</th><th>amount</th><th>status</th>"
            f"<th>recovered</th></tr>{rows}</table></div>"
        )

    _CSS = (
        "@font-face{"
        "  font-family:'Inter';"
        "  font-style:normal;font-weight:100 900;font-stretch:normal;"
        "  src:url('/static/vendor/fonts/InterVariable.woff2') format('woff2-variations'),"
        "      url('/static/vendor/fonts/InterVariable.woff2') format('woff2');"
        "  font-display:swap;"
        "}"
        "body{font-family:'Inter',-apple-system,'Segoe UI',Roboto,sans-serif;margin:2rem;"
        "  background:#002970;color:#e2e8f0}"
        ".card{background:#003a94;border-radius:12px;padding:1.2rem 1.5rem;"
        "  margin-bottom:1rem;box-shadow:0 2px 8px rgba(0,0,0,.25)}"
        "h1{font-size:1.3rem}h2{font-size:1rem;color:#7FDBFF}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem}"
        ".kpi{font-size:1.7rem;font-weight:700;color:#00BAF2}"
        ".sub{color:#9fc3e8;font-size:.85rem}"
        "table{width:100%;border-collapse:collapse;font-size:.9rem}"
        "th,td{padding:.45rem .6rem;text-align:left;border-bottom:1px solid #1f55a8}"
        "th{color:#7FDBFF;font-weight:600}"
        ".pos{color:#4ade80}.neg{color:#f87171}"
        "footer{color:#9fc3e8;font-size:.8rem;margin-top:1.5rem}"
        "disclaimer{color:#7a9fc0;font-size:.7rem;font-style:italic;margin-top:1rem;text-align:center}"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="theme-color" content="#002970">
<title>Revenue Recovery Agent — Report</title>
<style>{ _CSS }</style></head><body>
<h1>paytm Revenue Recovery Agent — Batch Report</h1>

<div class="card"><h2>Headline (treatment vs randomized control)</h2>
<div class="grid">
 <div><div class="kpi">{hd['incremental_recovery_pp']:+.1f} pp</div>
   <div class="sub">incremental recovery lift<br>95% CI [{ci[0]:+.1f}, {ci[1]:+.1f}]</div></div>
 <div><div class="kpi">{fmt_rupees(hd['incremental_money_paise'])}</div>
   <div class="sub">incremental money recovered (est.)</div></div>
 <div><div class="kpi">{t_rate:.1f}%</div>
   <div class="sub">treatment recovery rate<br>(control {c_rate:.1f}%)</div></div>
 <div><div class="kpi">{fmt_rupees(b['amount_at_risk_paise'])}</div>
   <div class="sub">amount at risk across {b['cases']} cases</div></div>
</div></div>

<div class="card"><h2>Honest costs</h2>
<div class="grid">
 <div><div class="kpi">{cost['contacts_executed']}</div>
   <div class="sub">customer contacts</div></div>
 <div><div class="kpi">{fmt_rupees(cost['spend_paise'])}</div>
   <div class="sub">total spend</div></div>
 <div><div class="kpi">{fmt_rupees(cost['cost_per_incremental_recovery_paise'])
   if cost['cost_per_incremental_recovery_paise'] is not None else '—'}</div>
   <div class="sub">cost / incremental recovery</div></div>
 <div><div class="kpi">{cost['redundant_contact_share']*100:.0f}%</div>
   <div class="sub">redundant-contact share (would have paid anyway)</div></div>
 <div><div class="kpi">{cost['opt_outs']}</div><div class="sub">opt-outs caused</div></div>
 <div><div class="kpi">{rep['promises']['received']}</div>
   <div class="sub">promises-to-pay captured<br>(keep rate {keep_txt})</div></div>
</div></div>

{pie_html}

<div class="card"><h2>Per failure class</h2>
<table><tr><th>class</th><th>n</th><th>treatment</th><th>control</th><th>lift</th></tr>
{per_class_rows}</table></div>

<div class="card"><h2>Policy gates that blocked actions (transparency)</h2>
<table><tr><th>reason</th><th>#actions</th></tr>{blocked_rows}</table></div>

{cases_html}

<style> a {{ color: #00BAF2; }} </style>
<footer>Every number on this page is computed from the audit trail in SQLite.
Treatment/control split is stratified by failure class at ingest.</footer>
<p class="disclaimer">Built as a concept for the Paytm Hackathon. Not an official Paytm product.</p>
</body></html>"""
