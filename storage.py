"""
storage.py
----------
Saves a timestamped snapshot of player data every time we run the
script. This is what turns a one-off API call into a trend-analysis
tool: without saved history, you can only ever see "now", never
"how has this changed".

Snapshots are saved as simple JSON files in data/snapshots/, one per
run, named by date. Later, analyze.py reads several snapshots back
to compute trends.
"""

import json
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"


def save_snapshot(players: list[dict]) -> Path:
    """
    Saves today's player data slice to data/snapshots/YYYY-MM-DD.json.
    Keeps only the fields we actually care about, to keep files small.
    """
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    trimmed = [
        {
            "id": p["id"],
            "name": p["web_name"],
            "team": p["team"],
            "position": p["element_type"],  # 1=GK 2=DEF 3=MID 4=FWD
            "price": p["now_cost"] / 10,     # FPL stores price as e.g. 55 -> £5.5m
            "total_points": p["total_points"],
            "form": float(p["form"]),
            "points_per_game": float(p["points_per_game"]),
            "minutes": p["minutes"],
            "selected_by_percent": float(p["selected_by_percent"]),
        }
        for p in players
    ]

    snapshot_path = SNAPSHOT_DIR / f"{date.today().isoformat()}.json"
    snapshot_path.write_text(json.dumps(trimmed, indent=2))
    return snapshot_path


def load_all_snapshots() -> dict[str, list[dict]]:
    """
    Loads every saved snapshot, keyed by date string.
    Returns them sorted oldest-to-newest.
    """
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshots = {}
    for file in sorted(SNAPSHOT_DIR.glob("*.json")):
        snapshots[file.stem] = json.loads(file.read_text())
    return snapshots
