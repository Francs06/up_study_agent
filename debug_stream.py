import os, json, logging
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

LOGIN_URL = "https://clickup.up.ac.za/webapps/login/"
# Blackboard calendar API - fetch events for next 60 days
CALENDAR_URL = "https://clickup.up.ac.za/learn/api/v1/calendar/items?since={since}&until={until}&limit=100"

username = os.environ["UP_USERNAME"]
password = os.environ["UP_PASSWORD"]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
    page.fill("#user_id", username)
    page.fill("#password", password)
    page.click("#entry-login")
    page.wait_for_url("**/ultra/**", timeout=20000)
    logging.info("Logged in.")

    # Try the calendar API
    since = "2026-04-18T00:00:00.000Z"
    until = "2026-06-30T00:00:00.000Z"
    url = f"https://clickup.up.ac.za/learn/api/v1/calendar/items?since={since}&until={until}&limit=100"

    result = page.evaluate("""async (url) => {
        const r = await fetch(url, {credentials: 'include'});
        return {status: r.status, body: await r.text()};
    }""", url)

    logging.info(f"Calendar API status: {result['status']}")

    with open("debug_output.txt", "w") as f:
        f.write(f"Status: {result['status']}\n\n")
        f.write(result['body'][:5000])

    browser.close()
    print("Done. Check debug_output.txt")
