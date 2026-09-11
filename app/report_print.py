"""Print-optimized report page (browser print to PDF).

A separate module keeps the FastAPI surface in main.py readable: this
endpoint renders a complete HTML document.
"""
from __future__ import annotations

from string import Template
from typing import Any

from fastapi.responses import HTMLResponse

from .measure import build_report, fmt_rupees

_CSS = """
  @font-face{
  font-family:'Inter';
  font-style:normal;font-weight:100 900;font-stretch:normal;
  src:url('/static/vendor/fonts/InterVariable.woff2') format('woff2-variations'),
      url('/static/vendor/fonts/InterVariable.woff2') format('woff2');
  font-display:swap;
 }
  body{font-family:'Inter','Georgia','Times New Roman',serif;max-width:720px;margin:32px auto;color:#111;line-height:1.45}
  h1{font-size:22px;border-bottom:2px solid #002970;padding-bottom:6px}
  h2{font-size:14px;margin:22px 0 6px;color:#002970}
  table{width:100%;border-collapse:collapse;font-size:12px}
  th,td{border:1px solid #ccc;padding:4px 8px;text-align:left}
  th{background:#eaf6fd;color:#002970}
  .hero{font-size:30px;font-weight:bold;color:#002970}
  .sub{color:#555;font-size:12px}
  .foot{margin-top:24px;font-size:10px;color:#777;border-top:1px solid #ddd;padding-top:8px}
  .disclaimer{margin-top:14px;font-size:9px;color:#888;font-style:italic;text-align:center}
  @media print{body{margin:12mm}}
"""

_TPL = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Recovery Report -- Incremental Lift</title>
<style>$css</style></head><body>
<h1>Paytm Revenue Recovery Agent -- Incremental Lift Report</h1>
<div class="hero">$inc_money incremental</div>
<div class="sub">+$lift_pp pp lift &middot; 95% CI [+$ci_lo, +$ci_hi] &middot;
treatment $rate_t vs control $rate_c &middot;
$ncases cases &middot; $risk at risk</div>

<h2>Honest costs</h2>
<table>
<tr><th>Metric</th><th>Value</th></tr>
<tr><td>Total contact spend</td><td>$spend</td></tr>
<tr><td>Cost per incremental recovery</td><td>$cpir</td></tr>
<tr><td>Redundant-contact share (would have paid anyway)</td><td>$redundant</td></tr>
<tr><td>Opt-outs honored</td><td>$optouts</td></tr>
</table>

<h2>Per-failure-class performance</h2>
<table>
<tr><th>Class</th><th>Treated</th><th>Treatment</th><th>Control</th><th>Lift</th></tr>
$rows
</table>

<h2>Audit integrity</h2>
<p style="font-size:12px">SHA-256 audit chain: <b>$chain_cell</b> &middot;
Every number above is computed from the append-only audit trail and reproducible with <code>--seed 42</code>.</p>

<div class="foot">Generated from the live audit trail &middot; methodology: randomized control groups, 2,000-rep percentile bootstrap &middot;
simulation parameters are stated assumptions (config.yaml &rarr; world:).</div>
<p class="disclaimer">Built as a concept for the Paytm Hackathon. Not an official Paytm product.</p>
<script>window.addEventListener('load',function(){if(location.search.indexOf('autoprint')>-1)window.print()})</script>
</body></html>"""

_EMPTY_ROW_TPL = '<tr><td colspan="$c">$t</td></tr>'


def render_print_report(store, cfg: dict[str, Any]) -> HTMLResponse:
    """Render the print-friendly incremental-lift one-pager."""
    rep = build_report(store.all_cases(), store.actions_rows(), cfg)
    h = rep["headline"]
    b = rep["batch"]
    cost = rep["cost"]
    per_class = rep.get("per_class", {})

    valid, _broken = store.verify_audit_chain()
    chain_cell = "VALID" if valid else "BROKEN"

    rows = []
    for cls, d in sorted(per_class.items(), key=lambda kv: -kv[1]["lift_pp"]):
        rows.append(
            "<tr><td>" + str(cls).replace("_", " ").title() + "</td>"
            f"<td>{d.get('n', 0)}</td>"
            f"<td>{d['treatment_rate'] * 100:.1f}%</td>"
            f"<td>{d['control_rate'] * 100:.1f}%</td>"
            f"<td>+{d['lift_pp']:.1f} pp</td></tr>"
        )
    rows_html = (chr(10).join(rows) if rows
                 else Template(_EMPTY_ROW_TPL).substitute(c="5", t="no data"))

    ci = h["incremental_recovery_ci95_pp"]
    cpir = cost.get("cost_per_incremental_recovery_paise")
    html = Template(_TPL).safe_substitute(
        css=_CSS,
        inc_money=fmt_rupees(h["incremental_money_paise"]),
        lift_pp=f"{h['incremental_recovery_pp']:.1f}",
        ci_lo=f"{ci[0]:.1f}", ci_hi=f"{ci[1]:.1f}",
        rate_t=f"{h['recovery_rate_treatment'] * 100:.1f}%",
        rate_c=f"{h['recovery_rate_control'] * 100:.1f}%",
        ncases=b["cases"],
        risk=fmt_rupees(b["amount_at_risk_paise"]),
        spend=fmt_rupees(cost["spend_paise"]),
        cpir=fmt_rupees(cpir) if cpir is not None else "&mdash;",
        redundant=f"{cost['redundant_contact_share'] * 100:.0f}%",
        optouts=cost["opt_outs"],
        rows=rows_html,
        chain_cell=chain_cell,
    )
    return HTMLResponse(html)
