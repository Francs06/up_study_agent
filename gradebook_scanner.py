"""
gradebook_scanner.py
Fetches all gradebook items per course via the Blackboard grades API.
Extracts future-dated items that are unopened/unsubmitted.
"""

import logging
from datetime import datetime, timezone, timedelta
from playwright.sync_api import Page

log = logging.getLogger(__name__)

BASE_URL = "https://clickup.up.ac.za"

# Active S1 2026 courses to scan (excluding JCP)
COURSES = {
    "_188765_1": "MSD 210 S1 2026",
    "_190939_1": "MOW 217 S1 2026",
    "_191012_1": "WTW 256 S1 2026",
    "_190953_1": "WTW 258 S1 2026",
    "_189590_1": "MJJ 210 S1 2026",
    "_189473_1": "MPR 213 S1 2026",
}

# Statuses that mean the student hasn't done it yet
PENDING_STATUSES = {"UNOPENED", "NOT_ATTEMPTED", "IN_PROGRESS", "NO_STATUS"}

# Column categories to skip (not student-actionable)
SKIP_CATEGORIES = {
    "Total", "Total Attendance", "Overall Mark",
}


def _iso_to_dt(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def fetch_gradebook_for_course(page: Page, course_id: str, user_id: str) -> list[dict]:
    """
    Fetches all gradebook grade entries for a course via the browser session.
    Handles pagination automatically.
    """
    all_results = []
    url = (
        f"{BASE_URL}/learn/api/v1/courses/{course_id}/gradebook/grades"
        f"?userId={user_id}&limit=50&offset=0"
        f"&expand=column&includeNoGradeItems=true"
        f"&skipExternalGrade=true&skipKnowledgeCheck=true"
    )

    while url:
        data = page.evaluate("""async (url) => {
            const r = await fetch(url, {credentials: 'include'});
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return await r.json();
        }""", url)

        results = data.get("results", [])
        all_results.extend(results)

        next_page = data.get("paging", {}).get("nextPage", "")
        url = f"{BASE_URL}{next_page}" if next_page else None

    return all_results


def extract_deadlines_from_gradebook(
    results: list[dict],
    course_name: str,
    course_id: str,
) -> list[dict]:
    """
    Filter gradebook results to only actionable future deadlines
    that the student hasn't submitted yet.
    """
    now = datetime.now(tz=timezone.utc)
    deadlines = []
    seen_column_ids = set()

    for item in results:
        col = item.get("column") or {}
        column_id = col.get("id") or item.get("columnId")

        if not column_id or column_id in seen_column_ids:
            continue
        seen_column_ids.add(column_id)

        # Skip calculated/total columns
        if item.get("isCalculatedColumnGrade"):
            continue

        # Skip non-scorable
        if not col.get("scorable", True):
            continue

        # Skip deleted
        if col.get("deleted"):
            continue

        # Skip known noise categories
        col_name = col.get("columnName", "")
        category_title = (col.get("gradebookCategory") or {}).get("title", "")
        if category_title in SKIP_CATEGORIES or col_name in SKIP_CATEGORIES:
            continue

        # Check due date
        due_dt = _iso_to_dt(col.get("dueDate"))
        if not due_dt:
            continue

        # Skip if already past (more than 1 day ago)
        if due_dt < (now - timedelta(days=1)):
            continue

        # Check submission status — only include if not yet submitted
        submission_status = (item.get("submissionStatus") or {}).get("status", "NO_STATUS")
        attempts_left = item.get("attemptsLeft")
        last_attempt_id = item.get("lastAttemptId")

        # If already submitted (GRADED, COMPLETED) and no attempts left → skip
        if submission_status == "GRADED" and attempts_left == 0:
            continue

        # If graded and has a score → skip
        if item.get("status") == "GRADED" and last_attempt_id:
            continue

        deadlines.append({
            "title": f"{course_name}: {col_name}",
            "label": f"GRADEBOOK:{category_title or 'Assignment'}",
            "course_id": course_id,
            "se_id": f"gb_{column_id}",  # stable ID for dedup
            "due": due_dt,
            "url": f"{BASE_URL}/ultra/courses/{course_id}/outline",
            "event_type": "GRADEBOOK",
            "category": category_title,
        })

    return deadlines


def scan_all_courses(page: Page, user_id: str) -> list[dict]:
    """
    Scan all configured courses and return all future unsubmitted deadlines.
    """
    all_deadlines = []

    for course_id, course_name in COURSES.items():
        log.info(f"Scanning gradebook: {course_name}...")
        try:
            results = fetch_gradebook_for_course(page, course_id, user_id)
            deadlines = extract_deadlines_from_gradebook(results, course_name, course_id)
            log.info(f"  → {len(deadlines)} upcoming unsubmitted items")
            all_deadlines.extend(deadlines)
        except Exception as e:
            log.warning(f"  Failed to scan {course_name}: {e}")

    return all_deadlines
