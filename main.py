#!/usr/bin/env python3
"""
UP Study Agent — main entry point.
1. Logs in once, keeps browser open
2. Fetches stream → direct deadlines + announcements
3. Scans gradebook per course → reading assignments etc.
4. Runs Claude on announcements → extracts event dates
5. Syncs everything to Google Calendar
"""

import os
import sys
import logging
from datetime import datetime, timezone

from auth.blackboard_login import login_and_get_session
from parser.stream_parser import parse_stream, parse_announcements
from gradebook_scanner import scan_all_courses
from gcalendar.google_calendar import sync_to_calendar
from claude_processor import process_announcement, announcement_events_to_calendar

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)


def main():
    log.info("=== UP Study Agent starting ===")
    log.info(f"Run time: {datetime.now().isoformat()}")

    # ── 1. Login — keep browser open for gradebook scan ──────────────────
    log.info("Logging in...")
    playwright, browser, page, raw, user_id = login_and_get_session(
        username=os.environ["UP_USERNAME"],
        password=os.environ["UP_PASSWORD"],
    )
    log.info(f"Stream fetched. Total entries: {len(raw.get('sv_streamEntries', []))}")

    all_events = []

    try:
        # ── 2. Direct stream deadlines ────────────────────────────────────
        log.info("Parsing stream deadlines...")
        deadlines, _ = parse_stream(raw)
        log.info(f"Found {len(deadlines)} stream deadlines.")
        all_events.extend(deadlines)

        # ── 3. Gradebook deep scan ────────────────────────────────────────
        log.info("Scanning gradebooks...")
        gb_deadlines = scan_all_courses(page, user_id)
        log.info(f"Found {len(gb_deadlines)} gradebook deadlines.")
        all_events.extend(gb_deadlines)

        # ── 4. Claude announcement processing ────────────────────────────
        log.info("Processing announcements with Claude...")
        announcements = parse_announcements(raw)
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

        for ann in announcements:
            extracted = process_announcement(
                title=ann["title"],
                body=ann["body"],
                course_name=ann["course_name"],
                today=today,
            )
            cal_items = announcement_events_to_calendar(
                extracted, ann["course_name"], ann["se_id"]
            )
            all_events.extend(cal_items)

        log.info(f"Total events to sync: {len(all_events)}")

    finally:
        browser.close()
        playwright.stop()

    # ── 5. Sync to Google Calendar ────────────────────────────────────────
    if not all_events:
        log.info("Nothing new to sync. All done.")
        return

    results = sync_to_calendar(all_events)
    log.info(f"Calendar: {results['created']} created, {results['skipped']} already existed.")
    log.info("=== UP Study Agent complete ===")


if __name__ == "__main__":
    main()
