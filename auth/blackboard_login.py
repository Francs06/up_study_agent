"""
auth/blackboard_login.py
Headless login to clickup.up.ac.za using Playwright.
Fetches the activity stream directly within the browser session to avoid
XSRF token issues with external requests.
"""

import json
import logging
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

log = logging.getLogger(__name__)

LOGIN_URL = "https://clickup.up.ac.za/webapps/login/"
STREAM_URL = "https://clickup.up.ac.za/learn/api/v1/streams/ultra"
STREAM_PAYLOAD = {
    "sv_provider": "all",
    "forOverview": False,
    "sv_streamEntries": [],
}


def get_stream_data(username: str, password: str) -> dict:
    """
    Logs in via Playwright and fetches the activity stream directly
    from within the browser session, bypassing XSRF issues.
    Returns the parsed stream JSON.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        try:
            log.info(f"Navigating to {LOGIN_URL}")
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)

            page.fill("#user_id", username)
            page.fill("#password", password)
            page.click("#entry-login")

            page.wait_for_url("**/ultra/**", timeout=20000)
            log.info("Login redirect detected — authenticated.")

        except PlaywrightTimeout as e:
            page.screenshot(path="login_failure.png")
            raise RuntimeError(f"Login timed out. Screenshot saved. Error: {e}")

        # Use the browser's fetch API — automatically includes cookies and XSRF
        log.info("Fetching activity stream via browser session...")
        result = page.evaluate(
            """async ([url, payload]) => {
                const response = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                    credentials: 'include'
                });
                if (!response.ok) {
                    throw new Error('Stream fetch failed: ' + response.status + ' ' + response.statusText);
                }
                return await response.json();
            }""",
            [STREAM_URL, STREAM_PAYLOAD],
        )

        browser.close()
        log.info("Stream fetched successfully.")
        return result
