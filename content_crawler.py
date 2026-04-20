"""
content_crawler.py
Crawls course content trees, downloads new PDFs/documents,
and sends them to Claude for extraction.
Maintains state in state.json to avoid reprocessing.
"""

import os
import json
import base64
import logging
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import Page
import anthropic

log = logging.getLogger(__name__)

BASE_URL = "https://clickup.up.ac.za"
STATE_FILE = Path("state.json")
DASHBOARD_FILE = Path("docs/dashboard.json")

# Root content folder IDs per course (from network tab - top level)
COURSE_ROOTS = {
    "_190939_1": {"name": "MOW 217 S1 2026", "color": "#F5C518"},
    "_188765_1": {"name": "MSD 210 S1 2026", "color": "#3B6EC4"},
    "_191012_1": {"name": "WTW 256 S1 2026", "color": "#8B1A2B"},
    "_190953_1": {"name": "WTW 258 S1 2026", "color": "#F4736A"},
    "_189590_1": {"name": "MJJ 210 S1 2026", "color": "#9EA8B3"},
    "_189473_1": {"name": "MPR 213 S1 2026", "color": "#2952A3"},
}

# File types worth reading
READABLE_MIME_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/html",
}

# Folders/files to skip (noise)
SKIP_TITLE_KEYWORDS = [
    "memo", "solution", "answer", "mark scheme", "past paper",
    "old test", "old exam", "previous year",
]

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"seen_content_ids": [], "course_insights": {}, "last_run": None}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def load_dashboard() -> dict:
    if DASHBOARD_FILE.exists():
        with open(DASHBOARD_FILE) as f:
            return json.load(f)
    return {"generated_at": None, "courses": {}, "deadlines": [], "focus_today": ""}


def fetch_children(page: Page, course_id: str, content_id: str) -> list[dict]:
    url = (
        f"{BASE_URL}/learn/api/v1/courses/{course_id}/contents/{content_id}/children"
        f"?@view=Summary&expand=gradebookCategory&includeInActivityTracking=true&limit=100"
    )
    try:
        data = page.evaluate("""async (url) => {
            const r = await fetch(url, {credentials: 'include'});
            if (!r.ok) return {results: []};
            return await r.json();
        }""", url)
        return data.get("results", [])
    except Exception as e:
        log.warning(f"Failed to fetch children of {content_id}: {e}")
        return []


def fetch_root_contents(page: Page, course_id: str) -> list[dict]:
    url = (
        f"{BASE_URL}/learn/api/v1/courses/{course_id}/contents"
        f"?@view=Summary&includeInActivityTracking=true&limit=100"
    )
    try:
        data = page.evaluate("""async (url) => {
            const r = await fetch(url, {credentials: 'include'});
            if (!r.ok) return {results: []};
            return await r.json();
        }""", url)
        return data.get("results", [])
    except Exception as e:
        log.warning(f"Failed to fetch root contents for {course_id}: {e}")
        return []


def download_pdf(page: Page, permanent_url: str) -> bytes | None:
    full_url = f"{BASE_URL}{permanent_url}"
    try:
        result = page.evaluate("""async (url) => {
            const r = await fetch(url, {credentials: 'include'});
            if (!r.ok) return null;
            const buf = await r.arrayBuffer();
            const bytes = new Uint8Array(buf);
            let binary = '';
            for (let i = 0; i < bytes.byteLength; i++) {
                binary += String.fromCharCode(bytes[i]);
            }
            return btoa(binary);
        }""", full_url)
        if result:
            return base64.b64decode(result)
        return None
    except Exception as e:
        log.warning(f"Failed to download {permanent_url}: {e}")
        return None


