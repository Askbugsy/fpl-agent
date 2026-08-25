"""
db.py
-----
A proper local database, replacing the JSON-file snapshots. Same
underlying idea (one row per player per week), but now genuinely
queryable with SQL instead of hand-written Python loops.

SQLite needs no server or installation - it's a single file
(data/fpl.db) that lives in the repo alongside everything else.

Table shape:
    player_snapshots(
        player_id, snapshot_date, name, team, position,
        price, total_points, form, points_per_game,
        minutes, selected_by_percent
    )
One row per player, per date the script has run. Querying "how has
Player X changed over the last 4 weeks" is now a single SQL query
instead of manually loading and comparing JSON files.
"""

import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "fpl.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS player_snapshots (
    player_id INTEGER NOT NULL,
    snapshot_date TEXT NOT NULL,
    name TEXT NOT NULL,
    team INTEGER NOT NULL,
    position INTEGER NOT NULL,
    price REAL NOT NULL,
    total_points INTEGER NOT NULL,
    form REAL NOT NULL,
    points_per_game REAL NOT NULL,
    minutes INTEGER NOT NULL,
    selected_by_percent REAL NOT NULL,
    PRIMARY KEY (player_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS squad_picks (
    entry_id INTEGER NOT NULL,
    gameweek INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    squad_position INTEGER NOT NULL,   -- 1-11 = starting XI, 12-15 = bench
    multiplier INTEGER NOT NULL,       -- 0 = benched, 1 = starter, 2/3 = captain/triple
    is_captain INTEGER NOT NULL,
    is_vice_captain INTEGER NOT NULL,
    gw_points INTEGER,                 -- points that player scored this gameweek
    PRIMARY KEY (entry_id, gameweek, player_id)
);

CREATE TABLE IF NOT EXISTS player_gw_history (
    player_id INTEGER NOT NULL,
    season TEXT NOT NULL,      -- e.g. '2026/27' for current, '2025/26' for past
    gameweek INTEGER,          -- NULL for past-season summary rows
    total_points INTEGER,
    minutes INTEGER,
    goals_scored INTEGER,
    assists INTEGER,
    price REAL,
    PRIMARY KEY (player_id, season, gameweek)
);
"""


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def save_snapshot(players: list[dict]) -> str:
    """
    Inserts today's player data as new rows. Uses INSERT OR REPLACE
    so re-running on the same day updates rather than duplicates.
    """
    today = date.today().isoformat()
    conn = get_connection()

    rows = [
        (
            p["id"], today, p["web_name"], p["team"], p["element_type"],
            p["now_cost"] / 10, p["total_points"], float(p["form"]),
            float(p["points_per_game"]), p["minutes"], float(p["selected_by_percent"]),
        )
        for p in players
    ]

    conn.executemany(
        """INSERT OR REPLACE INTO player_snapshots
           (player_id, snapshot_date, name, team, position, price,
            total_points, form, points_per_game, minutes, selected_by_percent)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    conn.close()
    return today


def get_movers(limit: int = 10) -> list[dict]:
    """
    Compares the two most recent snapshot dates and returns the
    biggest form risers and fallers - the actual SQL query version
    of what analyze.py's compute_trends() did by hand.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row

    dates = conn.execute(
        "SELECT DISTINCT snapshot_date FROM player_snapshots ORDER BY snapshot_date DESC LIMIT 2"
    ).fetchall()
    if len(dates) < 2:
        conn.close()
        return []

    latest, previous = dates[0]["snapshot_date"], dates[1]["snapshot_date"]

    rows = conn.execute(
        """
        SELECT cur.name, cur.price, cur.form AS current_form,
               (cur.form - prev.form) AS form_change,
               (cur.total_points - prev.total_points) AS points_gained
        FROM player_snapshots cur
        JOIN player_snapshots prev
          ON cur.player_id = prev.player_id AND prev.snapshot_date = ?
        WHERE cur.snapshot_date = ?
        ORDER BY form_change DESC
        LIMIT ?
        """,
        (previous, latest, limit),
    ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def save_squad_picks(entry_id: int, gameweek: int, picks_data: dict, player_points: dict[int, int]) -> None:
    """
    Saves one gameweek's squad from the entry/picks API response.
    player_points maps player_id -> points scored that gameweek,
    looked up from the current player_snapshots data so we don't
    need a second API call just for points.
    """
    conn = get_connection()
    rows = [
        (
            entry_id, gameweek, p["element"], p["position"], p["multiplier"],
            int(p["is_captain"]), int(p["is_vice_captain"]),
            player_points.get(p["element"]),
        )
        for p in picks_data["picks"]
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO squad_picks
           (entry_id, gameweek, player_id, squad_position, multiplier,
            is_captain, is_vice_captain, gw_points)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    conn.close()


def get_latest_squad(entry_id: int) -> list[dict]:
    """
    Returns the most recent gameweek's squad for this entry, each
    row joined with the player's current name/team/position from
    the latest player_snapshots data.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row

    latest_gw = conn.execute(
        "SELECT MAX(gameweek) AS gw FROM squad_picks WHERE entry_id = ?", (entry_id,)
    ).fetchone()["gw"]
    if latest_gw is None:
        conn.close()
        return []

    latest_snapshot_date = conn.execute(
        "SELECT MAX(snapshot_date) AS d FROM player_snapshots"
    ).fetchone()["d"]

    rows = conn.execute(
        """
        SELECT sp.squad_position, sp.multiplier, sp.is_captain,
               sp.is_vice_captain, sp.gw_points,
               ps.name, ps.position, ps.price
        FROM squad_picks sp
        JOIN player_snapshots ps
          ON sp.player_id = ps.player_id AND ps.snapshot_date = ?
        WHERE sp.entry_id = ? AND sp.gameweek = ?
        ORDER BY sp.squad_position
        """,
        (latest_snapshot_date, entry_id, latest_gw),
    ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def get_top_value(position: int | None = None, min_minutes: int = 90, limit: int = 10) -> list[dict]:
    """Latest snapshot only, ranked by points-per-game per £1m spent."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row

    latest_date = conn.execute(
        "SELECT MAX(snapshot_date) AS d FROM player_snapshots"
    ).fetchone()["d"]

    query = """
        SELECT name, team, position, price, points_per_game,
               ROUND(points_per_game / price, 3) AS value_score
        FROM player_snapshots
        WHERE snapshot_date = ? AND minutes >= ?
    """
    params: list = [latest_date, min_minutes]
    if position:
        query += " AND position = ?"
        params.append(position)
    query += " ORDER BY value_score DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]
