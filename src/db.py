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
from datetime import date, datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "fpl.db"
CURRENT_SEASON = "2026/27"  # matches backfill_history.py - used to pull this season's gw history

STATUS_LABELS = {
    "a": "Available", "d": "Doubtful", "i": "Injured",
    "s": "Suspended", "u": "Unavailable", "n": "Not available",
}

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
    photo_code INTEGER,
    full_name TEXT,
    status TEXT,                    -- 'a'=available 'd'=doubtful 'i'=injured 's'=suspended 'u'/'n'=unavailable
    chance_of_playing INTEGER,      -- 0-100, FPL's own estimate for the next match
    news TEXT,                      -- free-text injury/news note, written by FPL's editorial team
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
    selling_price REAL,                -- what you'd actually get if sold now (post sell-on tax)
    purchase_price REAL,               -- what you paid
    PRIMARY KEY (entry_id, gameweek, player_id)
);

CREATE TABLE IF NOT EXISTS entry_summary (
    entry_id INTEGER NOT NULL,
    gameweek INTEGER NOT NULL,
    bank REAL,              -- money in the bank, in £m
    team_value REAL,        -- total squad value, in £m
    gw_points INTEGER,
    gw_rank INTEGER,        -- your rank for this gameweek specifically
    overall_rank INTEGER,
    PRIMARY KEY (entry_id, gameweek)
);

