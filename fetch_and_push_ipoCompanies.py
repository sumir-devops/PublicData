"""
Fetch IPO company list from CDSC using Playwright.
Compares with existing GitHub file — only pushes if data has changed.

When run via GitHub Actions, GITHUB_TOKEN is injected automatically.
For local runs, set the GITHUB_TOKEN environment variable or hardcode below.

Install dependencies:
    pip install playwright requests
    playwright install chromium

Usage:
    # GitHub Actions: token injected via env automatically
    # Local:
    export GITHUB_TOKEN=ghp_yourTokenHere
    python fetch_and_push_ipo.py
"""

import asyncio
import json
import base64
import os
import sys
from datetime import datetime
from playwright.async_api import async_playwright
import requests


# ── Config ────────────────────────────────────────────────────────────────────
TARGET_URL   = "https://iporesult.cdsc.com.np/result/companyShares/fileUploaded"
HOME_URL     = "https://iporesult.cdsc.com.np/"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "ghp_yourTokenHere")  # ← or hardcode here for local use
GITHUB_OWNER = "sumir-devops"
GITHUB_REPO  = "PublicData"
GITHUB_PATH  = "IPOCompanies.json"
GITHUB_API   = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_PATH}"
# ──────────────────────────────────────────────────────────────────────────────


async def fetch_ipo_data():
    """Use Playwright (real Chromium) to bypass F5 WAF and fetch IPO data."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/147.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="Asia/Kathmandu",
            extra_http_headers={"Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8"},
        )

        page = await context.new_page()
        captured = {}

        async def handle_response(response):
            if TARGET_URL in response.url:
                print(f"[✓] Intercepted: {response.url}  (status {response.status})")
                try:
                    captured["data"] = await response.json()
                except Exception:
                    captured["raw"] = await response.text()

        page.on("response", handle_response)

        print(f"[→] Loading {HOME_URL} ...")
        await page.goto(HOME_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)

        if not captured:
            print("[→] Auto-intercept missed — calling API directly with browser session...")
            resp = await page.request.get(
                TARGET_URL,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": HOME_URL,
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-origin",
                },
            )
            print(f"    Status: {resp.status}")
            try:
                captured["data"] = await resp.json()
            except Exception:
                captured["raw"] = await resp.text()

        await browser.close()

        if "data" in captured:
            return captured["data"]
        if "raw" in captured:
            return captured["raw"]
        return None


def normalize(data) -> str:
    """Canonical JSON string for comparison — sort keys so order changes are ignored."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return data.strip()
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


def get_github_file():
    """Fetch current file from GitHub. Returns (content_str, sha) or (None, None)."""
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    resp = requests.get(GITHUB_API, headers=headers)

    if resp.status_code == 200:
        file_data = resp.json()
        content   = base64.b64decode(file_data["content"]).decode("utf-8")
        return content, file_data["sha"]
    elif resp.status_code == 404:
        return None, None
    else:
        print(f"[✗] GitHub GET failed: {resp.status_code} — {resp.text}")
        return None, None


def push_to_github(data, sha) -> bool:
    """Push data to GitHub. sha=None means create new file."""
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    }

    content_str = json.dumps(data, indent=2, ensure_ascii=False) if isinstance(data, (dict, list)) else str(data)
    content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")
    timestamp   = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    payload = {
        "message": f"chore: update IPO companies list [{timestamp}]",
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha

    print(f"[→] Pushing to github.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_PATH} ...")
    put_resp = requests.put(GITHUB_API, headers=headers, json=payload)

    if put_resp.status_code in (200, 201):
        commit = put_resp.json().get("commit", {})
        print(f"[✓] Pushed! Commit: {commit.get('sha', '')[:7]}  —  {commit.get('html_url', '')}")
        return True
    else:
        print(f"[✗] GitHub PUT failed: {put_resp.status_code}")
        print(f"    {put_resp.text}")
        return False


async def main():
    print("=" * 60)
    print("CDSC IPO Fetcher → GitHub Pusher (change-detection mode)")
    print("=" * 60)

    if GITHUB_TOKEN == "ghp_yourTokenHere":
        print("[✗] GITHUB_TOKEN not set. Set env var or replace in script.")
        sys.exit(1)

    # Step 1: Fetch fresh data from CDSC
    fresh_data = await fetch_ipo_data()
    if fresh_data is None:
        print("[✗] Failed to fetch IPO data. Aborting.")
        sys.exit(1)

    if isinstance(fresh_data, (dict, list)):
        body = fresh_data.get("body", fresh_data) if isinstance(fresh_data, dict) else fresh_data
        company_list = (
            body.get("companyShareList")
            or body.get("CompanyShareList")
            or (fresh_data if isinstance(fresh_data, list) else [])
        )
        print(f"[✓] Fetched {len(company_list)} companies from CDSC.")
    else:
        print(f"[✓] Fetched raw response ({len(fresh_data)} chars).")

    # Step 2: Fetch existing file from GitHub
    print(f"[→] Fetching existing file from GitHub...")
    existing_content, existing_sha = get_github_file()

    if existing_sha:
        print(f"[✓] Existing file found (SHA: {existing_sha[:7]}...)")
    else:
        print(f"[✓] No existing file — will create it.")

    # Step 3: Compare
    fresh_normalized    = normalize(fresh_data)
    existing_normalized = normalize(existing_content) if existing_content else None

    if existing_normalized and fresh_normalized == existing_normalized:
        print("\n[=] No changes detected. GitHub file is already up to date.")
        print("    Skipping push.")
        sys.exit(0)

    # Step 4: Push only if changed
    if existing_normalized:
        print("\n[!] Changes detected — pushing update to GitHub...")
    else:
        print("\n[+] New file — pushing to GitHub...")

    success = push_to_github(fresh_data, existing_sha)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
