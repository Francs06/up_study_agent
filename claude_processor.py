"""
claude_processor.py
Uses Claude to extract actionable calendar events from announcement text.
Returns a list of calendar-ready event dicts, or empty list if nothing actionable.
"""

import os
import json
import logging
import re
from datetime import datetime, timezone

import anthropic

log = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = """You are an assistant that extracts actionable academic events from university announcements.

Given an announcement, extract any events that a student should add to their calendar — things like:
- Test/exam dates and times
- Assignment submission deadlines
- Consultation sessions
- Compulsory meetings or events

For each event found, return a JSON object. If there are no actionable calendar events, return an empty array.

Today's date is provided in the user message. Use it to interpret relative dates like "Monday" or "next week".

Respond ONLY with a JSON array. No explanation, no markdown, no backticks. Examples:

[
  {
    "title": "MOW 217: Semester Test 2",
    "date": "2026-04-28",
    "time": "09:00",
    "duration_hours": 2,
    "description": "Scope: Power transmissions, Theme 5 (Equilibrium, Bearings, Gears), CAD drawings. Submit CAD as PDF."
  }
]

Or if nothing actionable:
[]

Rules:
- Only include events with a specific date (not vague "soon" or "this week")
- duration_hours is optional, omit if unknown
- time is 24h format, omit if unknown
- date is YYYY-MM-DD format
- Keep description under 200 chars, focused on what the student needs to know/do
- Ignore purely informational announcements with no dates (research tools, SRC notices, etc.)
"""


def process_announcement(title: str, body: str, course_name: str, today: str) -> list[dict]:
    """
    Send an announcement to Claude and get back a list of calendar events.
    Returns [] if nothing actionable or if the API call fails.
    """
    if not body or len(body.strip()) < 50:
        return []

    user_message = f"""Today's date: {today}
Course: {course_name}
Announcement title: {title}

Announcement body:
{body[:3000]}"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",  # Fast and cheap for this task
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text.strip()

        # Strip any accidental markdown fences
        raw = re.sub(r"```json|```", "", raw).strip()

        events = json.loads(raw)
        if not isinstance(events, list):
            return []

        log.info(f"Claude found {len(events)} events in: {title}")
        return events

    except json.JSONDecodeError as e:
        log.warning(f"Claude returned invalid JSON for '{title}': {e}")
        return []
    except Exception as e:
        log.warning(f"Claude API error for '{title}': {e}")
        return []


def announcement_events_to_calendar(events: list[dict], course_name: str, se_id: str) -> list[dict]:
    """Convert Claude's extracted events into our calendar format."""
    calendar_items = []

    for i, ev in enumerate(events):
        date_str = ev.get("date")
        if not date_str:
            continue

        time_str = ev.get("time", "08:00")
        duration = ev.get("duration_hours", 1)

        try:
            dt = datetime.fromisoformat(f"{date_str}T{time_str}:00+02:00")
        except ValueError:
            log.warning(f"Bad date format from Claude: {date_str} {time_str}")
            continue

        # Skip if in the past
        if dt < datetime.now(tz=timezone.utc):
            continue

        calendar_items.append({
            "title": ev.get("title", f"{course_name}: Event"),
            "label": "ANNOUNCEMENT",
            "course_id": course_name,
            "se_id": f"{se_id}_claude_{i}",
            "due": dt,
            "url": None,
            "event_type": "ANNOUNCEMENT",
            "description": ev.get("description", ""),
            "duration_hours": duration,
        })

    return calendar_items