CREATE TABLE IF NOT EXISTS gameweek_summary (
    gameweek INTEGER PRIMARY KEY,
    average_score INTEGER,
    highest_score INTEGER,
    deadline_time TEXT
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
    short_name TEXT NOT NULL,
    team_code INTEGER
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
    # Migration guard: if this DB was created before photo_code/team_code
    # existed, add them now rather than failing. Safe to run every time -
    # SQLite just errors (harmlessly, caught below) if the column's already there.
    for stmt in (
        "ALTER TABLE player_snapshots ADD COLUMN photo_code INTEGER",
        "ALTER TABLE teams ADD COLUMN team_code INTEGER",
        "ALTER TABLE squad_picks ADD COLUMN selling_price REAL",
        "ALTER TABLE squad_picks ADD COLUMN purchase_price REAL",
        "ALTER TABLE entry_summary ADD COLUMN gw_rank INTEGER",
        "ALTER TABLE player_snapshots ADD COLUMN full_name TEXT",
        "ALTER TABLE player_snapshots ADD COLUMN status TEXT",
        "ALTER TABLE player_snapshots ADD COLUMN chance_of_playing INTEGER",
        "ALTER TABLE player_snapshots ADD COLUMN news TEXT",
        "ALTER TABLE gameweek_summary ADD COLUMN deadline_time TEXT",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
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
            p["code"], f"{p['first_name']} {p['second_name']}".strip(),
            p.get("status"), p.get("chance_of_playing_next_round"), p.get("news") or None,
        )
        for p in players
    ]

    conn.executemany(
        """INSERT OR REPLACE INTO player_snapshots
           (player_id, snapshot_date, name, team, position, price,
            total_points, form, points_per_game, minutes, selected_by_percent,
            photo_code, full_name, status, chance_of_playing, news)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    conn.close()
    return today


def _form_baseline_date(conn: sqlite3.Connection, latest_date: str) -> str | None:
    """
    Finds the most recent snapshot_date before latest_date that
    reflects a genuinely earlier gameweek - detected by comparing the
    total points summed across all players. Form (and total_points)
    only move when a gameweek actually gets scored, so two snapshots
    taken on different days but both sitting in the same idle gap
    between gameweeks (e.g. the whole week between GW1 finishing and
    GW2 kicking off) look identical here and this walks further back
    instead of settling for a same-gameweek pair, which would
    otherwise make every form trend read as a flat 0 until the next
    gameweek finishes.

    Returns None if every recorded snapshot reflects the same
    gameweek state as latest_date (there simply isn't a second real
    gameweek on record yet) - callers should treat that as "no trend
    data yet" rather than manufacturing a zero delta.

    Note this is deliberately form/points-specific - price moves day
    to day from transfer-market activity independent of gameweeks, so
    price trends should keep comparing against the immediately
    preceding snapshot rather than this gameweek-aware baseline.
    """
    latest_total = conn.execute(
        "SELECT SUM(total_points) FROM player_snapshots WHERE snapshot_date = ?",
        (latest_date,),
    ).fetchone()[0]

    for row in conn.execute(
        "SELECT DISTINCT snapshot_date FROM player_snapshots WHERE snapshot_date < ? ORDER BY snapshot_date DESC",
        (latest_date,),
    ):
        d = row["snapshot_date"]
        total = conn.execute(
            "SELECT SUM(total_points) FROM player_snapshots WHERE snapshot_date = ?", (d,)
        ).fetchone()[0]
        if total != latest_total:
            return d
    return None


def get_movers(limit: int = 10) -> list[dict]:
    """
    Compares the latest snapshot against the last snapshot that
    reflects a genuinely earlier gameweek, and returns the biggest
    form risers and fallers - the actual SQL query version of what
    analyze.py's compute_trends() did by hand.

    Falls back to ranking by current (GW1, or whichever gameweek is
    most recently completed) form directly whenever no earlier
    gameweek is on record yet to diff against - e.g. right now, in
    the gap between GW1 finishing and GW2 kicking off, where a
    week-over-week diff would just be a meaningless 0 for almost
    everyone. That fallback rows still carry a form_change key (equal
    to current_form) so callers don't need to special-case the shape.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row

    latest_row = conn.execute("SELECT MAX(snapshot_date) AS d FROM player_snapshots").fetchone()
    if latest_row["d"] is None:
        conn.close()
        return []
    latest = latest_row["d"]

    previous = _form_baseline_date(conn, latest)
    if previous is None:
        rows = conn.execute(
            """SELECT player_id, name, price, form AS current_form,
                      form AS form_change, total_points AS points_gained
               FROM player_snapshots
               WHERE snapshot_date = ?
               ORDER BY form DESC
               LIMIT ?""",
            (latest, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    rows = conn.execute(
        """
        SELECT cur.player_id, cur.name, cur.price, cur.form AS current_form,
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


def has_form_trend_baseline() -> bool:
    """
    True once a real earlier-gameweek snapshot exists to diff form
    against, i.e. once get_movers() is returning a genuine trend
    rather than its current-form fallback. Used purely to word the
    Movers & Shakers card subtitle honestly.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    latest_row = conn.execute("SELECT MAX(snapshot_date) AS d FROM player_snapshots").fetchone()
    if latest_row["d"] is None:
        conn.close()
        return False
    baseline = _form_baseline_date(conn, latest_row["d"])
    conn.close()
    return baseline is not None


def save_squad_picks(entry_id: int, gameweek: int, picks_data: dict, player_points: dict[int, int]) -> None:
    """
    Saves one gameweek's squad from the entry/picks API response.
    player_points maps player_id -> points scored that gameweek,
    looked up from the current player_snapshots data so we don't
    need a second API call just for points.

    Also saves each player's selling_price and purchase_price,
    straight from the API - FPL already calculates the sell-on tax
    for us, so we don't need to reimplement that rule ourselves.
    """
    conn = get_connection()
    rows = [
        (
            entry_id, gameweek, p["element"], p["position"], p["multiplier"],
            int(p["is_captain"]), int(p["is_vice_captain"]),
            player_points.get(p["element"]),
            p.get("selling_price", 0) / 10 if p.get("selling_price") else None,
            p.get("purchase_price", 0) / 10 if p.get("purchase_price") else None,
        )
        for p in picks_data["picks"]
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO squad_picks
           (entry_id, gameweek, player_id, squad_position, multiplier,
            is_captain, is_vice_captain, gw_points, selling_price, purchase_price)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    conn.close()


def save_entry_summary(entry_id: int, gameweek: int, entry_history: dict, live_summary: dict | None = None) -> None:
    """
    Saves bank/team value/points/rank for this gameweek. bank/value/
    points come from the picks API's entry_history block. Rank is a
    different story: entry_history's 'rank'/'overall_rank' are frozen
    at the moment that gameweek was scored and don't track the
    standings afterward - confirmed by these staying byte-identical
    across separate runs while the FPL app showed a different overall
    rank. live_summary (from fpl_client.get_entry_summary, the same
    endpoint the app itself reads) has 'summary_event_rank' and
    'summary_overall_rank', which do keep moving - prefer those when
    available, falling back to entry_history's frozen values only if
    the live call wasn't made.
    """
    if live_summary:
        gw_rank = live_summary.get("summary_event_rank")
        overall_rank = live_summary.get("summary_overall_rank")
    else:
        gw_rank = entry_history.get("rank")
        overall_rank = entry_history.get("overall_rank")

    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO entry_summary
           (entry_id, gameweek, bank, team_value, gw_points, gw_rank, overall_rank)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            entry_id, gameweek,
            entry_history.get("bank", 0) / 10,
            entry_history.get("value", 0) / 10,
            entry_history.get("points"),
            gw_rank,
            overall_rank,
        ),
    )
    conn.commit()
    conn.close()


def save_gameweek_summary(events: list[dict]) -> None:
    """
    Saves the league-wide average/highest score and deadline time per
    gameweek - all already present in the bootstrap-static 'events'
    array we fetch every week anyway.

    Deliberately NOT filtered to only gameweeks with a final score -
    future gameweeks have no average_score yet, but we still need
    their deadline_time for the countdown, so every event gets a row.
    """
    conn = get_connection()
    rows = [
        (e["id"], e.get("average_entry_score"), e.get("highest_score"), e.get("deadline_time"))
        for e in events
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO gameweek_summary
           (gameweek, average_score, highest_score, deadline_time) VALUES (?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    conn.close()


def get_manager_stats(entry_id: int) -> dict:
    """
    Your stats for the latest gameweek, alongside the league-wide
    average/highest score for that same gameweek - the comparison
    that makes your own number mean something.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row

    latest_gw = conn.execute(
        "SELECT MAX(gameweek) AS gw FROM entry_summary WHERE entry_id = ?", (entry_id,)
    ).fetchone()["gw"]
    if latest_gw is None:
        conn.close()
        return {}

    entry = conn.execute(
        "SELECT * FROM entry_summary WHERE entry_id = ? AND gameweek = ?",
        (entry_id, latest_gw),
    ).fetchone()
    gw_summary = conn.execute(
        "SELECT * FROM gameweek_summary WHERE gameweek = ?", (latest_gw,)
    ).fetchone()

    conn.close()
    return {
        "gameweek": latest_gw,
        "gw_points": entry["gw_points"] if entry else None,
        "gw_rank": entry["gw_rank"] if entry else None,
        "overall_rank": entry["overall_rank"] if entry else None,
        "bank": entry["bank"] if entry else None,
        "team_value": entry["team_value"] if entry else None,
        "average_score": gw_summary["average_score"] if gw_summary else None,
        "highest_score": gw_summary["highest_score"] if gw_summary else None,
    }


def get_latest_squad(entry_id: int) -> list[dict]:
    """
    Returns the most recent gameweek's squad for this entry, with
    every stat needed for the FPL-style pitch-view toggle (Opponent,
    Points, Price, Selling Price, FDR, Form, Ownership, Price
    Change) - one call, all fields, so the dashboard can embed it
    once and let the person switch views client-side without extra
    queries.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row

    latest_gw = conn.execute(
        "SELECT MAX(gameweek) AS gw FROM squad_picks WHERE entry_id = ?", (entry_id,)
    ).fetchone()["gw"]
    if latest_gw is None:
        conn.close()
        return []

    dates = conn.execute(
        "SELECT DISTINCT snapshot_date FROM player_snapshots ORDER BY snapshot_date DESC LIMIT 2"
    ).fetchall()
    latest_snapshot_date = dates[0]["snapshot_date"]
    previous_snapshot_date = dates[1]["snapshot_date"] if len(dates) > 1 else None

    rows = conn.execute(
        """
        SELECT sp.player_id, sp.squad_position, sp.multiplier, sp.is_captain,
               sp.is_vice_captain, sp.gw_points, sp.selling_price,
               ps.name, ps.position, ps.price, ps.photo_code, ps.team,
               ps.form, ps.selected_by_percent, ps.status, ps.chance_of_playing, ps.news
        FROM squad_picks sp
        JOIN player_snapshots ps
          ON sp.player_id = ps.player_id AND ps.snapshot_date = ?
        WHERE sp.entry_id = ? AND sp.gameweek = ?
        ORDER BY sp.squad_position
        """,
        (latest_snapshot_date, entry_id, latest_gw),
    ).fetchall()

    squad = []
    for r in rows:
        row = dict(r)

        # Next fixture: opponent + FDR (same lookup pattern as captain suggestions)
        fixture = conn.execute(
            """SELECT team_h, team_a, team_h_difficulty, team_a_difficulty
               FROM fixtures WHERE (team_h = ? OR team_a = ?) AND finished = 0
               ORDER BY gameweek ASC LIMIT 1""",
            (row["team"], row["team"]),
        ).fetchone()
        if fixture:
            is_home = fixture["team_h"] == row["team"]
            row["difficulty"] = fixture["team_h_difficulty"] if is_home else fixture["team_a_difficulty"]
            opp_id = fixture["team_a"] if is_home else fixture["team_h"]
            opp = conn.execute("SELECT short_name FROM teams WHERE team_id = ?", (opp_id,)).fetchone()
            row["opponent"] = f"{opp['short_name']} ({'H' if is_home else 'A'})" if opp else None
        else:
            row["difficulty"], row["opponent"] = None, None

        # Price change vs the previous snapshot
        row["price_change"] = None
        if previous_snapshot_date:
            prev = conn.execute(
                "SELECT price FROM player_snapshots WHERE player_id = ? AND snapshot_date = ?",
                (row["player_id"], previous_snapshot_date),
            ).fetchone()
            if prev:
                row["price_change"] = round(row["price"] - prev["price"], 1)

        squad.append(row)

    conn.close()
    return squad


def get_squad_history(entry_id: int) -> dict:
    """
    Returns every gameweek's squad on record for this entry, keyed by
    gameweek number - the weekly pipeline already saves a fresh
    squad_picks row-set every time it runs (via save_squad_picks in
    main.py), so this just reads back everything that's accumulated
    rather than needing any separate "save a snapshot" step.

    Player identity (name, position, photo, team, status) comes from
    the latest snapshot, since that rarely changes; each gameweek's
    role (captain/vice/bench) and points come straight from that
    gameweek's own squad_picks row, so a past week's numbers stay
    exactly what actually happened that week regardless of how the
    player's current form/price has since moved.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row

    latest_date = conn.execute(
        "SELECT MAX(snapshot_date) AS d FROM player_snapshots"
    ).fetchone()["d"]
    if latest_date is None:
        conn.close()
        return {}

    rows = conn.execute(
        """
        SELECT sp.gameweek, sp.player_id, sp.squad_position, sp.multiplier,
               sp.is_captain, sp.is_vice_captain, sp.gw_points,
               ps.name, ps.position, ps.photo_code, ps.status, ps.news
        FROM squad_picks sp
        JOIN player_snapshots ps
          ON sp.player_id = ps.player_id AND ps.snapshot_date = ?
        WHERE sp.entry_id = ?
        ORDER BY sp.gameweek, sp.squad_position
        """,
        (latest_date, entry_id),
    ).fetchall()
    conn.close()

    history = {}
    for r in rows:
        history.setdefault(r["gameweek"], []).append(dict(r))
    return history


def save_teams(teams: list[dict]) -> None:
    """Saves the current team ID -> name mapping, used for readable output."""
    conn = get_connection()
    rows = [(t["id"], t["name"], t["short_name"], t["code"]) for t in teams]
    conn.executemany(
        "INSERT OR REPLACE INTO teams (team_id, name, short_name, team_code) VALUES (?, ?, ?, ?)",
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


def get_squad_player_projections(entry_id: int, min_minutes: int = 0) -> list[dict]:
    """
    Computes the same transparent projected-score formula used for
    captaincy - form x fixture favourability - for EVERY player in
    your 15-man squad (not just starters). This is the shared
    building block behind both get_captain_suggestions and the
    formation optimizer below, so the two features can never
    silently drift out of sync with different formulas.

    score = form * (6 - difficulty) / 5   ...then scaled by availability:
      - Injured/suspended/unavailable -> score forced to 0 (can't play, so
        can't score, no matter how good their form was before)
      - Doubtful -> score scaled by FPL's own "chance of playing" percentage
      - Available -> unchanged

    This means both get_captain_suggestions and get_optimal_formation
    automatically stop recommending unavailable players, without
    either of them needing their own separate injury-checking logic.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row

    latest_date = conn.execute(
        "SELECT MAX(snapshot_date) AS d FROM player_snapshots"
    ).fetchone()["d"]
    latest_gw = conn.execute(
        "SELECT MAX(gameweek) AS gw FROM squad_picks WHERE entry_id = ?", (entry_id,)
    ).fetchone()["gw"]
    if latest_gw is None:
        conn.close()
        return []

    players = conn.execute(
        """SELECT sp.multiplier, ps.player_id, ps.name, ps.team, ps.position,
                  ps.form, ps.price, ps.photo_code, ps.status, ps.chance_of_playing
           FROM squad_picks sp
           JOIN player_snapshots ps ON sp.player_id = ps.player_id AND ps.snapshot_date = ?
           WHERE sp.entry_id = ? AND sp.gameweek = ? AND ps.minutes >= ?""",
        (latest_date, entry_id, latest_gw, min_minutes),
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

        fixture_multiplier = (6 - difficulty) / 5 if difficulty else 1.0
        score = round(p["form"] * fixture_multiplier, 2)

        # Availability adjustment - an injured player's form is irrelevant
        # if they physically can't take the pitch.
        if p["status"] in ("i", "s", "u", "n"):
            score = 0.0
        elif p["status"] == "d":
            chance = p["chance_of_playing"] if p["chance_of_playing"] is not None else 50
            score = round(score * (chance / 100), 2)

        opponent_name = None
        if opponent_id:
            row = conn.execute(
                "SELECT short_name FROM teams WHERE team_id = ?", (opponent_id,)
            ).fetchone()
            opponent_name = row["short_name"] if row else None

        results.append({
            "player_id": p["player_id"], "name": p["name"], "position": p["position"],
            "form": p["form"], "price": p["price"], "photo_code": p["photo_code"],
            "multiplier": p["multiplier"], "difficulty": difficulty,
            "status": p["status"], "chance_of_playing": p["chance_of_playing"],
            "opponent": opponent_name, "is_home": is_home, "score": score,
        })

    conn.close()
    return results


def get_captain_suggestions(entry_id: int, limit: int = 5, min_minutes: int = 60) -> list[dict]:
    """
    Captain suggestion, restricted to your CURRENT STARTING XI only -
    not the full player pool. You can only captain someone who's
    actually in your squad and starting, so that's the only pool
    that makes sense here. Uses get_squad_player_projections above
    for the actual scoring.
    """
    projections = get_squad_player_projections(entry_id, min_minutes)
    starters = [p for p in projections if p["multiplier"] > 0]
    starters.sort(key=lambda r: r["score"], reverse=True)
    return starters[:limit]


VALID_FORMATIONS = [
    (d, m, f)
    for d in range(3, 6)      # 3-5 defenders
    for m in range(2, 6)      # 2-5 midfielders
    for f in range(1, 4)      # 1-3 forwards
    if d + m + f == 10        # +1 GK = 11 starters total
]


def get_optimal_formation(entry_id: int, min_minutes: int = 0) -> dict:
    """
    Tries every FPL-legal formation (3-4-3, 3-5-2, 4-4-2, 4-5-1,
    5-3-2, etc.) and works out which one gives the highest total
    projected score from your actual 15-man squad.

    For a FIXED formation, the best XI is simply the top-N scoring
    players within each position group - since scores are additive
    across independent groups (no interaction between your defence
    and midfield picks), greedy top-N per group is mathematically
    optimal for that formation. The only real work is comparing
    across all valid formations to find the best one overall.

    Returns the recommended formation plus full starting XI + bench +
    suggested captain for EVERY valid formation (not just the best
    one) under "all_formations", keyed off nothing but ordered by
    projected total - so a dashboard can let you switch between
    formations client-side without another query, while still
    defaulting to the recommendation.
    """
    projections = get_squad_player_projections(entry_id, min_minutes)
    if not projections:
        return {"formation": None, "reason": "No squad data available yet."}

    by_position = {1: [], 2: [], 3: [], 4: []}
    for p in projections:
        by_position[p["position"]].append(p)
    for pos in by_position:
        by_position[pos].sort(key=lambda p: p["score"], reverse=True)

    if not by_position[1]:
        return {"formation": None, "reason": "No goalkeeper data available yet."}
    best_gk = by_position[1][0]  # same keeper regardless of outfield shape

    all_formations = []

    for d, m, f in VALID_FORMATIONS:
        if len(by_position[2]) < d or len(by_position[3]) < m or len(by_position[4]) < f:
            continue  # squad doesn't have enough players in this position for this shape

        defenders = by_position[2][:d]
        midfielders = by_position[3][:m]
        forwards = by_position[4][:f]
        xi = [best_gk] + defenders + midfielders + forwards
        total = round(sum(p["score"] for p in xi), 2)
        label = f"{d}-{m}-{f}"

        xi_ids = {p["player_id"] for p in xi}
        bench = [p for p in projections if p["player_id"] not in xi_ids]
        bench.sort(key=lambda p: p["score"], reverse=True)
        # Vice-captain is the second-highest scorer in the XI, not the
        # bench - FPL only lets the armband fall back to the vice if the
        # captain doesn't register a score (didn't play, injured, etc.),
        # and the vice has to already be a starter for that to happen.
        xi_by_score = sorted(xi, key=lambda p: p["score"], reverse=True)
        captain = xi_by_score[0]
        vice_captain = xi_by_score[1] if len(xi_by_score) > 1 else None

        all_formations.append({
            "formation": label, "projected_total": total,
            "starting_xi": xi, "bench": bench, "suggested_captain": captain,
            "suggested_vice_captain": vice_captain,
        })

    if not all_formations:
        return {"formation": None, "reason": "Not enough players in each position yet."}

    all_formations.sort(key=lambda c: c["projected_total"], reverse=True)
    best = dict(all_formations[0])
    best["all_formations"] = all_formations
    return best


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


def get_transfer_suggestions(entry_id: int, limit: int = 5, min_minutes: int = 60) -> list[dict]:
    """
    Flags squad players worth considering transferring out, based on
    three signals:

      - Falling price (a strong community signal others are selling)
      - Falling form (recent performance trending down)
      - Injury/availability status, straight from FPL's own data
        (doubtful/injured/suspended/unavailable)

    urgency_score = (form drop x 2) + (price drop x 10) + injury_urgency
    Price drop is weighted heavily since a falling price is a costly,
    hard-to-reverse signal. Injury status is weighted heavily too,
    but scaled by how uncertain FPL's own "chance of playing" estimate
    is for a doubtful player - a 75% chance is a much softer flag
    than a 25% chance.

    Tiers: score >= 5 -> High, >= 2 -> Medium, > 0 -> Low.
    Players with none of the three signals aren't flagged at all.

    For each flagged player, suggests up to 3 replacement candidates
    in the same position that you could genuinely afford - selling
    price (post sell-on tax, from the real API data) plus your
    current bank, so suggestions respect the actual £100m constraint.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row

    dates = conn.execute(
        "SELECT DISTINCT snapshot_date FROM player_snapshots ORDER BY snapshot_date DESC LIMIT 2"
    ).fetchall()
    if len(dates) < 2:
        conn.close()
        return []  # need at least 2 snapshots to detect a trend
    latest_date, previous_date = dates[0]["snapshot_date"], dates[1]["snapshot_date"]

    latest_gw = conn.execute(
        "SELECT MAX(gameweek) AS gw FROM squad_picks WHERE entry_id = ?", (entry_id,)
    ).fetchone()["gw"]
    if latest_gw is None:
        conn.close()
        return []

    bank_row = conn.execute(
        "SELECT bank FROM entry_summary WHERE entry_id = ? AND gameweek = ?",
        (entry_id, latest_gw),
    ).fetchone()
    bank = bank_row["bank"] if bank_row else 0.0

    squad = conn.execute(
        """SELECT sp.player_id, sp.selling_price, cur.name, cur.position, cur.team,
                  cur.form AS current_form, cur.price AS current_price,
                  cur.status, cur.chance_of_playing, cur.news
           FROM squad_picks sp
           JOIN player_snapshots cur ON sp.player_id = cur.player_id AND cur.snapshot_date = ?
           WHERE sp.entry_id = ? AND sp.gameweek = ?""",
        (latest_date, entry_id, latest_gw),
    ).fetchall()
    squad_ids = [s["player_id"] for s in squad]

    flagged = []
    for s in squad:
        prev = conn.execute(
            "SELECT form, price FROM player_snapshots WHERE player_id = ? AND snapshot_date = ?",
            (s["player_id"], previous_date),
        ).fetchone()
        if not prev:
            continue

        form_drop = max(0.0, prev["form"] - s["current_form"])
        price_drop = max(0.0, prev["price"] - s["current_price"])

        # Injury/availability signal - a genuinely unavailable player is a
        # strong flag on its own; a "doubtful" one scales by how unlikely
        # FPL itself thinks they are to play (lower chance = bigger flag).
        injury_urgency = 0.0
        injury_reason = None
        if s["status"] in ("i", "s", "u", "n"):
            injury_urgency = 8.0
            injury_reason = STATUS_LABELS.get(s["status"], "Unavailable")
        elif s["status"] == "d":
            chance = s["chance_of_playing"] if s["chance_of_playing"] is not None else 50
            injury_urgency = round((100 - chance) / 100 * 5, 1)
            injury_reason = f"Doubtful ({chance}% chance of playing)"

        urgency_score = round(form_drop * 2 + price_drop * 10 + injury_urgency, 1)
        if urgency_score <= 0:
            continue

        tier = "High" if urgency_score >= 5 else "Medium" if urgency_score >= 2 else "Low"
        budget = round((s["selling_price"] or s["current_price"]) + bank, 1)

        placeholders = ",".join("?" * len(squad_ids))
        candidates = conn.execute(
            f"""SELECT player_id, name, price, form, points_per_game,
                       ROUND(points_per_game / price, 3) AS value_score
                FROM player_snapshots
                WHERE snapshot_date = ? AND position = ? AND minutes >= ?
                  AND price <= ? AND player_id NOT IN ({placeholders})
                ORDER BY value_score DESC LIMIT 3""",
            [latest_date, s["position"], min_minutes, budget] + squad_ids,
        ).fetchall()

        flagged.append({
            "player_id": s["player_id"], "name": s["name"], "position": s["position"], "urgency": tier,
            "urgency_score": urgency_score, "form_drop": round(form_drop, 1),
            "price_drop": round(price_drop, 1), "injury_reason": injury_reason,
            "budget_available": budget,
            "candidates": [dict(c) for c in candidates],
        })

    conn.close()
    flagged.sort(key=lambda f: f["urgency_score"], reverse=True)
    return flagged[:limit]


def get_all_player_profiles(min_minutes: int = 0) -> dict:
    """
    Builds a lookup dict of every player's season-to-date profile,
    keyed by player_id - meant to be embedded once in the dashboard
    page as JSON, so any click-to-view-profile popup anywhere on the
    page can look a player up instantly without a fresh query.

    Includes gameweek-by-gameweek history if backfill_history.py has
    been run; falls back gracefully to current-snapshot data alone
    if not (empty gw_history list rather than an error).
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row

    latest_date = conn.execute(
        "SELECT MAX(snapshot_date) AS d FROM player_snapshots"
    ).fetchone()["d"]
    if latest_date is None:
        conn.close()
        return {}

    players = conn.execute(
        """SELECT ps.player_id, ps.name, ps.full_name, ps.position, ps.price, ps.total_points,
                  ps.form, ps.points_per_game, ps.minutes, ps.selected_by_percent,
                  ps.photo_code, ps.status, ps.chance_of_playing, ps.news, t.short_name AS team
           FROM player_snapshots ps
           LEFT JOIN teams t ON ps.team = t.team_id
           WHERE ps.snapshot_date = ? AND ps.minutes >= ?""",
        (latest_date, min_minutes),
    ).fetchall()

    profiles = {}
    for p in players:
        history = conn.execute(
            """SELECT gameweek, total_points, minutes, goals_scored, assists
               FROM player_gw_history
               WHERE player_id = ? AND season = ? AND gameweek IS NOT NULL
               ORDER BY gameweek""",
            (p["player_id"], CURRENT_SEASON),
        ).fetchall()

        season_history = []
        for h in conn.execute(
            """SELECT season, total_points, minutes, goals_scored, assists
               FROM player_gw_history
               WHERE player_id = ? AND season != ? AND gameweek IS NULL
               ORDER BY season DESC""",
            (p["player_id"], CURRENT_SEASON),
        ).fetchall():
            h = dict(h)
            # Past seasons only give us total minutes, not an actual
            # appearance count, so games played (and points/game) is a
            # rough guesstimate: minutes / 90, rounded. That undercounts
            # players with a lot of late-sub cameos and overcounts anyone
            # who played extra time, but it's the best estimate available
            # without a real per-appearance data source.
            games_est = round(h["minutes"] / 90) if h["minutes"] else 0
            h["games_est"] = games_est
            h["points_per_game_est"] = round(h["total_points"] / games_est, 2) if games_est > 0 else None
            season_history.append(h)

        profiles[p["player_id"]] = {
            "name": p["name"], "full_name": p["full_name"] or p["name"],
            "team": p["team"], "position": p["position"],
            "price": p["price"], "total_points": p["total_points"], "form": p["form"],
            "points_per_game": p["points_per_game"], "minutes": p["minutes"],
            "selected_by_percent": p["selected_by_percent"], "photo_code": p["photo_code"],
            "status": p["status"], "status_label": STATUS_LABELS.get(p["status"], "Available"),
            "chance_of_playing": p["chance_of_playing"], "news": p["news"],
            "gw_history": [dict(h) for h in history],
            "season_history": season_history,
        }

    conn.close()
    return profiles


def get_next_deadline() -> dict:
    """
    Finds the soonest upcoming gameweek deadline. Deadline times are
    stored as FPL's own ISO 8601 UTC strings (e.g.
    '2026-08-28T17:30:00Z'), which sort correctly as plain text
    comparisons since they're fixed-width and zero-padded - no date
    parsing needed for the SQL side.

    Returns {} if there's no future deadline in the data (e.g. the
    season's finished, or gameweek_summary hasn't been populated yet).
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute(
        """SELECT gameweek, deadline_time FROM gameweek_summary
           WHERE deadline_time > ? ORDER BY deadline_time ASC LIMIT 1""",
        (now_iso,),
    ).fetchone()
    conn.close()

    return dict(row) if row else {}


POSITION_CODES = {"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}


def get_watchlist(manual_picks: dict, min_minutes: int = 60) -> dict:
    """
    Builds the watchlist: one data-driven pick per position (highest
    points-per-game-per-£1m, same "value score" formula used
    elsewhere), plus your own manual pick per position, resolved by
    name from manual_picks (e.g. {"GK": "Raya", ...}).

    Each pick also carries its form/price change vs a previous
    snapshot, same as the movers/shakers trend logic - so you're not
    just seeing a single week's number, you're watching how that
    specific player is trending over time, exactly the point of a
    watchlist rather than a one-off snapshot.

    Price and form use different baselines, since they move on
    different clocks: price shifts day to day with transfer-market
    activity, so it's compared against the immediately preceding
    snapshot. Form only moves when a gameweek is actually scored, so
    it's compared against the last snapshot that reflects a genuinely
    earlier gameweek (via _form_baseline_date) - otherwise, in the gap
    between gameweeks, it would always read as a flat 0 change even
    though nothing has really been compared yet. When no such earlier
    gameweek exists, form_change is left as None rather than faked.

    A manual slot that's None, blank, or doesn't match any current
    player comes back as {"found": False} rather than being silently
    dropped, so a typo or a transferred-out player is visible on the
    dashboard instead of just quietly missing.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row

    dates = conn.execute(
        "SELECT DISTINCT snapshot_date FROM player_snapshots ORDER BY snapshot_date DESC LIMIT 2"
    ).fetchall()
    latest_date = dates[0]["snapshot_date"]
    previous_date = dates[1]["snapshot_date"] if len(dates) > 1 else None
    form_baseline_date = _form_baseline_date(conn, latest_date)

    def with_trend(row: dict) -> dict:
        """Adds form_change (gameweek-aware baseline) and price_change (previous snapshot), if available."""
        row = dict(row)
        row["form_change"], row["price_change"] = None, None
        if previous_date:
            prev = conn.execute(
                "SELECT price FROM player_snapshots WHERE player_id = ? AND snapshot_date = ?",
                (row["player_id"], previous_date),
            ).fetchone()
            if prev:
                row["price_change"] = round(row["price"] - prev["price"], 1)
        if form_baseline_date:
            prev_form = conn.execute(
                "SELECT form FROM player_snapshots WHERE player_id = ? AND snapshot_date = ?",
                (row["player_id"], form_baseline_date),
            ).fetchone()
            if prev_form:
                row["form_change"] = round(row["form"] - prev_form["form"], 1)
        return row

    watchlist = {}
    for label, pos_code in POSITION_CODES.items():
        data_driven = conn.execute(
            """SELECT player_id, name, price, points_per_game, form, photo_code,
                      ROUND(points_per_game / price, 3) AS value_score
               FROM player_snapshots
               WHERE snapshot_date = ? AND position = ? AND minutes >= ?
               ORDER BY value_score DESC LIMIT 1""",
            (latest_date, pos_code, min_minutes),
        ).fetchone()

        manual_name = (manual_picks or {}).get(label)
        manual_pick = None
        if manual_name:
            manual_row = conn.execute(
                """SELECT player_id, name, price, points_per_game, form, photo_code
                   FROM player_snapshots
                   WHERE snapshot_date = ? AND position = ? AND LOWER(name) = LOWER(?)""",
                (latest_date, pos_code, manual_name),
            ).fetchone()
            manual_pick = with_trend(manual_row) if manual_row else {"found": False, "name": manual_name}

        watchlist[label] = {
            "data_driven": with_trend(data_driven) if data_driven else None,
            "manual": manual_pick,
        }

    conn.close()
    return watchlist


def save_player_gw_history(player_id: int, summary: dict) -> None:
    """
    Saves one player's full history from the element-summary API
    response: this season's results gameweek by gameweek, plus
    past-season summaries. Shared by backfill_history.py (all ~700
    players, run occasionally) and main.py's lighter per-run refresh
    (just the handful of players who've ever been in your squad),
    which keeps get_what_if_scenarios's trajectories current without
    paying for a full backfill on every single run.
    """
    conn = get_connection()

    gw_rows = [
        (
            player_id, CURRENT_SEASON, gw["round"], gw["total_points"],
            gw["minutes"], gw["goals_scored"], gw["assists"],
            gw["value"] / 10,
        )
        for gw in summary.get("history", [])
    ]
    past_rows = [
        (
            player_id, season["season_name"], None, season["total_points"],
            season["minutes"], season["goals_scored"], season["assists"],
            None,  # no single price for a whole past season
        )
        for season in summary.get("history_past", [])
    ]

    conn.executemany(
        """INSERT OR REPLACE INTO player_gw_history
           (player_id, season, gameweek, total_points, minutes,
            goals_scored, assists, price)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        gw_rows + past_rows,
    )
    conn.commit()
    conn.close()


def get_squad_alltime_player_ids(entry_id: int) -> list[int]:
    """
    Every player who has ever appeared in this entry's squad_picks,
    across all recorded gameweeks - the small pool (a season's worth
    of transfers, typically a few dozen players at most) that
    get_what_if_scenarios needs fresh per-gameweek history for, without
    paying for backfill_history.py's full ~700-player sweep on every
    weekly run.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT player_id FROM squad_picks WHERE entry_id = ?", (entry_id,)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_what_if_scenarios(entry_id: int) -> dict:
    """
    For every distinct squad you've held this season - a new one starts
    each time the 15-man player set actually changes via a transfer;
    captain or formation tweaks within the same 15 don't count -
    simulates "what if I'd never touched this squad again": freezes
    that gameweek's starting XI, bench, and captain choice exactly as
    picked, then carries it forward using each player's REAL points
    every following gameweek (from player_gw_history, independent of
    whether you actually owned them that week), so each past version
    of your team can be compared against what actually happened.

    Simplifications, deliberately: captain/formation/bench stay frozen
    at how they were picked on the scenario's starting gameweek (no
    hypothetical re-captaining or auto-subs week to week), and the
    Bench Boost chip isn't modelled - only the frozen starting XI's
    points count each week, same as normal scoring.

    A scenario whose starting gameweek hasn't been played yet (no real
    per-gameweek data exists for it) is left out entirely rather than
    shown with an empty trajectory - it'll appear on its own once that
    gameweek is actually scored.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row

    latest_history_gw = conn.execute(
        "SELECT MAX(gameweek) AS gw FROM player_gw_history WHERE season = ? AND gameweek IS NOT NULL",
        (CURRENT_SEASON,),
    ).fetchone()["gw"]
    if latest_history_gw is None:
        conn.close()
        return {"scenarios": [], "reality": [], "latest_gw": None}

    squad_gws = [
        r["gameweek"] for r in conn.execute(
            "SELECT DISTINCT gameweek FROM squad_picks WHERE entry_id = ? ORDER BY gameweek",
            (entry_id,),
        ).fetchall()
    ]
    if not squad_gws:
        conn.close()
        return {"scenarios": [], "reality": [], "latest_gw": latest_history_gw}

    latest_snapshot_date = conn.execute(
        "SELECT MAX(snapshot_date) AS d FROM player_snapshots"
    ).fetchone()["d"]

    def load_squad(gw: int) -> list[dict]:
        rows = conn.execute(
            """SELECT sp.player_id, sp.squad_position, sp.multiplier,
                      ps.name, ps.position, ps.photo_code
               FROM squad_picks sp
               JOIN player_snapshots ps ON sp.player_id = ps.player_id AND ps.snapshot_date = ?
               WHERE sp.entry_id = ? AND sp.gameweek = ?
               ORDER BY sp.squad_position""",
            (latest_snapshot_date, entry_id, gw),
        ).fetchall()
        return [dict(r) for r in rows]

    # Epoch = a genuinely different 15-man squad. Comparing player_id sets
    # only (not multiplier) means a formation/captain change alone - with
    # no transfer - doesn't start a new scenario.
    epochs = []
    prev_ids = None
    for gw in squad_gws:
        rows = load_squad(gw)
        ids = frozenset(r["player_id"] for r in rows)
        if ids != prev_ids:
            epochs.append((gw, rows))
        prev_ids = ids

    all_player_ids = {r["player_id"] for _, rows in epochs for r in rows}
    points_lookup: dict[tuple[int, int], int] = {}
    if all_player_ids:
        placeholders = ",".join("?" * len(all_player_ids))
        for row in conn.execute(
            f"""SELECT player_id, gameweek, total_points FROM player_gw_history
                WHERE season = ? AND gameweek IS NOT NULL AND player_id IN ({placeholders})""",
            [CURRENT_SEASON] + list(all_player_ids),
        ).fetchall():
            points_lookup[(row["player_id"], row["gameweek"])] = row["total_points"]

    scenarios = []
    for start_gw, rows in epochs:
        if start_gw > latest_history_gw:
            continue  # this squad's first gameweek hasn't been played yet
        starters = [r for r in rows if r["multiplier"] > 0]
        trajectory = []
        cumulative = 0
        for gw in range(start_gw, latest_history_gw + 1):
            gw_points = sum(
                points_lookup.get((r["player_id"], gw), 0) * r["multiplier"]
                for r in starters
            )
            cumulative += gw_points
            trajectory.append({"gameweek": gw, "points": gw_points, "cumulative": cumulative})
        scenarios.append({
            "start_gw": start_gw,
            "squad": [
                {"player_id": r["player_id"], "name": r["name"], "position": r["position"],
                 "photo_code": r["photo_code"], "multiplier": r["multiplier"]}
                for r in starters
            ],
            "trajectory": trajectory,
            "total_to_date": cumulative,
        })

    reality_rows = conn.execute(
        """SELECT gameweek, gw_points FROM entry_summary
           WHERE entry_id = ? AND gameweek <= ? AND gw_points IS NOT NULL
           ORDER BY gameweek""",
        (entry_id, latest_history_gw),
    ).fetchall()
    reality = []
    cumulative = 0
    for r in reality_rows:
        cumulative += r["gw_points"]
        reality.append({"gameweek": r["gameweek"], "points": r["gw_points"], "cumulative": cumulative})

    conn.close()
    return {"scenarios": scenarios, "reality": reality, "latest_gw": latest_history_gw}


def get_top_value(position: int | None = None, min_minutes: int = 90, limit: int = 10) -> list[dict]:
    """Latest snapshot only, ranked by points-per-game per £1m spent."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row

    latest_date = conn.execute(
        "SELECT MAX(snapshot_date) AS d FROM player_snapshots"
    ).fetchone()["d"]

    query = """
        SELECT player_id, name, team, position, price, points_per_game,
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
