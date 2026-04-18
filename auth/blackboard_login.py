"""
auth/blackboard_login.py
Headless login to clickup.up.ac.za using Playwright.
Navigates to the stream page first to establish session context,
then intercepts the actual stream API response.
"""

import json
import logging
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

log = logging.getLogger(__name__)

LOGIN_URL = "https://clickup.up.ac.za/webapps/login/"
STREAM_PAGE_URL = "https://clickup.up.ac.za/ultra/stream"
STREAM_API_URL = "https://clickup.up.ac.za/learn/api/v1/streams/ultra"
STREAM_PAYLOAD = {
    "sv_provider": "all",
    "forOverview": False,
    "sv_streamEntries": [],
}


def get_stream_data(username: str, password: str) -> dict:
    """
    Logs in, navigates to the stream page, and intercepts the stream API call
    that the page makes automatically on load.
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

        # Intercept and capture the stream API response
        captured = {}

        def handle_response(response):
            if STREAM_API_URL in response.url and response.request.method == "POST":
                try:
                    captured["data"] = response.json()
                    log.info(f"Intercepted stream API response ({response.status})")
                except Exception as e:
                    log.warning(f"Could not parse intercepted response: {e}")

        page = context.new_page()
        page.on("response", handle_response)

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

        # Navigate to the stream page — this triggers the API call automatically
        log.info("Navigating to stream page to trigger API call...")
        page.goto(STREAM_PAGE_URL, wait_until="networkidle", timeout=30000)

        # Give it a moment for any lazy-loaded requests
        page.wait_for_timeout(3000)

        browser.close()

        if "data" in captured:
            log.info("Stream data captured successfully.")
            return captured["data"]

        # Fallback: if interception missed it, raise a clear error
        raise RuntimeError(
            "Stream API call was not intercepted. "
            "The page may have changed structure."
        )
