"""The dashboard's JSX must compile — a syntax error there is a blank page at `/`.

Babel runs in the *browser* (vendored, type="text/babel" src=), so pytest never
sees syntax errors: every test could pass while `/` renders nothing. This test
compiles app/static/dashboard.jsx with the same vendored Babel the dashboard
ships, via Node (`node --version` is a hard requirement — fail, don't skip,
because a skip here is exactly how the blank page shipped).

The JSX lives in its own file since the Phase 1 split (dashboard.html is now a
thin shell that loads /static/dashboard.css + /static/dashboard.jsx); this test
fails loudly if the split files are missing or the shell no longer references
them.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parent.parent / "app" / "static" / "dashboard.html"
JSX = Path(__file__).resolve().parent.parent / "app" / "static" / "dashboard.jsx"
CSS = Path(__file__).resolve().parent.parent / "app" / "static" / "dashboard.css"
VENDOR = Path(__file__).resolve().parent.parent / "static" / "vendor"
REPO = Path(__file__).resolve().parent.parent


def test_dashboard_shell_references_split_files() -> None:
    """The shell must load the extracted CSS and JSX via /static/."""
    html = DASHBOARD.read_text(encoding="utf-8")
    assert '/static/dashboard.css' in html, "dashboard.html no longer links dashboard.css"
    assert re.search(
        r'<script[^>]+type="text/babel"[^>]+src="/static/dashboard\.jsx(\?v=\d+)?"', html
    ), "dashboard.html no longer loads dashboard.jsx via text/babel src"


def test_dashboard_jsx_compiles_with_vendored_babel() -> None:
    node = shutil.which("node")
    assert node is not None, "node is required to compile the dashboard JSX (it already is for Babel in the browser)"

    assert JSX.is_file(), f"{JSX} missing — the Phase 1 split extracted it from dashboard.html"
    script = JSX.read_text(encoding="utf-8")
    assert script.strip(), "dashboard.jsx is empty"

    js = (
        "const fs=require('fs');"
        "const mod=require(process.argv[1]);const babel=mod.default||mod;"
        "const src=fs.readFileSync(process.argv[2],'utf8');"
        "try{babel.transform(src,{presets:['react']});console.log('JSX OK')}"
        "catch(e){console.error('JSX FAIL:',e.message.split('\\n')[0]);process.exit(3)}"
    )
    res = subprocess.run(
        [node, "-e", js, str(VENDOR / "babel.min.js"), str(JSX)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"dashboard JSX failed to compile:\n{res.stdout}\n{res.stderr}"


def test_dashboard_css_exists_and_has_dark_theme() -> None:
    assert CSS.is_file(), f"{CSS} missing"
    css = CSS.read_text(encoding="utf-8")
    assert "paytm-navy" in css, "Paytm color system missing from dashboard.css"
    assert "prefers-reduced-motion" in css, "reduced-motion support missing"
    assert "Inter" in css, "Inter font stack missing from dashboard.css"