def analyse_file_with_claude(
    filename: str,
    pdf_bytes: bytes,
    course_name: str,
) -> dict:
    """Send PDF to Claude and extract structured insights."""
    try:
        b64 = base64.standard_b64encode(pdf_bytes).decode()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64,
                        }
                    },
                    {
                        "type": "text",
                        "text": f"""You are analysing a university course document for a student.
Course: {course_name}
File: {filename}

Extract ONLY what is actionable or important for a student. Respond in JSON only, no markdown:

{{
  "summary": "1-2 sentence summary of what this document is",
  "type": "one of: assignment_brief|test_scope|lecture_notes|guidelines|reference|other",
  "deadlines": [
    {{"description": "...", "date": "YYYY-MM-DD or null if not found"}}
  ],
  "key_requirements": ["bullet point requirements if it's an assignment/project"],
  "important_notes": ["critical things a student must not forget"],
  "is_actionable": true
}}

If the document is just reference material with nothing actionable (like a formula sheet or drawing template), set is_actionable to false and keep other fields minimal."""
                    }
                ]
            }]
        )
        raw = response.content[0].text.strip()
        import re
        raw = re.sub(r"```json|```", "", raw).strip()
        return json.loads(raw)
    except Exception as e:
        log.warning(f"Claude analysis failed for {filename}: {e}")
        return {"summary": filename, "type": "other", "is_actionable": False}


def crawl_course(
    page: Page,
    course_id: str,
    course_name: str,
    state: dict,
    max_files: int = 10,
) -> list[dict]:
    """
    Recursively crawl course content, process new files.
    Returns list of insights from new files.
    """
    seen = set(state.get("seen_content_ids", []))
    insights = []
    files_processed = 0
    queue = []  # (content_id, depth, parent_title)

    # Start from root
    root_items = fetch_root_contents(page, course_id)
    for item in root_items:
        queue.append((item, 0, ""))

    while queue and files_processed < max_files:
        item, depth, parent_title = queue.pop(0)
        content_id = item.get("id")
        handler = item.get("contentHandler", "")
        title = item.get("title", "untitled")
        visibility = item.get("visibility", "VISIBLE")

        if content_id in seen:
            continue

        if visibility != "VISIBLE":
            continue

        # Skip noise folders/files
        if any(kw in title.lower() for kw in SKIP_TITLE_KEYWORDS):
            seen.add(content_id)
            continue

        # It's a folder — recurse
        if "folder" in handler:
            children = fetch_children(page, course_id, content_id)
            for child in children:
                queue.append((child, depth + 1, title))
            seen.add(content_id)
            continue

        # It's a file — check if PDF
        content_detail = item.get("contentDetail", {})
        file_info = content_detail.get("resource/x-bb-file", {}).get("file", {})
        mime = file_info.get("mimeType", "")
        permanent_url = file_info.get("permanentUrl", "")

        if mime in READABLE_MIME_TYPES and permanent_url:
            log.info(f"  New file: {title} ({mime})")
            pdf_bytes = download_pdf(page, permanent_url)
            if pdf_bytes and len(pdf_bytes) < 5_000_000:  # skip files > 5MB
                insight = analyse_file_with_claude(title, pdf_bytes, course_name)
                insight["filename"] = title
                insight["course_id"] = course_id
                insight["content_id"] = content_id
                insight["parent_folder"] = parent_title
                insights.append(insight)
                files_processed += 1
                log.info(f"    → {insight.get('type')}: {insight.get('summary', '')[:60]}")

        seen.add(content_id)

    # Update state
    state["seen_content_ids"] = list(seen)
    return insights


def crawl_all_courses(page: Page, state: dict) -> dict:
    """Crawl all courses and return insights per course."""
    all_insights = {}
    for course_id, info in COURSE_ROOTS.items():
        course_name = info["name"]
        log.info(f"Crawling content: {course_name}...")
        insights = crawl_course(page, course_id, course_name, state)
        if insights:
            all_insights[course_id] = insights
            log.info(f"  → {len(insights)} new files analysed")
        else:
            log.info(f"  → No new files")

    return all_insights
