"""
config.py
---------
Small, central place for settings that might change or that you'd
want to reuse in a future agent. Keeping this separate from the
logic files means updating your Team ID (or reusing this pattern
for a different FPL team) doesn't mean hunting through multiple
files.
"""

TEAM_ID = 6738704  # "Real Bugsy United"

# Your own manual watchlist pick, one per position. Edit these names
# to change your picks - matched against the live player data by
# name each week, so no need to look up internal player IDs. If a
# name doesn't match (e.g. a typo, or the player's left the league),
# the dashboard will show that slot as "not found" rather than
# silently guessing.
MY_WATCHLIST = {
    "GK": None,
    "DEF": None,
    "MID": None,
    "FWD": None,
}

# The dashboard's "Discuss with Claude" button copies a fresh weekly
# briefing to your clipboard and opens this URL in a new tab, so you
# can paste it in and start talking. Leave blank to open a brand new
# claude.ai chat every time. Once you've started a conversation you
# want to keep using all season (so each week's briefing lands in the
# same ongoing thread instead of a fresh one), paste that
# conversation's URL here instead - e.g.
# CLAUDE_CHAT_URL = "https://claude.ai/chat/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
CLAUDE_CHAT_URL = ""
