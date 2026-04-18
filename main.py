#!/usr/bin/env python3
"""
UP Study Agent
Logs into clickup.up.ac.za, fetches the activity stream,
syncs deadlines to Google Calendar, and uses Claude to
extract events from announcements.
"""

import os
import sys
import logging
from datetime import datetime, timezone

from auth.blackboard_login import get_stream_data
from parser.stream_parser import parse_stream, parse_announcements
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

    log.info("Logging in and fetching stream...")
    raw = get_stream_data(
        username=os.environ["UP_USERNAME"],
        password=os.environ["UP_PASSWORD"],
    )
    log.info(f"Stream fetched. Total entries: {len(raw.get('sv_streamEntries', []))}")

    # ── 1. Direct deadlines ───────────────────────────────────────────────
    log.info("Parsing deadlines...")
    deadlines, _ = parse_stream(raw)
    log.info(f"Found {len(deadlines)} direct deadlines.")

    # ── 2. Claude announcement processing ────────────────────────────────
    log.info("Processing announcements with Claude...")
    announcements = parse_announcements(raw)
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    claude_events = []

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
        claude_events.extend(cal_items)

    log.info(f"Claude extracted {len(claude_events)} events from announcements.")

    # ── 3. Sync all to calendar ───────────────────────────────────────────
    all_events = deadlines + claude_events

    if not all_events:
        log.info("Nothing new to sync. All done.")
        return

    log.info(f"Syncing {len(all_events)} events to Google Calendar...")
    results = sync_to_calendar(all_events)
    log.info(f"Calendar: {results['created']} created, {results['skipped']} already existed.")

    log.info("=== UP Study Agent complete ===")


if __name__ == "__main__":
    main()
