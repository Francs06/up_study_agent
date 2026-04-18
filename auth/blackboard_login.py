"""
auth/blackboard_login.py
Headless login to clickup.up.ac.za using Playwright.
Returns a session cookie string that can be used for subsequent API calls.
"""

import logging
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

log = logging.getLogger(__name__)

LOGIN_URL = "https://clickup.up.ac.za/webapps/login/"
STREAM_URL = "https://clickup.up.ac.za/learn/api/v1/streams/ultra"


def get_session_cookie(username: str, password: str) -> dict:
    """
    Logs in via Playwright and returns the session cookies as a dict.
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

            # Fill in credentials
            page.fill("#user_id", username)
            page.fill("#password", password)
            page.click("#entry-login")

            # Wait for redirect after login
            page.wait_for_url("**/ultra/**", timeout=20000)
            log.info("Login redirect detected — authenticated.")

        except PlaywrightTimeout as e:
            # Take a screenshot to help debug if login fails in CI
            page.screenshot(path="login_failure.png")
            raise RuntimeError(
                f"Login timed out. Screenshot saved as login_failure.png. Error: {e}"
            )

        # Extract all cookies from the session
        cookies = context.cookies()
        browser.close()

        # Convert list of cookie dicts to a simple name→value dict
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        log.info(f"Captured {len(cookie_dict)} session cookies.")
        return cookie_dict
