"""
auth/blackboard_login.py
Logs into clickup.up.ac.za and intercepts the full activity stream.
Waits for all paginated responses before returning.
"""

import json
import logging
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

log = logging.getLogger(__name__)

LOGIN_URL = "https://clickup.up.ac.za/webapps/login/"
STREAM_PAGE_URL = "https://clickup.up.ac.za/ultra/stream"
STREAM_API_URL = "https://clickup.up.ac.za/learn/api/v1/streams/ultra"


def get_stream_data(username: str, password: str) -> dict:
    """
    Logs in, navigates to the stream page, and intercepts all stream API
    responses. Merges paginated responses into a single result.
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

        all_entries = []
        providers = None
        last_raw = {}

        def handle_response(response):
            nonlocal providers, last_raw
            if STREAM_API_URL in response.url and response.request.method == "POST":
                try:
                    data = response.json()
                    entries = data.get("sv_streamEntries", [])
                    all_entries.extend(entries)
                    if providers is None:
                        providers = data.get("sv_providers")
                    last_raw = data
                    log.info(f"Intercepted stream response ({response.status}): {len(entries)} entries")
                except Exception as e:
                    log.warning(f"Could not parse stream response: {e}")

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
            raise RuntimeError(f"Login timed out. Error: {e}")

        # Navigate to stream page — triggers the API calls
        log.info("Navigating to stream page...")
        page.goto(STREAM_PAGE_URL, wait_until="networkidle", timeout=30000)

        # Wait generously for all lazy-loaded stream requests to complete
        page.wait_for_timeout(6000)

        browser.close()

        log.info(f"Stream capture complete. Total entries: {len(all_entries)}")

        # Return a merged result dict
        merged = dict(last_raw)
        merged["sv_streamEntries"] = all_entries
        if providers:
            merged["sv_providers"] = providers
        return merged
