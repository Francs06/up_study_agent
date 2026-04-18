"""
debug_stream.py - run once to inspect stream entry structure
Remove after debugging.
"""
import os, json, sys, logging
from collections import Counter
from auth.blackboard_login import get_stream_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

raw = get_stream_data(os.environ["UP_USERNAME"], os.environ["UP_PASSWORD"])
entries = raw.get("sv_streamEntries", [])

# Count providers
providers = Counter(e.get("providerId") for e in entries)
print("\n=== PROVIDERS ===")
for p, count in providers.most_common():
    print(f"  {p}: {count}")

# Print first 3 entries of each provider so we can see the structure
seen = set()
print("\n=== SAMPLE ENTRIES ===")
for entry in entries:
    p = entry.get("providerId")
    if p in seen:
        continue
    seen.add(p)
    isd = entry.get("itemSpecificData", {}) or {}
    print(f"\n--- Provider: {p} ---")
    print(f"  se_id: {entry.get('se_id')}")
    print(f"  se_timestamp: {entry.get('se_timestamp')}")
    print(f"  title: {isd.get('title')}")
    print(f"  eventType: {isd.get('eventType')}")
    nd = isd.get("notificationDetails") or {}
    print(f"  notificationDetails.eventType: {nd.get('eventType')}")
    print(f"  notificationDetails.dueDate: {nd.get('dueDate')}")
    cd = isd.get("calendarDetails") or {}
    print(f"  calendarDetails: {cd}")
    print(f"  se_rhs: {entry.get('se_rhs','')[:80]}")
