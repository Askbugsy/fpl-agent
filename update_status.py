"""
update_status.py
-----------------
Writes the current UTC timestamp into README.md, replacing a marked
line. Run this right after main.py in the GitHub Actions workflow,
so anyone opening the repo can see at a glance when data was last
pulled - no digging into the Actions tab required.

Expects README.md to contain a line starting with "**Last data
pull:**" - if it's missing, this adds one to the top of the file
instead of failing silently.
"""

import re
from datetime import datetime, timezone
from pathlib import Path

README_PATH = Path(__file__).parent / "README.md"
MARKER_PATTERN = re.compile(r"\*\*Last data pull:\*\*.*")


def update_status():
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    new_line = f"**Last data pull:** {timestamp}"

    text = README_PATH.read_text() if README_PATH.exists() else "# fpl-agent\n\n"

    if MARKER_PATTERN.search(text):
        text = MARKER_PATTERN.sub(new_line, text, count=1)
    else:
        # No marker found yet - add one near the top, after the first heading
        lines = text.splitlines()
        insert_at = 1 if lines else 0
        lines.insert(insert_at, f"\n{new_line}\n")
        text = "\n".join(lines)

    README_PATH.write_text(text)
    print(f"Updated README.md -> {new_line}")


if __name__ == "__main__":
    update_status()
