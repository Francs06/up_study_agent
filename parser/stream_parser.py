"""
parser/stream_parser.py
Parses the Blackboard Ultra activity stream into calendar deadlines.
Deduplicates by (course_id, title, due_date) so the same assignment
appearing under multiple event types only creates one calendar entry.
"""

import re
import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

# Event types that carry a meaningful due date worth calendaring
CALENDAR_EVENT_TYPES = {
    "AS:DUE", "UA:DUE", "PE:DUE", "SU:DUE", "PS:DUE",
    "TE:DUE", "GB:DUE", "SC:DUE",
    "UA:UA_AVAIL", "AS:AS_AVAIL", "TE:TE_AVAIL", "PS:PS_AVAIL",
    "PE:PE_AVAIL", "SU:SU_AVAIL", "SC:SC_AVAIL",
}

# Announcement source types
ANNOUNCEMENT_SOURCE_TYPES = {"AN"}

LOOKBACK_DAYS = 14


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


def parse_stream(raw: dict) -> tuple[list[dict], list]:
    entries = raw.get("sv_streamEntries", [])

    # Build course name lookup from sv_extras
    course_names = {}
    for c in raw.get("sv_extras", {}).get("sx_courses", []):
        course_names[c["id"]] = c.get("name", c["id"])

    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(days=LOOKBACK_DAYS)

    seen_ids = set()
    # Dedup key: (course_id, normalized_title, due_date_str)
    seen_deadline_keys = set()
    deadlines = []

    for entry in entries:
        se_id = entry.get("se_id")
        if se_id in seen_ids:
            continue
        seen_ids.add(se_id)

        isd = entry.get("itemSpecificData", {}) or {}
        nd = isd.get("notificationDetails") or {}
        event_type = (entry.get("extraAttribs") or {}).get("event_type", "")
        title = isd.get("title") or nd.get("announcementTitle") or "Untitled"
        course_id = entry.get("se_courseId", "")
        course_name = course_names.get(course_id, course_id)
        rhs = entry.get("se_rhs", "")
        url = f"https://clickup.up.ac.za{rhs}" if rhs else None
        timestamp = _ms_to_dt(entry.get("se_timestamp", 0))
        due_dt = _iso_to_dt(nd.get("dueDate"))

        # ── Deadlines ─────────────────────────────────────────────────────
        if event_type in CALENDAR_EVENT_TYPES and due_dt:
            # Skip if already past (more than 1 day ago)
            if due_dt < (now - timedelta(days=1)):
                continue

            # Deduplicate: same assignment can appear as both DUE and AVAIL
            dedup_key = (course_id, title.strip().lower(), due_dt.date().isoformat())
            if dedup_key in seen_deadline_keys:
                log.debug(f"Dedup skipped: {title}")
                continue
            seen_deadline_keys.add(dedup_key)

            deadlines.append({
                "title": f"{course_name}: {title}",
                "label": event_type,
                "course_id": course_id,
                "se_id": se_id,
                "due": due_dt,
                "url": url,
                "event_type": event_type,
            })

    log.info(f"Parsed {len(deadlines)} deduplicated deadlines from {len(entries)} entries.")
    return deadlines, []
