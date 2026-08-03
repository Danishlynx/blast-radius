"""Capture the DataHub lineage view of the demo chain into examples/.

Logs in via the REST endpoint (no UI form driving) and injects the session
cookies into a headless browser.

Run: uv run --with playwright python scripts/screenshot_lineage.py
(needs `uv run --with playwright playwright install chromium` once)
"""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

FRONTEND = "http://localhost:9002"
URN = "urn:li:dataset:(urn:li:dataPlatform:duckdb,warehouse.main.fct_customer_features,PROD)"
OUT = Path(__file__).resolve().parents[1] / "examples" / "lineage.png"


def session_cookies() -> list[dict]:
    s = requests.Session()
    resp = s.post(
        f"{FRONTEND}/logIn",
        json={"username": "datahub", "password": "datahub"},
        timeout=15,
    )
    resp.raise_for_status()
    return [
        {"name": c.name, "value": c.value, "domain": "localhost", "path": "/"}
        for c in s.cookies
    ]


def main() -> int:
    cookies = session_cookies()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        context.add_cookies(cookies)
        page = context.new_page()
        lineage_url = f"{FRONTEND}/dataset/{urllib.parse.quote(URN, safe='')}/Lineage"
        page.goto(lineage_url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(8000)  # let the lineage graph render
        # Dismiss onboarding tooltips / overlays that cover the graph.
        for selector in (
            'button[aria-label="Close"]',
            ".ant-popover button .anticon-close",
            ".anticon-close",
        ):
            try:
                page.locator(selector).first.click(timeout=2000)
                page.wait_for_timeout(500)
            except Exception:
                pass
        page.keyboard.press("Escape")
        page.wait_for_timeout(1500)
        OUT.parent.mkdir(exist_ok=True)
        page.screenshot(path=str(OUT), full_page=False)
        browser.close()
    print(f"saved {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
