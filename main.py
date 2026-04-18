#!/usr/bin/env python3
"""
UP Study Agent
Logs into clickup.up.ac.za, fetches the activity stream,
and syncs deadlines to Google Calendar.
"""

import os
import sys
import logging
from datetime import datetime

from auth.blackboard_login import get_stream_data
from parser.stream_parser import parse_stream
from gcalendar.google_calendar import sync_to_calendar

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

    log.info("Parsing actionable items...")
    deadlines, _ = parse_stream(raw)
    log.info(f"Found {len(deadlines)} calendar events.")

    if not deadlines:
        log.info("Nothing new to sync. All done.")
        return

    log.info("Syncing to Google Calendar...")
    results = sync_to_calendar(deadlines)
    log.info(f"Calendar: {results['created']} created, {results['skipped']} already existed.")

    log.info("=== UP Study Agent complete ===")


if __name__ == "__main__":
    main()
