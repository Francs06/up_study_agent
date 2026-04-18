"""
calendar/google_calendar.py
Syncs deadline items to Google Calendar.
Uses the Google Calendar API v3 via a service account or OAuth token stored in secrets.
"""

import json
import logging
import os
from datetime import timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

# We tag every event we create so we can detect duplicates on future runs
AGENT_TAG = "up-study-agent"


def _get_service():
    """Build the Google Calendar API service from the service account JSON secret."""
    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)


def _event_exists(service, se_id: str) -> bool:
    """Check if we already created an event for this stream entry."""
    result = service.events().list(
        calendarId=CALENDAR_ID,
        privateExtendedProperty=f"se_id={se_id}",
    ).execute()
    return bool(result.get("items"))


def sync_to_calendar(deadlines: list[dict]) -> dict:
    """
    Create Google Calendar events for each deadline.
    Skips items already synced (idempotent).
    Returns a summary dict.
    """
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
            # If no due date, create as an all-day event for today
            from datetime import date
            today = date.today().isoformat()
            start = {"date": today}
            end = {"date": today}

        event_body = {
            "summary": f"📚 {item['title']}",
            "description": (
                f"Type: {item['label']}\n"
                f"Course: {item['course_id']}\n"
                f"Link: {item.get('url', 'N/A')}\n\n"
                f"Synced by UP Study Agent."
            ),
            "start": start,
            "end": end,
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 24 * 60},  # 1 day before
                    {"method": "popup", "minutes": 2 * 60},   # 2 hours before
                ],
            },
            "extendedProperties": {
                "private": {
                    "se_id": se_id,
                    "source": AGENT_TAG,
                }
            },
            "colorId": "11",  # Tomato red for deadlines
        }

        try:
            service.events().insert(calendarId=CALENDAR_ID, body=event_body).execute()
            log.info(f"Created calendar event: {item['title']}")
            created += 1
        except HttpError as e:
            log.error(f"Failed to create event '{item['title']}': {e}")

    return {"created": created, "skipped": skipped}
