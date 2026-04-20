"""
dashboard_builder.py
Builds dashboard.json from all collected data,
then generates a Claude summary for "focus today".
"""

import os
import json
import logging
from datetime import datetime, timezone
import anthropic

log = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

COURSE_META = {
    "_190939_1": {"name": "MOW 217", "full": "MOW 217 S1 2026 — Manufacturing & Design", "color": "#F5C518"},
    "_188765_1": {"name": "MSD 210", "full": "MSD 210 S1 2026 — Dynamics", "color": "#3B6EC4"},
    "_191012_1": {"name": "WTW 256", "full": "WTW 256 S1 2026 — Differential Equations", "color": "#8B1A2B"},
    "_190953_1": {"name": "WTW 258", "full": "WTW 258 S1 2026 — Calculus", "color": "#F4736A"},
    "_189590_1": {"name": "MJJ 210", "full": "MJJ 210 S1 2026 — Professional Communication", "color": "#9EA8B3"},
    "_189473_1": {"name": "MPR 213", "full": "MPR 213 S1 2026 — Programming & IT", "color": "#2952A3"},
}


def generate_focus_today(deadlines: list, insights: dict) -> str:
    """Ask Claude to generate a concise daily focus summary."""
    now = datetime.now(tz=timezone.utc)

    deadline_text = "\n".join([
        f"- {d['title']} (due {d['due_date']})"
        for d in sorted(deadlines, key=lambda x: x.get("due_date", "9999"))[:10]
        if d.get("due_date", "9999") >= now.strftime("%Y-%m-%d")
    ])

    insight_text = ""
    for course_id, items in insights.items():
        for item in items:
            if item.get("is_actionable") and item.get("key_requirements"):
                course = COURSE_META.get(course_id, {}).get("name", course_id)
                insight_text += f"\n{course} — {item['filename']}: {', '.join(item['key_requirements'][:2])}"

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": f"""You are a study assistant for Franco, a 2nd year Mechanical Engineering student at UP.

Today is {now.strftime('%A, %d %B %Y')}.

Upcoming deadlines:
{deadline_text or 'None found'}

Recent document insights:
{insight_text or 'None'}

Write a short, direct "focus for today" message (2-3 sentences max). Be specific about what matters most right now. No fluff, no generic advice. Speak directly to Franco."""
            }]
        )
        return response.content[0].text.strip()
    except Exception as e:
        log.warning(f"Focus generation failed: {e}")
        return "Check your upcoming deadlines and stay on top of reading assignments."


def build_dashboard(
    deadlines: list,
    content_insights: dict,
    state: dict,
) -> dict:
    now = datetime.now(tz=timezone.utc)

    # Generate focus summary
    log.info("Generating daily focus with Claude...")
    focus = generate_focus_today(deadlines, content_insights)

    # Group deadlines by course
    by_course = {}
    for d in deadlines:
        cid = d.get("course_id", "unknown")
        if cid not in by_course:
            by_course[cid] = []
        by_course[cid].append(d)

    # Build course summaries
    courses = {}
    for course_id, meta in COURSE_META.items():
        course_deadlines = by_course.get(course_id, [])
        course_insights = content_insights.get(course_id, [])

        actionable = [i for i in course_insights if i.get("is_actionable")]

        courses[course_id] = {
            "name": meta["name"],
            "full_name": meta["full"],
            "color": meta["color"],
            "deadlines": sorted(
                [d for d in course_deadlines if d.get("due_date")],
                key=lambda x: x["due_date"]
            )[:8],
            "new_documents": [
                {
                    "filename": i["filename"],
                    "type": i.get("type", "other"),
                    "summary": i.get("summary", ""),
                    "key_requirements": i.get("key_requirements", []),
                    "important_notes": i.get("important_notes", []),
                }
                for i in actionable
            ],
        }

    dashboard = {
        "generated_at": now.isoformat(),
        "generated_at_human": now.strftime("%d %B %Y at %H:%M SAST"),
        "focus_today": focus,
        "total_upcoming_deadlines": len([d for d in deadlines if d.get("due_date", "9999") >= now.strftime("%Y-%m-%d")]),
        "courses": courses,
        "all_deadlines": sorted(
            [d for d in deadlines if d.get("due_date", "9999") >= now.strftime("%Y-%m-%d")],
            key=lambda x: x.get("due_date", "9999")
        ),
    }

    return dashboard
