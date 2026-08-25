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

CREATE TABLE IF NOT EXISTS teams (
    team_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    short_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fixtures (
    fixture_id INTEGER PRIMARY KEY,
    gameweek INTEGER,
    team_h INTEGER NOT NULL,
    team_a INTEGER NOT NULL,
    team_h_difficulty INTEGER,
    team_a_difficulty INTEGER,
    finished INTEGER NOT NULL,
    kickoff_time TEXT
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


def save_teams(teams: list[dict]) -> None:
    """Saves the current team ID -> name mapping, used for readable output."""
    conn = get_connection()
    rows = [(t["id"], t["name"], t["short_name"]) for t in teams]
    conn.executemany(
        "INSERT OR REPLACE INTO teams (team_id, name, short_name) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def save_fixtures(fixtures: list[dict]) -> None:
    """
    Saves the fixture list, including FPL's own 1-5 difficulty
    rating for each side. Re-running this overwrites old rows for
    the same fixture, so difficulty ratings / finished status stay
    current.
    """
    conn = get_connection()
    rows = [
        (
            f["id"], f.get("event"), f["team_h"], f["team_a"],
            f.get("team_h_difficulty"), f.get("team_a_difficulty"),
            int(f["finished"]), f.get("kickoff_time"),
        )
        for f in fixtures
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO fixtures
           (fixture_id, gameweek, team_h, team_a, team_h_difficulty,
            team_a_difficulty, finished, kickoff_time)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    conn.close()


def get_captain_suggestions(limit: int = 5, min_minutes: int = 60) -> list[dict]:
    """
    A transparent, formula-based captain suggestion - not magic,
    just: recent form, weighted by how favourable the player's next
    fixture is (FPL's own 1-5 difficulty rating, lower = easier).

    score = form * (6 - difficulty) / 5

    Easy fixture (difficulty 1) -> multiplier 1.0
    Hard fixture (difficulty 5) -> multiplier 0.2
    No fixture data yet -> falls back to form alone (multiplier 1.0)

    This is a simple, adjustable formula, not a prediction model -
    the point is transparency: you can see exactly why a player is
    ranked where they are, and tune the weighting yourself later.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row

    latest_date = conn.execute(
        "SELECT MAX(snapshot_date) AS d FROM player_snapshots"
    ).fetchone()["d"]

    players = conn.execute(
        """SELECT player_id, name, team, position, form, price
           FROM player_snapshots
           WHERE snapshot_date = ? AND minutes >= ?""",
        (latest_date, min_minutes),
    ).fetchall()

    results = []
    for p in players:
        fixture = conn.execute(
            """SELECT team_h, team_a, team_h_difficulty, team_a_difficulty, gameweek
               FROM fixtures
               WHERE (team_h = ? OR team_a = ?) AND finished = 0
               ORDER BY gameweek ASC LIMIT 1""",
            (p["team"], p["team"]),
        ).fetchone()

        difficulty, opponent_id, is_home = None, None, None
        if fixture:
            is_home = fixture["team_h"] == p["team"]
            difficulty = fixture["team_h_difficulty"] if is_home else fixture["team_a_difficulty"]
            opponent_id = fixture["team_a"] if is_home else fixture["team_h"]

        multiplier = (6 - difficulty) / 5 if difficulty else 1.0
        score = round(p["form"] * multiplier, 2)

        opponent_name = None
        if opponent_id:
            row = conn.execute(
                "SELECT short_name FROM teams WHERE team_id = ?", (opponent_id,)
            ).fetchone()
            opponent_name = row["short_name"] if row else None

        results.append({
            "name": p["name"], "position": p["position"], "form": p["form"],
            "price": p["price"], "difficulty": difficulty,
            "opponent": opponent_name, "is_home": is_home, "score": score,
        })

    conn.close()
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def get_squad_team_ids(entry_id: int) -> list[int]:
    """The set of clubs currently represented in your squad."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    latest_gw = conn.execute(
        "SELECT MAX(gameweek) AS gw FROM squad_picks WHERE entry_id = ?", (entry_id,)
    ).fetchone()["gw"]
    latest_date = conn.execute("SELECT MAX(snapshot_date) AS d FROM player_snapshots").fetchone()["d"]
    rows = conn.execute(
        """SELECT DISTINCT ps.team FROM squad_picks sp
           JOIN player_snapshots ps ON sp.player_id = ps.player_id AND ps.snapshot_date = ?
           WHERE sp.entry_id = ? AND sp.gameweek = ?""",
        (latest_date, entry_id, latest_gw),
    ).fetchall()
    conn.close()
    return [r["team"] for r in rows]


def get_double_and_blank_gameweeks(team_ids: list[int], lookahead: int = 8) -> dict:
    """
    Scans upcoming fixtures for each team and flags:
      - doubles: gameweeks where a team plays 2+ times (Triple Captain signal)
      - blanks:  gameweeks where a team has no fixture at all (Bench Boost risk /
                 Free Hit signal)
    Only looks at unfinished fixtures, within `lookahead` gameweeks of the
    earliest upcoming one.
    """
    if not team_ids:
        return {"doubles": [], "blanks": []}

    conn = get_connection()
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(team_ids))

    earliest = conn.execute(
        "SELECT MIN(gameweek) AS gw FROM fixtures WHERE finished = 0"
    ).fetchone()["gw"]
    if earliest is None:
        conn.close()
        return {"doubles": [], "blanks": []}

    counts = conn.execute(
        f"""
        SELECT gameweek, team_id, COUNT(*) AS n FROM (
            SELECT gameweek, team_h AS team_id FROM fixtures
            WHERE finished = 0 AND team_h IN ({placeholders}) AND gameweek BETWEEN ? AND ?
            UNION ALL
            SELECT gameweek, team_a AS team_id FROM fixtures
            WHERE finished = 0 AND team_a IN ({placeholders}) AND gameweek BETWEEN ? AND ?
        ) GROUP BY gameweek, team_id
        """,
        team_ids + [earliest, earliest + lookahead] + team_ids + [earliest, earliest + lookahead],
    ).fetchall()

    all_gws = range(earliest, earliest + lookahead + 1)
    seen = {(r["gameweek"], r["team_id"]): r["n"] for r in counts}

    doubles, blanks = [], []
    for gw in all_gws:
        for team_id in team_ids:
            n = seen.get((gw, team_id), 0)
            team_name = conn.execute(
                "SELECT short_name FROM teams WHERE team_id = ?", (team_id,)
            ).fetchone()
            name = team_name["short_name"] if team_name else str(team_id)
            if n >= 2:
                doubles.append({"gameweek": gw, "team": name})
            elif n == 0:
                blanks.append({"gameweek": gw, "team": name})

    conn.close()
    return {"doubles": doubles, "blanks": blanks}


def get_chip_suggestions(entry_id: int) -> dict:
    """
    Plain-language chip timing hints, built only from doubles/blanks
    detected above. Deliberately conservative - if there's no clear
    signal, it says so rather than guessing.
    """
    team_ids = get_squad_team_ids(entry_id)
    dg = get_double_and_blank_gameweeks(team_ids)

    if dg["doubles"]:
        gws = sorted(set(d["gameweek"] for d in dg["doubles"]))
        teams_by_gw = {gw: [d["team"] for d in dg["doubles"] if d["gameweek"] == gw] for gw in gws}
        best_gw = gws[0]
        triple_captain = (
            f"GW{best_gw}: {', '.join(teams_by_gw[best_gw])} play(s) twice - "
            f"strong Triple Captain / Bench Boost window."
        )
    else:
        triple_captain = "No double gameweeks detected in your squad's next few fixtures yet - hold both chips."

    if dg["blanks"]:
        gws = sorted(set(b["gameweek"] for b in dg["blanks"]))
        counts = {gw: sum(1 for b in dg["blanks"] if b["gameweek"] == gw) for gw in gws}
        worst_gw = max(counts, key=counts.get)
        free_hit = f"GW{worst_gw}: {counts[worst_gw]} of your teams have no fixture - possible Free Hit target."
    else:
        free_hit = "No blank gameweeks detected yet for your squad's teams."

    return {"triple_captain_bench_boost": triple_captain, "free_hit": free_hit}


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
