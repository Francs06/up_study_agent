#!/usr/bin/env python3
"""
UP Study Agent
Logs into clickup.up.ac.za, fetches the activity stream,
and syncs deadlines/tasks to Google Calendar and Google Tasks.
"""

import os
import sys
import logging
from datetime import datetime

from auth.blackboard_login import get_stream_data
from parser.stream_parser import parse_stream
from gcalendar.google_calendar import sync_to_calendar
from tasks.google_tasks import sync_to_tasks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)


def main():
    log.info("=== UP Study Agent starting ===")
    log.info(f"Run time: {datetime.now().isoformat()}")

    # ── 1. Login + fetch stream in one browser session ────────────────────────
    log.info("Logging in and fetching stream...")
    raw = get_stream_data(
        username=os.environ["UP_USERNAME"],
        password=os.environ["UP_PASSWORD"],
    )
    log.info(f"Stream fetched. Total entries: {len(raw.get('sv_streamEntries', []))}")

    # ── 2. Parse actionable items ─────────────────────────────────────────────
    log.info("Parsing actionable items...")
    deadlines, tasks = parse_stream(raw)
    log.info(f"Found {len(deadlines)} calendar events and {len(tasks)} tasks.")

    if not deadlines and not tasks:
        log.info("Nothing new to sync. All done.")
        return

    # ── 3. Sync to Google Calendar ────────────────────────────────────────────
    if deadlines:
        log.info("Syncing deadlines to Google Calendar...")
        cal_results = sync_to_calendar(deadlines)
        log.info(f"Calendar: {cal_results['created']} created, {cal_results['skipped']} already existed.")

    # ── 4. Sync to Google Tasks ───────────────────────────────────────────────
    if tasks:
        log.info("Syncing tasks to Google Tasks...")
        task_results = sync_to_tasks(tasks)
        log.info(f"Tasks: {task_results['created']} created, {task_results['skipped']} already existed.")

    log.info("=== UP Study Agent complete ===")


if __name__ == "__main__":
    main()
