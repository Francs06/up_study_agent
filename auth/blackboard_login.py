"""
auth/blackboard_login.py
Headless login to clickup.up.ac.za using Playwright.
Grabs the XSRF token from cookies and includes it in the stream request header.
"""

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
    Logs in via Playwright, extracts the XSRF token, and fetches the stream.
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

        # Extract XSRF token from cookies
        cookies = context.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        log.info(f"Cookies available: {list(cookie_dict.keys())}")

        xsrf_token = (
            cookie_dict.get("XSRF-TOKEN")
            or cookie_dict.get("xsrf")
            or cookie_dict.get("bb-xsrf-token")
            or ""
        )
        log.info(f"XSRF token found: {'yes' if xsrf_token else 'NO - will try without'}")

        # Fetch stream with explicit XSRF header
        log.info("Fetching activity stream...")
        result = page.evaluate(
            """async ([url, payload, xsrf]) => {
                const headers = {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                };
                if (xsrf) {
                    headers['X-Blackboard-XSRF'] = xsrf;
                    headers['X-XSRF-TOKEN'] = xsrf;
                }
                const response = await fetch(url, {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify(payload),
                    credentials: 'include'
                });
                if (!response.ok) {
                    const text = await response.text();
                    throw new Error('Stream fetch failed: ' + response.status + ' ' + text.substring(0, 200));
                }
                return await response.json();
            }""",
            [STREAM_URL, STREAM_PAYLOAD, xsrf_token],
        )

        browser.close()
        log.info("Stream fetched successfully.")
        return result
