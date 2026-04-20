"""
content_crawler.py
Crawls course content trees, reads inline documents AND downloads PDFs.
Handles BB pages (isBbPage folders), ultraDocumentBody blocks,
and standard file attachments.
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

INLINE_DOC_HANDLERS = {
    "resource/x-bb-document",
    "resource/x-bb-lesson",
}

SKIP_TITLE_KEYWORDS = [
    "memo", "solution", "answer", "mark scheme",
    "old test", "old exam", "previous year", "past paper",
]

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"seen_content_ids": [], "course_insights": {}, "last_run": None}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)


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


ANALYSE_PROMPT = """You are analysing a university course document for a 2nd year Mechanical Engineering student at UP.
Course: {course_name}
Document title: {filename}

Extract ONLY what is actionable or important for the student. Respond in JSON only, no markdown fences:

{{
  "summary": "1-2 sentence summary of what this document contains",
  "type": "one of: assignment_brief|test_scope|tutorial_info|lecture_content|guidelines|schedule|reference|other",
  "deadlines": [
    {{"description": "what is due or happening", "date": "YYYY-MM-DD or null if no specific date"}}
  ],
  "key_requirements": ["list of important tasks or requirements"],
  "important_notes": ["critical things not to forget"],
  "is_actionable": true
}}

Rules:
- For weekly schedule documents (e.g. "Week 20-24 April"), extract tutorial tests, quizzes, lecture topics
- If there is a tutorial test mentioned, include it as a deadline with the week's date range
- Set is_actionable to false ONLY for pure reference material like formula sheets or textbook chapters
- Keep summaries concise and specific"""


def analyse_with_claude(text: str, filename: str, course_name: str) -> dict:
    if len(text.strip()) < 20:
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
        log.warning(f"Claude analysis failed for {filename}: {e}")
        return {"summary": filename, "type": "other", "is_actionable": False}


def analyse_pdf_with_claude(pdf_bytes: bytes, filename: str, course_name: str) -> dict:
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


def collect_bb_page_text(page: Page, course_id: str, folder_id: str) -> str:
    """
    For isBbPage folders, collect all child text blocks into one string.
    Children are often titled 'ultraDocumentBody' with text in body.rawText.
    """
    children = fetch_children(page, course_id, folder_id)
    texts = []
    for child in children:
        body = child.get("body", {}) or {}
        raw = body.get("rawText", "").strip()
        if raw:
            texts.append(raw)
        # Also check displayText as fallback
        display = body.get("displayText", "").strip()
        if display and display != raw:
            texts.append(display)
        # Check title if it's not the generic ultraDocumentBody
        title = child.get("title", "")
        if title and title != "ultraDocumentBody" and title not in texts:
            texts.insert(0, title)
    return "\n\n".join(texts)


def crawl_course(
    page: Page,
    course_id: str,
    course_name: str,
    state: dict,
    max_items: int = 80,
) -> list[dict]:
    seen = set(state.get("seen_content_ids", []))
    insights = []
    items_processed = 0

    # Use a stack: (item, parent_title, is_bb_page_child)
    stack = []
    root_items = fetch_root_contents(page, course_id)
    for item in reversed(root_items):
        stack.append((item, "", False))

    while stack and items_processed < max_items:
        item, parent_title, is_bb_page_child = stack.pop()
        content_id = item.get("id")
        handler = item.get("contentHandler", "")
        title = item.get("title", "untitled")
        visibility = item.get("visibility", "VISIBLE")

        if not content_id or content_id in seen:
            continue
        if visibility != "VISIBLE":
            seen.add(content_id)
            continue
        if any(kw in title.lower() for kw in SKIP_TITLE_KEYWORDS):
            seen.add(content_id)
            continue

        # ── BB Page (isBbPage folder) ──────────────────────────────────
        # These are "Week X-Y" type pages — collect all child text blocks
        is_bb_page = (
            "folder" in handler and
            item.get("contentDetail", {}).get("resource/x-bb-folder", {}).get("isBbPage", False)
        )

        if is_bb_page:
            log.info(f"  BB Page: {title}")
            combined_text = collect_bb_page_text(page, course_id, content_id)
            if combined_text.strip():
                insight = analyse_with_claude(combined_text, title, course_name)
                if insight.get("is_actionable"):
                    insight["filename"] = title
                    insight["course_id"] = course_id
                    insight["content_id"] = content_id
                    insight["parent_folder"] = parent_title
                    insights.append(insight)
                    log.info(f"    → {insight.get('type')}: {insight.get('summary','')[:80]}")
            # Mark all children as seen too so we don't reprocess them individually
            children = fetch_children(page, course_id, content_id)
            for child in children:
                cid = child.get("id")
                if cid:
                    seen.add(cid)
            seen.add(content_id)
            items_processed += 1
            continue

        # ── Regular folder — recurse ───────────────────────────────────
        if "folder" in handler or "lessonplan" in handler:
            children = fetch_children(page, course_id, content_id)
            for child in reversed(children):
                stack.append((child, title, False))
            seen.add(content_id)
            continue

        # ── Inline document (non-BB-page) ──────────────────────────────
        if handler in INLINE_DOC_HANDLERS:
            body = item.get("body", {}) or {}
            raw_text = body.get("rawText", "").strip()
            if not raw_text:
                raw_text = body.get("displayText", "").strip()

            if raw_text and len(raw_text) > 40:
                log.info(f"  Inline doc: {title}")
                insight = analyse_with_claude(raw_text, title, course_name)
                if insight.get("is_actionable"):
                    insight["filename"] = title
                    insight["course_id"] = course_id
                    insight["content_id"] = content_id
                    insight["parent_folder"] = parent_title
                    insights.append(insight)
                    log.info(f"    → {insight.get('type')}: {insight.get('summary','')[:80]}")
                items_processed += 1

            seen.add(content_id)
            continue

        # ── Skip ultraDocumentBody children (handled by BB page collector) ─
        if title == "ultraDocumentBody":
            seen.add(content_id)
            continue

        # ── File (PDF etc.) ────────────────────────────────────────────
        content_detail = item.get("contentDetail", {})
        file_info = content_detail.get("resource/x-bb-file", {}).get("file", {})
        mime = file_info.get("mimeType", "")
        permanent_url = file_info.get("permanentUrl", "")

        if mime in READABLE_MIME_TYPES and permanent_url:
            log.info(f"  File: {title}")
            pdf_bytes = download_pdf(page, permanent_url)
            if pdf_bytes and 100 < len(pdf_bytes) < 8_000_000:
                insight = analyse_pdf_with_claude(pdf_bytes, title, course_name)
                if insight.get("is_actionable"):
                    insight["filename"] = title
                    insight["course_id"] = course_id
                    insight["content_id"] = content_id
                    insight["parent_folder"] = parent_title
                    insights.append(insight)
                    log.info(f"    → {insight.get('type')}: {insight.get('summary','')[:80]}")
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
            log.info(f"  → {len(insights)} actionable items")
        else:
            log.info(f"  → Nothing new")
    return all_insights
