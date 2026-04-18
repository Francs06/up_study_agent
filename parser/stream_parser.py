"""
parser/stream_parser.py
Parses the Blackboard Ultra activity stream.
Event type comes from extraAttribs.event_type.
Due date comes from notificationDetails.dueDate.
"""

import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

# event_type prefixes that indicate a deadline/due item
DUE_EVENT_TYPES = {
    "AS:DUE", "UA:DUE", "PE:DUE", "SU:DUE", "PS:DUE",
    "TE:DUE", "GB:DUE", "SC:DUE",
}

# event_type prefixes that indicate something newly available with a due date
AVAIL_EVENT_TYPES = {
    "UA:UA_AVAIL", "AS:AS_AVAIL", "TE:TE_AVAIL", "PS:PS_AVAIL",
    "PE:PE_AVAIL", "SU:SU_AVAIL", "SC:SC_AVAIL",
}

# Announcement event types
ANNOUNCEMENT_EVENT_TYPES = {"AN:AN_AVAIL"}

# Source types that indicate a gradeable item with a due date
GRADEABLE_SOURCE_TYPES = {"UA", "AS", "PE", "PS", "TE", "SC", "SU"}

LOOKBACK_DAYS = 14  # wider window to catch things posted a while ago but still future


def _ms_to_dt(ms) -> datetime | None:
    if not ms or ms <= 0 or ms > 9007199254740990:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _iso_to_dt(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def parse_stream(raw: dict) -> tuple[list[dict], list[dict]]:
    entries = raw.get("sv_streamEntries", [])

    # Build course name lookup from sv_extras
    course_names = {}
    for c in raw.get("sv_extras", {}).get("sx_courses", []):
        course_names[c["id"]] = c.get("name", c["id"])

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
        isd = entry.get("itemSpecificData", {}) or {}
        nd = isd.get("notificationDetails") or {}
        event_type = (entry.get("extraAttribs") or {}).get("event_type", "")
        title = isd.get("title") or nd.get("announcementTitle") or "Untitled"
        course_id = entry.get("se_courseId", "")
        course_name = course_names.get(course_id, course_id)
        rhs = entry.get("se_rhs", "")
        url = f"https://clickup.up.ac.za{rhs}" if rhs else None

        due_dt = _iso_to_dt(nd.get("dueDate"))

        # ── Announcements ─────────────────────────────────────────────────
        if event_type in ANNOUNCEMENT_EVENT_TYPES or (
            provider == "bb-nautilus" and nd.get("sourceType") == "AN"
            and nd.get("announcementTitle")
        ):
            # Only include if recent (posted in last 14 days)
            if timestamp and timestamp < cutoff:
                continue
            body = (nd.get("announcementBody") or "")
            # Strip HTML tags roughly
            import re
            body_text = re.sub(r"<[^>]+>", " ", body).strip()[:400]
            tasks.append({
                "type": "announcement",
                "title": f"📢 {course_name}: {nd.get('announcementTitle', title)}",
                "notes": f"{body_text}\n\n[se_id:{se_id}]",
                "course_id": course_id,
                "se_id": se_id,
                "url": url,
                "due": None,
            })
            continue

        # ── Items with due dates ──────────────────────────────────────────
        if due_dt:
            # Skip if due date already passed more than 1 day ago
            if due_dt < (now - timedelta(days=1)):
                continue

            due_str = due_dt.strftime("%d %b %Y %H:%M SAST")

            # Calendar event for anything with a future due date
            if event_type in DUE_EVENT_TYPES or event_type in AVAIL_EVENT_TYPES:
                deadlines.append({
                    "title": f"{course_name}: {title}",
                    "label": event_type,
                    "course_id": course_id,
                    "se_id": se_id,
                    "due": due_dt,
                    "url": url,
                    "event_type": event_type,
                })
                tasks.append({
                    "type": "assignment",
                    "title": f"📚 {course_name}: {title}",
                    "notes": f"Due: {due_str}\n{url or ''}\n\n[se_id:{se_id}]",
                    "course_id": course_id,
                    "se_id": se_id,
                    "due": due_dt,
                    "url": url,
                })

    log.info(f"Parsed {len(deadlines)} deadlines, {len(tasks)} tasks from {len(entries)} entries.")
    return deadlines, tasks
