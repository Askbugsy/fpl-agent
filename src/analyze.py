"""
analyze.py
----------
This is where snapshots turn into insight. Everything here is plain
maths over the JSON files storage.py saved - no AI needed for this
layer, just comparison.

Two things it can tell you right now:
1. Which players are trending up/down in form since the last snapshot
2. A simple "value score" - points per gameweek, per million spent -
   to spot players outperforming their price tag
"""


def compute_trends(snapshots: dict[str, list[dict]]) -> list[dict]:
    """
    Compares the two most recent snapshots and returns each player's
    change in form and total points between them.

    Needs at least 2 snapshots to say anything about a "trend" -
    with only 1, there's nothing to compare against yet.
    """
    dates = sorted(snapshots.keys())
    if len(dates) < 2:
        return []

    previous = {p["id"]: p for p in snapshots[dates[-2]]}
    latest = {p["id"]: p for p in snapshots[dates[-1]]}

    trends = []
    for player_id, current in latest.items():
        if player_id not in previous:
            continue  # new player, e.g. a transfer window signing
        old = previous[player_id]
        trends.append({
            "name": current["name"],
            "price": current["price"],
            "form_change": round(current["form"] - old["form"], 1),
            "points_gained": current["total_points"] - old["total_points"],
            "current_form": current["form"],
        })

    return trends


def top_value_players(latest_snapshot: list[dict], position: int | None = None, min_minutes: int = 90) -> list[dict]:
    """
    Ranks players by points-per-game per £1m spent - a simple way to
    surface value picks. Filters out players with barely any minutes
    (small sample size, e.g. a sub appearance) since their numbers
    are noisy and unreliable.

    position: filter to 1=GK 2=DEF 3=MID 4=FWD, or None for everyone.
    """
    pool = [p for p in latest_snapshot if p["minutes"] >= min_minutes]
    if position:
        pool = [p for p in pool if p["position"] == position]

    for p in pool:
        p["value_score"] = round(p["points_per_game"] / p["price"], 3)

    return sorted(pool, key=lambda p: p["value_score"], reverse=True)
