"""
content_crawler.py
Crawls course content trees, reads inline documents AND downloads PDFs,
sends new content to Claude for extraction.
Maintains state in state.json to avoid reprocessing.
"""

import os
import json
import base64
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import Page
import anthropic

log = logging.getLogger(__name__)

BASE_URL = "https://clickup.up.ac.za"
STATE_FILE = Path("state.json")
DASHBOARD_FILE = Path("docs/dashboard.json")

COURSE_ROOTS = {
    "_190939_1": {"name": "MOW 217 S1 2026", "color": "#F5C518"},
    "_188765_1": {"name": "MSD 210 S1 2026", "color": "#3B6EC4"},
    "_191012_1": {"name": "WTW 256 S1 2026", "color": "#8B1A2B"},
    "_190953_1": {"name": "WTW 258 S1 2026", "color": "#F4736A"},
    "_189590_1": {"name": "MJJ 210 S1 2026", "color": "#9EA8B3"},
    "_189473_1": {"name": "MPR 213 S1 2026", "color": "#2952A3"},
}

READABLE_MIME_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
}

# Inline document handlers (content in body.rawText)
INLINE_DOC_HANDLERS = {
    "resource/x-bb-document",
    "resource/x-bb-lesson",
    "resource/x-bb-blti-link",
}

SKIP_TITLE_KEYWORDS = [
    "memo", "solution", "answer", "mark scheme",
    "old test", "old exam", "previous year", "past paper",
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
        f"?@view=Summary&expand=gradebookCategory&includeInActivityTracking=true&limit=200"
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
        f"?@view=Summary&includeInActivityTracking=true&limit=200"
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


ANALYSE_PROMPT = """You are analysing a university course document for a Mechanical Engineering student at UP.
Course: {course_name}
File: {filename}

Extract ONLY what is actionable or important. Respond in JSON only, no markdown:

{{
  "summary": "1-2 sentence summary",
  "type": "one of: assignment_brief|test_scope|tutorial_info|lecture_content|guidelines|reference|schedule|other",
  "deadlines": [
    {{"description": "...", "date": "YYYY-MM-DD or null"}}
  ],
  "key_requirements": ["important requirements or tasks"],
  "important_notes": ["critical things not to forget"],
  "is_actionable": true
}}

Set is_actionable to false for pure reference material (formula sheets, textbook chapters).
For tutorial schedule documents, set type to "tutorial_info" and extract test/quiz dates."""


def analyse_text_with_claude(text: str, filename: str, course_name: str) -> dict:
    """Analyse plain text content with Claude."""
    if len(text.strip()) < 30:
        return {"summary": filename, "type": "other", "is_actionable": False}
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": ANALYSE_PROMPT.format(
                    course_name=course_name, filename=filename
                ) + f"\n\nDocument content:\n{text[:4000]}"
            }]
        )
        raw = re.sub(r"```json|```", "", response.content[0].text.strip()).strip()
        return json.loads(raw)
    except Exception as e:
        log.warning(f"Claude text analysis failed for {filename}: {e}")
        return {"summary": filename, "type": "other", "is_actionable": False}


def analyse_pdf_with_claude(pdf_bytes: bytes, filename: str, course_name: str) -> dict:
    """Analyse PDF content with Claude."""
    try:
        b64 = base64.standard_b64encode(pdf_bytes).decode()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf", "data": b64}
                    },
                    {
                        "type": "text",
                        "text": ANALYSE_PROMPT.format(course_name=course_name, filename=filename)
                    }
                ]
            }]
        )
        raw = re.sub(r"```json|```", "", response.content[0].text.strip()).strip()
        return json.loads(raw)
    except Exception as e:
        log.warning(f"Claude PDF analysis failed for {filename}: {e}")
        return {"summary": filename, "type": "other", "is_actionable": False}


def crawl_course(
    page: Page,
    course_id: str,
    course_name: str,
    state: dict,
    max_items: int = 60,
) -> list[dict]:
    seen = set(state.get("seen_content_ids", []))
    insights = []
    items_processed = 0
    queue = []

    root_items = fetch_root_contents(page, course_id)
    for item in root_items:
        queue.append((item, 0, ""))

    while queue and items_processed < max_items:
        item, depth, parent_title = queue.pop(0)
        content_id = item.get("id")
        handler = item.get("contentHandler", "")
        title = item.get("title", "untitled")
        visibility = item.get("visibility", "VISIBLE")

        if content_id in seen:
            continue
        if visibility != "VISIBLE":
            continue
        if any(kw in title.lower() for kw in SKIP_TITLE_KEYWORDS):
            seen.add(content_id)
            continue

        # Folder — recurse
        if "folder" in handler or "lessonplan" in handler:
            children = fetch_children(page, course_id, content_id)
            for child in children:
                queue.append((child, depth + 1, title))
            seen.add(content_id)
            continue

        # Inline document (BB page, lesson) — read body.rawText
        if handler in INLINE_DOC_HANDLERS:
            body = item.get("body", {})
            raw_text = body.get("rawText", "").strip()
            # Also grab contentExtract if body is empty
            if not raw_text:
                raw_text = item.get("contentExtract", "").strip()

            if raw_text and len(raw_text) > 40:
                log.info(f"  Inline doc: {title}")
                insight = analyse_text_with_claude(raw_text, title, course_name)
                if insight.get("is_actionable"):
                    insight["filename"] = title
                    insight["course_id"] = course_id
                    insight["content_id"] = content_id
                    insight["parent_folder"] = parent_title
                    insights.append(insight)
                    log.info(f"    → {insight.get('type')}: {insight.get('summary','')[:70]}")
                items_processed += 1

            seen.add(content_id)
            continue

        # File — download and read
        content_detail = item.get("contentDetail", {})
        file_info = content_detail.get("resource/x-bb-file", {}).get("file", {})
        mime = file_info.get("mimeType", "")
        permanent_url = file_info.get("permanentUrl", "")

        if mime in READABLE_MIME_TYPES and permanent_url:
            log.info(f"  File: {title} ({mime})")
            pdf_bytes = download_pdf(page, permanent_url)
            if pdf_bytes and 100 < len(pdf_bytes) < 8_000_000:
                insight = analyse_pdf_with_claude(pdf_bytes, title, course_name)
                if insight.get("is_actionable"):
                    insight["filename"] = title
                    insight["course_id"] = course_id
                    insight["content_id"] = content_id
                    insight["parent_folder"] = parent_title
                    insights.append(insight)
                    log.info(f"    → {insight.get('type')}: {insight.get('summary','')[:70]}")
                items_processed += 1

        seen.add(content_id)

    state["seen_content_ids"] = list(seen)
    return insights


def crawl_all_courses(page: Page, state: dict) -> dict:
    all_insights = {}
    for course_id, info in COURSE_ROOTS.items():
        course_name = info["name"]
        log.info(f"Crawling content: {course_name}...")
        insights = crawl_course(page, course_id, course_name, state)
        if insights:
            all_insights[course_id] = insights
            log.info(f"  → {len(insights)} actionable items found")
        else:
            log.info(f"  → Nothing new")
    return all_insights
