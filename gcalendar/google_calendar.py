"""
gcalendar/google_calendar.py
Syncs deadline items to Google Calendar.
"""

import json
import logging
import os
from datetime import timedelta, datetime, date

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
AGENT_TAG = "up-study-agent"

# Human-readable labels for event types
EVENT_TYPE_LABELS = {
    "AS:DUE": "Assignment Due",
    "UA:DUE": "Assignment Due",
    "UA:UA_AVAIL": "Assignment Available",
    "PE:DUE": "Peer Review Due",
    "PE:PE_AVAIL": "Peer Review Available",
    "SU:DUE": "Survey Due",
    "PS:DUE": "Assessment Due",
    "PS:PS_AVAIL": "Assessment Available",
    "TE:DUE": "Test Due",
    "TE:TE_AVAIL": "Test Available",
    "GB:DUE": "Gradebook Item Due",
    "SC:DUE": "SCORM Item Due",
}


def _get_service():
    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)


def _event_exists(service, se_id: str) -> bool:
    result = service.events().list(
        calendarId=CALENDAR_ID,
        privateExtendedProperty=f"se_id={se_id}",
    ).execute()
    return bool(result.get("items"))


def sync_to_calendar(deadlines: list[dict]) -> dict:
    service = _get_service()
    created = 0
    skipped = 0

    for item in deadlines:
        se_id = item["se_id"]

        if _event_exists(service, se_id):
            log.debug(f"Skipping existing event: {item['title']}")
            skipped += 1
            continue

        due = item.get("due")
        if due:
            start = {"dateTime": due.isoformat(), "timeZone": "Africa/Johannesburg"}
            end_dt = due + timedelta(hours=1)
            end = {"dateTime": end_dt.isoformat(), "timeZone": "Africa/Johannesburg"}
        else:
            today = date.today().isoformat()
            start = {"date": today}
            end = {"date": today}

        # Human-readable type label
        event_type = item.get("event_type", "")
        type_label = EVENT_TYPE_LABELS.get(event_type, event_type)

        # Reminder: day before at 07:30 SAST
        # Calculate minutes from due time back to 07:30 the previous day
        if due:
            from datetime import timezone
            import pytz
            sast = pytz.timezone("Africa/Johannesburg")
            due_sast = due.astimezone(sast)
            prev_day_0730 = due_sast.replace(hour=7, minute=30, second=0, microsecond=0) - timedelta(days=1)
            minutes_before = int((due_sast - prev_day_0730).total_seconds() / 60)
        else:
            minutes_before = 24 * 60  # fallback: 24 hours

        event_body = {
            "summary": item["title"],
            "description": (
                f"{type_label}\n"
                f"Synced by UP Study Agent."
            ),
            "start": start,
            "end": end,
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": minutes_before},
                ],
            },
            "extendedProperties": {
                "private": {
                    "se_id": se_id,
                    "source": AGENT_TAG,
                }
            },
            "colorId": "11",  # Tomato red
        }

        try:
            service.events().insert(calendarId=CALENDAR_ID, body=event_body).execute()
            log.info(f"Created calendar event: {item['title']}")
            created += 1
        except HttpError as e:
            log.error(f"Failed to create event '{item['title']}': {e}")

    return {"created": created, "skipped": skipped}
