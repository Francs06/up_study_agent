import os, json, logging
from auth.blackboard_login import get_stream_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

raw = get_stream_data(os.environ["UP_USERNAME"], os.environ["UP_PASSWORD"])
entries = raw.get("sv_streamEntries", [])

print("\n=== FIRST 5 bb-nautilus ENTRIES (raw JSON) ===")
count = 0
for entry in entries:
    if entry.get("providerId") != "bb-nautilus":
        continue
    print(json.dumps(entry, indent=2, default=str))
    print("---")
    count += 1
    if count >= 5:
        break
