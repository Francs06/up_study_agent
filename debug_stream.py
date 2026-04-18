import os, json, logging
from auth.blackboard_login import get_stream_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

raw = get_stream_data(os.environ["UP_USERNAME"], os.environ["UP_PASSWORD"])
entries = raw.get("sv_streamEntries", [])

output = []
count = 0
for entry in entries:
    if entry.get("providerId") != "bb-nautilus":
        continue
    output.append(json.dumps(entry, indent=2, default=str))
    count += 1
    if count >= 5:
        break

result = "\n---\n".join(output)

# Write to file so we can download it as an artifact
with open("debug_output.txt", "w") as f:
    f.write(f"Total entries: {len(entries)}\n")
    f.write(f"bb-nautilus count: {count}\n\n")
    f.write(result)

print(f"Written {count} entries to debug_output.txt")
