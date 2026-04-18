"""
tasks/google_tasks.py
Syncs actionable items (assignments, announcements, grades) to Google Tasks.
Uses the Google Tasks API v1.
"""

import json
import logging
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/tasks"]
TASK_LIST_TITLE = "UP Study Agent"


def _get_service():
    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("tasks", "v1", credentials=creds)


def _get_or_create_task_list(service) -> str:
    """Get the 'UP Study Agent' task list ID, creating it if it doesn't exist."""
    lists = service.tasklists().list().execute()
    for tl in lists.get("items", []):
        if tl["title"] == TASK_LIST_TITLE:
            return tl["id"]

    # Create it
    new_list = service.tasklists().insert(body={"title": TASK_LIST_TITLE}).execute()
    log.info(f"Created task list: {TASK_LIST_TITLE}")
    return new_list["id"]


def _task_exists(service, tasklist_id: str, se_id: str) -> bool:
    """
    Check if we already created a task for this stream entry.
    We embed the se_id in the task notes as a fingerprint.
    """
    tasks = service.tasks().list(tasklist=tasklist_id, showCompleted=False).execute()
    for task in tasks.get("items", []):
        notes = task.get("notes", "")
        if f"[se_id:{se_id}]" in notes:
            return True
    return False


def sync_to_tasks(items: list[dict]) -> dict:
    """
    Create Google Tasks entries for each actionable item.
    Skips duplicates. Returns a summary dict.
    """
    service = _get_service()
    tasklist_id = _get_or_create_task_list(service)
    created = 0
    skipped = 0

    for item in items:
        se_id = item["se_id"]

        if _task_exists(service, tasklist_id, se_id):
            log.debug(f"Skipping existing task: {item['title']}")
            skipped += 1
            continue

        due = item.get("due")
        task_body = {
            "title": item["title"],
            "notes": f"{item.get('notes', '')}\n\n[se_id:{se_id}]",
        }

        if due:
            # Google Tasks expects RFC 3339 format, date portion only for due
            task_body["due"] = due.strftime("%Y-%m-%dT00:00:00.000Z")

        try:
            service.tasks().insert(tasklist=tasklist_id, body=task_body).execute()
            log.info(f"Created task: {item['title']}")
            created += 1
        except HttpError as e:
            log.error(f"Failed to create task '{item['title']}': {e}")

    return {"created": created, "skipped": skipped}
