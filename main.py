#!/usr/bin/env python3
"""
UP Study Agent — full pipeline:
1. Login, fetch stream + gradebook
2. Crawl course content, read new PDFs
3. Process announcements with Claude
4. Build dashboard.json for GitHub Pages
5. Sync to Google Calendar
6. Commit state.json + dashboard.json back to repo
"""

import os
import sys
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from auth.blackboard_login import login_and_get_session
from parser.stream_parser import parse_stream, parse_announcements
from gradebook_scanner import scan_all_courses, COURSES as GB_COURSES
from content_crawler import crawl_all_courses, load_state, save_state, COURSE_ROOTS
from dashboard_builder import build_dashboard, COURSE_META
from gcalendar.google_calendar import sync_to_calendar
from claude_processor import process_announcement, announcement_events_to_calendar

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

DASHBOARD_FILE = Path("docs/dashboard.json")


def format_deadline_for_dashboard(item: dict, course_name: str = None) -> dict:
    """Convert a calendar item to dashboard format."""
    due = item.get("due")
    return {
        "title": item.get("title", ""),
        "due_date": due.strftime("%Y-%m-%d") if due else None,
        "due_time": due.strftime("%H:%M") if due else None,
        "course_id": item.get("course_id", ""),
        "course_name": course_name or item.get("course_id", ""),
        "label": item.get("label", ""),
        "event_type": item.get("event_type", ""),
        "url": item.get("url", ""),
    }


def commit_files():
    """Commit state.json and dashboard.json back to the repo."""
    try:
        subprocess.run(["git", "config", "user.email", "agent@up-study-agent"], check=True)
        subprocess.run(["git", "config", "user.name", "UP Study Agent"], check=True)
        subprocess.run(["git", "add", "state.json", "docs/dashboard.json"], check=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            capture_output=True
        )
        if result.returncode != 0:  # there are changes
            subprocess.run([
                "git", "commit", "-m",
                f"agent: update dashboard {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            ], check=True)
            subprocess.run(["git", "push"], check=True)
            log.info("Committed and pushed state + dashboard.")
        else:
            log.info("No changes to commit.")
    except Exception as e:
        log.warning(f"Git commit failed: {e}")


def main():
    log.info("=== UP Study Agent starting ===")
    log.info(f"Run time: {datetime.now().isoformat()}")

    state = load_state()
    all_calendar_events = []
    all_deadlines_for_dashboard = []

    # ── 1. Login ──────────────────────────────────────────────────────────
    log.info("Logging in...")
    playwright, browser, page, raw, user_id = login_and_get_session(
        username=os.environ["UP_USERNAME"],
        password=os.environ["UP_PASSWORD"],
    )
    log.info(f"Stream: {len(raw.get('sv_streamEntries', []))} entries")

    try:
        # ── 2. Stream deadlines ───────────────────────────────────────────
        log.info("Parsing stream deadlines...")
        deadlines, _ = parse_stream(raw)
        log.info(f"Stream: {len(deadlines)} deadlines")
        all_calendar_events.extend(deadlines)

        # Build course name map
        course_names = {}
        for c in raw.get("sv_extras", {}).get("sx_courses", []):
            course_names[c["id"]] = c.get("name", c["id"])

        for item in deadlines:
            cname = course_names.get(item.get("course_id", ""), "")
            all_deadlines_for_dashboard.append(format_deadline_for_dashboard(item, cname))

        # ── 3. Gradebook scan ─────────────────────────────────────────────
        log.info("Scanning gradebooks...")
        gb_deadlines = scan_all_courses(page, user_id)
        log.info(f"Gradebook: {len(gb_deadlines)} deadlines")
        all_calendar_events.extend(gb_deadlines)

        for item in gb_deadlines:
            cid = item.get("course_id", "")
            cname = COURSE_META.get(cid, {}).get("full", cid)
            all_deadlines_for_dashboard.append(format_deadline_for_dashboard(item, cname))

        # ── 4. Content crawl + PDF reading ────────────────────────────────
        log.info("Crawling course content...")
        content_insights = crawl_all_courses(page, state)
        total_files = sum(len(v) for v in content_insights.values())
        log.info(f"Content: {total_files} new files analysed")

        # Extract any deadlines Claude found in documents
        for course_id, insights in content_insights.items():
            for insight in insights:
                for dl in insight.get("deadlines", []):
                    if dl.get("date"):
                        cname = COURSE_META.get(course_id, {}).get("full", course_id)
                        all_deadlines_for_dashboard.append({
                            "title": f"{insight['filename']}: {dl['description']}",
                            "due_date": dl["date"],
                            "due_time": None,
                            "course_id": course_id,
                            "course_name": cname,
                            "label": "From document",
                            "event_type": "DOCUMENT",
                        })

        # ── 5. Announcement processing ────────────────────────────────────
        log.info("Processing announcements...")
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
            for item in cal_items:
                all_deadlines_for_dashboard.append(
                    format_deadline_for_dashboard(item, ann["course_name"])
                )

        all_calendar_events.extend(claude_events)
        log.info(f"Announcements: {len(claude_events)} events extracted")

    finally:
        browser.close()
        playwright.stop()

    # ── 6. Build dashboard ────────────────────────────────────────────────
    log.info("Building dashboard...")
    dashboard = build_dashboard(
        deadlines=all_deadlines_for_dashboard,
        content_insights=content_insights if 'content_insights' in dir() else {},
        state=state,
    )
    DASHBOARD_FILE.parent.mkdir(exist_ok=True)
    with open(DASHBOARD_FILE, "w") as f:
        json.dump(dashboard, f, indent=2, default=str)
    log.info("Dashboard written.")

    # ── 7. Google Calendar sync ───────────────────────────────────────────
    if all_calendar_events:
        results = sync_to_calendar(all_calendar_events)
        log.info(f"Calendar: {results['created']} created, {results['skipped']} skipped")
    else:
        log.info("No calendar events to sync.")

    # ── 8. Save state + commit ────────────────────────────────────────────
    save_state(state)
    commit_files()

    log.info("=== UP Study Agent complete ===")


if __name__ == "__main__":
    main()
