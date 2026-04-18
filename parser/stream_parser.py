"""
parser/stream_parser.py
Parses the Blackboard Ultra activity stream.
Only extracts FUTURE-FACING actionable items — deadlines and new assignments.
Grades, old notifications, and historical entries are ignored.
"""

import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

# Only these event types create calendar events
CALENDAR_EVENT_TYPES = {
    "AS:DUE":      "Assignment Due",
    "PE:DUE":      "Peer Review Due",
    "SU:OVERDUE":  "Survey Overdue",
    "AS:OVERDUE":  "Assignment Overdue",
    "UA:OVERDUE":  "Item Overdue",
}

# Only these event types create tasks
TASK_EVENT_TYPES = {
    "AS:DUE":                "Assignment",
    "PE:DUE":                "Peer Review",
    "SU:SU_AVAIL":           "Survey Available",
    "PS:PS_AVAIL":           "Assessment Available",
    "AS:AS_GA_AVAIL_RESEND": "Group Assignment",
    "PE:PE_AVAIL_RESEND":    "Peer Review",
}

# Announcements only — no grades, no bb_mygrades, no bb_tel
ANNOUNCEMENT_PROVIDERS = {"bb-announcement"}

# How far back we look — ignore anything older than this
LOOKBACK_DAYS = 7


def _ms_to_dt(ms) -> datetime | None:
    if not ms or ms <= 0 or ms > 9007199254740990:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _extract_title(entry: dict) -> str:
    isd = entry.get("itemSpecificData", {}) or {}
    return (
        isd.get("title")
        or (isd.get("notificationDetails") or {}).get("announcementTitle")
        or "Untitled Item"
    )


def parse_stream(raw: dict) -> tuple[list[dict], list[dict]]:
    entries = raw.get("sv_streamEntries", [])
    deadlines = []
    tasks = []
    seen_ids = set()

    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(days=LOOKBACK_DAYS)

    for entry in entries:
        se_id = entry.get("se_id")
        if se_id in seen_ids:
            continue
        seen_ids.add(se_id)

        provider = entry.get("providerId", "")
        timestamp = _ms_to_dt(entry.get("se_timestamp", 0))
        title = _extract_title(entry)
        course_id = entry.get("se_courseId", "unknown")
        rhs = entry.get("se_rhs", "")
        url = f"https://clickup.up.ac.za{rhs}" if rhs else None
        isd = entry.get("itemSpecificData", {}) or {}
        nd = isd.get("notificationDetails") or {}
        event_type = nd.get("eventType") or isd.get("eventType")

        # ── Skip anything older than the lookback window ──────────────────
        if timestamp and timestamp < cutoff:
            continue

        # ── Announcements only (no grades, no discussion, no bb_tel) ─────
        if provider in ANNOUNCEMENT_PROVIDERS:
            body = nd.get("announcementBody", {}).get("rawText", "")
            tasks.append({
                "type": "announcement",
                "title": f"📢 {title}",
                "notes": (body[:400] if body else "New announcement.") + f"\n\n[se_id:{se_id}]",
                "course_id": course_id,
                "se_id": se_id,
                "url": url,
                "due": None,
            })
            continue

        # ── bb-nautilus: assignments, assessments, due dates only ─────────
        if provider == "bb-nautilus" and event_type:
            due_ms = nd.get("dueDate") or nd.get("availableDate")
            due_dt = _ms_to_dt(due_ms) if due_ms else None

            # Skip if due date is in the past (more than 1 day ago)
            if due_dt and due_dt < (now - timedelta(days=1)):
                continue

            if event_type in CALENDAR_EVENT_TYPES:
                label = CALENDAR_EVENT_TYPES[event_type]
                deadlines.append({
                    "title": title,
                    "label": label,
                    "course_id": course_id,
                    "se_id": se_id,
                    "due": due_dt,
                    "url": url,
                    "event_type": event_type,
                })

            if event_type in TASK_EVENT_TYPES:
                label = TASK_EVENT_TYPES[event_type]
                due_str = due_dt.strftime("%d %b %Y %H:%M") if due_dt else "TBD"
                tasks.append({
                    "type": "assignment",
                    "title": f"📚 {title}",
                    "notes": f"{label} — due {due_str}.\n{url or ''}\n\n[se_id:{se_id}]",
                    "course_id": course_id,
                    "se_id": se_id,
                    "due": due_dt,
                    "url": url,
                })

        # All other providers (bb_mygrades, bb_tel, bb_disc, etc.) are ignored

    log.info(f"Parsed {len(deadlines)} deadlines, {len(tasks)} tasks from {len(entries)} entries.")
    return deadlines, tasks
