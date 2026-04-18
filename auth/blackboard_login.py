"""
auth/blackboard_login.py
Logs into clickup.up.ac.za, intercepts the stream, and returns
both the stream data and an active page for further API calls.
"""

import logging
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

log = logging.getLogger(__name__)

LOGIN_URL = "https://clickup.up.ac.za/webapps/login/"
STREAM_PAGE_URL = "https://clickup.up.ac.za/ultra/stream"
STREAM_API_URL = "https://clickup.up.ac.za/learn/api/v1/streams/ultra"


def login_and_get_session(username: str, password: str):
    """
    Logs in and returns (playwright_context, page, stream_data, user_id).
    Caller is responsible for closing the browser.
    """
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
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
    user_id = None

    def handle_response(response):
        nonlocal providers, last_raw, user_id
        if STREAM_API_URL in response.url and response.request.method == "POST":
            try:
                data = response.json()
                entries = data.get("sv_streamEntries", [])
                all_entries.extend(entries)
                if providers is None:
                    providers = data.get("sv_providers")
                last_raw = data
                # Extract user_id from extras
                if not user_id:
                    for course in data.get("sv_extras", {}).get("sx_courses", []):
                        pass  # user_id comes from memberships URL
                log.info(f"Intercepted stream response ({response.status}): {len(entries)} entries")
            except Exception as e:
                log.warning(f"Could not parse stream response: {e}")

        # Grab user_id from memberships API call
        if not user_id and "memberships" in response.url and "users/" in response.url:
            try:
                import re
                match = re.search(r"/users/(_\d+_\d+)/", response.url)
                if match:
                    user_id = match.group(1)
                    log.info(f"Captured user_id: {user_id}")
            except Exception:
                pass

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

    log.info("Navigating to stream page...")
    page.goto(STREAM_PAGE_URL, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(6000)

    log.info(f"Stream capture complete. Total entries: {len(all_entries)}")

    merged = dict(last_raw)
    merged["sv_streamEntries"] = all_entries
    if providers:
        merged["sv_providers"] = providers

    return playwright, browser, page, merged, user_id or "_1321173_1"


def get_stream_data(username: str, password: str) -> dict:
    """Convenience wrapper that closes the browser and returns only stream data."""
    playwright, browser, page, stream_data, _ = login_and_get_session(username, password)
    browser.close()
    playwright.stop()
    return stream_data
