"""
parser/stream_parser.py
Fetches and parses the Blackboard Ultra activity stream.
Extracts deadlines (→ Google Calendar) and to-do tasks (→ Google Tasks).
"""

import logging
import requests
from datetime import datetime, timezone

log = logging.getLogger(__name__)

STREAM_URL = "https://clickup.up.ac.za/learn/api/v1/streams/ultra"

# Event types we care about and how to route them
CALENDAR_EVENT_TYPES = {
    "AS:DUE":      "Assignment Due",
    "AS:OVERDUE":  "Assignment Overdue",
    "UA:OVERDUE":  "Item Overdue",
    "GB:OVERDUE":  "Item Overdue",
    "SC:OVERDUE":  "Item Overdue",
    "PE:DUE":      "Peer Review Due",
    "SU:OVERDUE":  "Survey Overdue",
    "PS:PS_AVAIL": "Assessment Available",
    "SU:SU_AVAIL": "Survey Available",
}

TASK_EVENT_TYPES = {
    "AS:DUE":                  "Assignment",
    "PE:DUE":                  "Peer Review",
    "SU:SU_AVAIL":             "Survey",
    "PS:PS_AVAIL":             "Assessment",
    "AS:AS_GA_AVAIL_RESEND":   "Group Assignment",
    "PE:PE_AVAIL_RESEND":      "Peer Review",
}

ANNOUNCEMENT_PROVIDERS = {"bb-announcement", "bb_disc"}


def fetch_stream(cookie_dict: dict) -> dict:
    """POST to the stream endpoint using the session cookies."""
    session = requests.Session()
    for name, value in cookie_dict.items():
        session.cookies.set(name, value, domain="clickup.up.ac.za")

    headers = {
        "Content-Type": "application/json",
        "X-Blackboard-XSRF": cookie_dict.get("XSRF-TOKEN", ""),
        "Referer": "https://clickup.up.ac.za/ultra/stream",
    }

    # Payload mirrors what the browser sends
    payload = {
        "sv_provider": "all",
        "forOverview": False,
        "sv_streamEntries": [],
    }

    response = session.post(STREAM_URL, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def _ms_to_dt(ms: int) -> datetime | None:
    """Convert millisecond timestamp to UTC datetime."""
    if not ms or ms <= 0 or ms > 9007199254740990:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _extract_title(entry: dict) -> str:
    isd = entry.get("itemSpecificData", {}) or {}
    return (
        isd.get("title")
        or (isd.get("notificationDetails") or {}).get("announcementTitle")
        or (isd.get("assessmentStreamEntryDetails") or {}).get("title")
        or "Untitled Item"
    )


def _extract_course_id(entry: dict) -> str:
    return entry.get("se_courseId", "unknown_course")


def _extract_event_type(entry: dict) -> str | None:
    isd = entry.get("itemSpecificData", {}) or {}
    nd = isd.get("notificationDetails") or {}
    return nd.get("eventType") or isd.get("eventType")


def parse_stream(raw: dict) -> tuple[list[dict], list[dict]]:
    """
    Parse the raw stream JSON into two lists:
    - deadlines: items to add to Google Calendar
    - tasks: items to add to Google Tasks
    """
    entries = raw.get("sv_streamEntries", [])
    deadlines = []
    tasks = []
    seen_ids = set()

    for entry in entries:
        se_id = entry.get("se_id")
        if se_id in seen_ids:
            continue
        seen_ids.add(se_id)

        provider = entry.get("providerId", "")
        timestamp = _ms_to_dt(entry.get("se_timestamp", 0))
        title = _extract_title(entry)
        course_id = _extract_course_id(entry)
        event_type = _extract_event_type(entry)
        rhs = entry.get("se_rhs", "")
        url = f"https://clickup.up.ac.za{rhs}" if rhs else None

        isd = entry.get("itemSpecificData", {}) or {}

        # ── Announcements ─────────────────────────────────────────────────
        if provider in ANNOUNCEMENT_PROVIDERS:
            nd = isd.get("notificationDetails") or {}
            ann_title = nd.get("announcementTitle", title)
            body = nd.get("announcementBody", {}).get("rawText", "")
            tasks.append({
                "type": "announcement",
                "title": f"📢 {ann_title}",
                "notes": body[:500] if body else "New announcement posted.",
                "course_id": course_id,
                "se_id": se_id,
                "url": url,
                "due": None,
            })
            continue

        # ── Grade posted ──────────────────────────────────────────────────
        if provider == "bb_mygrades":
            gd = isd.get("gradeDetails") or {}
            grade = gd.get("grade")
            possible = gd.get("pointsPossible")
            if grade and possible:
                tasks.append({
                    "type": "grade",
                    "title": f"✅ Grade posted: {title} ({grade}/{possible})",
                    "notes": f"Grade received for {title}.",
                    "course_id": course_id,
                    "se_id": se_id,
                    "url": url,
                    "due": None,
                })
            continue

        # ── bb-nautilus: assignments, assessments, due dates ─────────────
        if provider == "bb-nautilus" and event_type:
            nd = isd.get("notificationDetails") or {}
            due_ms = nd.get("dueDate") or nd.get("availableDate")
            due_dt = _ms_to_dt(due_ms) if due_ms else timestamp

            if event_type in CALENDAR_EVENT_TYPES:
                label = CALENDAR_EVENT_TYPES[event_type]
                deadlines.append({
                    "title": f"{title}",
                    "label": label,
                    "course_id": course_id,
                    "se_id": se_id,
                    "due": due_dt,
                    "url": url,
                    "event_type": event_type,
                })

            if event_type in TASK_EVENT_TYPES:
                label = TASK_EVENT_TYPES[event_type]
                tasks.append({
                    "type": "assignment",
                    "title": f"📚 {title}",
                    "notes": f"{label} — due {due_dt.strftime('%d %b %Y %H:%M') if due_dt else 'TBD'}. {url or ''}",
                    "course_id": course_id,
                    "se_id": se_id,
                    "due": due_dt,
                    "url": url,
                })

    log.info(f"Parsed {len(deadlines)} deadlines, {len(tasks)} tasks from {len(entries)} entries.")
    return deadlines, tasks
