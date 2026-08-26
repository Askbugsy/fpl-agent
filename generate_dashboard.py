"""
generate_dashboard.py
----------------------
Builds a single, self-contained dashboard.html - no server, no
build step, just a file you open (or that GitHub Pages hosts for
you). Charts are drawn with Chart.js, loaded from a CDN.

Layout: a persistent deadline countdown sits above a tabbed
interface (Today / Squad / Moves / Explore) - only one section
visible at a time, since a single long scroll of every section
stacked full-width gets unwieldy fast on a phone.

Run this after main.py in the workflow; it reads straight from
data/fpl.db.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from db import get_connection, get_movers, get_top_value, get_latest_squad, get_captain_suggestions, get_chip_suggestions, get_transfer_suggestions, get_all_player_profiles, get_optimal_formation, get_manager_stats, get_next_deadline, get_watchlist
from config import TEAM_ID, MY_WATCHLIST

OUTPUT_PATH = Path(__file__).parent / "docs" / "index.html"
POSITION_NAMES = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

# The "premierleague25" segment is a Premier League-side asset bucket, not
# something exposed by any bootstrap-static field - confirmed against the
# real FPL site's own image URL. It's bumped by Premier League itself when
# they refresh player photos for kit changes/transfers/a new season, so if
# photos start showing stale kits again, check the real site's image URL
# again and update this constant to match. 250x250 (and larger) return
# HTTP 403 on the real CDN - 110x140 is the size actually served.
PLAYER_PHOTO_BASE = "https://resources.premierleague.com/premierleague25/photos/players"

# Fallback bucket for players who don't have a photo in the current-season
# bucket above. Can serve a stale, wrong-kit photo for players who've since
# had their current-season photo published, so it's only used as a second
# choice, never first.
PLAYER_PHOTO_LEGACY_BASE = "https://resources.premierleague.com/premierleague/photos/players"

# Shown in place of a player photo that fails to load on both buckets above
# - some players simply don't have a headshot on Premier League's CDN yet
# at all. A plain silhouette matching the light theme, inlined as base64 so
# it never needs its own network request and can't itself fail to load.
PLAYER_PHOTO_FALLBACK = (
    "data:image/svg+xml;base64,"
    "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxMDAgMTAwIj48cmVjdCB3aWR0aD0iMTAwIiBoZWlnaHQ9IjEwMCIgZmlsbD0iI0YzRTNENiIvPjxjaXJjbGUgY3g9IjUwIiBjeT0iMzgiIHI9IjE4IiBmaWxsPSIjOEE4MDc0Ii8+PHBhdGggZD0iTTIwIDkwYzAtMjIgMTMtMzYgMzAtMzZzMzAgMTQgMzAgMzYiIGZpbGw9IiM4QTgwNzQiLz48L3N2Zz4="
)


def get_latest_full_table() -> list[dict]:
    conn = get_connection()
    conn.row_factory = None
    latest_date = conn.execute("SELECT MAX(snapshot_date) FROM player_snapshots").fetchone()[0]
    rows = conn.execute(
        """SELECT player_id, name, team, position, price, total_points, form,
                  points_per_game, selected_by_percent
           FROM player_snapshots WHERE snapshot_date = ?
           ORDER BY total_points DESC""",
        (latest_date,),
    ).fetchall()
    conn.close()
    cols = ["player_id", "name", "team", "position", "price", "total_points", "form", "points_per_game", "selected_by_percent"]
    return [dict(zip(cols, r)) for r in rows], latest_date


def player_link(player_id: int, name: str) -> str:
    """Wraps a player's name in a clickable span that opens the shared profile popup."""
    return f'<span class="player-link" onclick="openProfile({player_id})">{name}</span>'


def captain_role_tag(i: int) -> str:
    if i == 0:
        return ' <span class="role-tag role-captain">Captain</span>'
    if i == 1:
        return ' <span class="role-tag role-vice">Vice-Captain</span>'
    return ""


def render_watchlist_pick(pick: dict) -> str:
    if not pick:
        return '<span class="muted">not set</span>'
    if pick.get("found") is False:
        return f'<span class="text-bad">"{pick["name"]}" not found &mdash; check config.py</span>'

    trend = ""
    if pick.get("form_change") is not None:
        fc, pc = pick["form_change"], pick["price_change"]
        fc_cls = "text-good" if fc > 0 else "text-bad" if fc < 0 else "muted"
        pc_cls = "text-good" if pc > 0 else "text-bad" if pc < 0 else "muted"
        trend = f' <span class="{fc_cls}">form {fc:+.1f}</span> <span class="{pc_cls}">price {pc:+.1f}m</span>'

    return f'{player_link(pick["player_id"], pick["name"])} &mdash; £{pick["price"]}m{trend}'


def render_watchlist_card(watchlist: dict) -> str:
    rows = ""
    for pos, picks in watchlist.items():
        rows += f'''
        <div class="watchlist-row">
          <div class="watchlist-pos">{pos}</div>
          <div class="watchlist-col"><span class="subtitle">Data-driven</span><br>{render_watchlist_pick(picks["data_driven"])}</div>
          <div class="watchlist-col"><span class="subtitle">Your pick</span><br>{render_watchlist_pick(picks["manual"])}</div>
        </div>'''
    return rows


def render_formation_card(formation: dict) -> str:
    """
    Renders the comparison table server-side (so it's visible even if
    JS fails to load) but leaves the pitch/bench/summary as empty
    placeholders - selectFormation() in the <script> block below fills
    those in from the embedded per-formation JSON, and swaps them
    again whenever a comparison row is clicked. Keeps the actual
    pitch-building logic (photos, captain/vice tags, status dots) in
    exactly one place (JS) instead of duplicating it between Python
    and JS.
    """
    if not formation.get("formation"):
        return f'<p>{formation.get("reason", "Not enough data yet.")}</p>'

    def comparison_row(c: dict) -> str:
        is_best = c["formation"] == formation["formation"]
        best_tag = ' <span class="best-tag">Recommended</span>' if is_best else ""
        return (
            f'<div class="formation-compare-row{" best" if is_best else ""}" '
            f'data-formation="{c["formation"]}" onclick="selectFormation(\'{c["formation"]}\')">'
            f'{c["formation"]} &mdash; {c["projected_total"]} pts{best_tag}</div>'
        )

    comparison_rows = "".join(comparison_row(c) for c in formation["all_formations"])

    return f'''
    <p id="formationSummary"></p>
    <div class="pitch" id="formationPitch"></div>
    <div class="formation-compare">{comparison_rows}</div>
    <strong class="block-label">Bench</strong>
    <div id="formationBench"></div>
    '''


def render_transfer_suggestion(t: dict) -> str:
    urgency_class = {"High": "badge-bad", "Medium": "badge-warn", "Low": "badge-muted"}.get(t["urgency"], "badge-muted")
    reasons = []
    if t.get("injury_reason"):
        reasons.append(t["injury_reason"])
    if t["form_drop"]:
        reasons.append(f"form -{t['form_drop']}")
    if t["price_drop"]:
        reasons.append(f"price -£{t['price_drop']}m")
    reason_text = ", ".join(reasons)

    candidates_html = "".join(
        f'<div class="candidate-row">&rarr; {player_link(c["player_id"], c["name"])} £{c["price"]}m '
        f'(form {c["form"]}, value {c["value_score"]})</div>'
        for c in t["candidates"]
    ) or '<div class="candidate-row">No affordable replacement found in this position.</div>'

    return f'''
    <div class="transfer-card">
      <span class="urgency-badge {urgency_class}">{t["urgency"]}</span>
      <strong>{player_link(t["player_id"], t["name"])}</strong> ({POSITION_NAMES.get(t["position"], "?")}) &mdash; {reason_text}
      <div class="budget-line">Budget if sold: £{t["budget_available"]}m</div>
      {candidates_html}
    </div>'''


def build_html() -> str:
    movers = get_movers(limit=10)
    value_picks = get_top_value(limit=10)
    full_table, latest_date = get_latest_full_table()
    squad = get_latest_squad(TEAM_ID)
    captain_picks = get_captain_suggestions(TEAM_ID, limit=5)
    vice_backup_note = ""
    if len(captain_picks) >= 2:
        vice_backup_note = (
            '<div class="squad-row bench">If ' + captain_picks[0]["name"] +
            " doesn't register a score (didn't play, injured, red-carded before kickoff), "
            "the armband automatically passes to " + captain_picks[1]["name"] + ".</div>"
        )
    chip_advice = get_chip_suggestions(TEAM_ID)
    transfer_suggestions = get_transfer_suggestions(TEAM_ID)

    for row in full_table:
        row["position"] = POSITION_NAMES.get(row["position"], "?")
    for row in squad:
        row["position"] = POSITION_NAMES.get(row["position"], "?")

    starters = [r for r in squad if r["multiplier"] > 0]
    bench = [r for r in squad if r["multiplier"] == 0]
    squad_total = sum((r["gw_points"] or 0) * r["multiplier"] for r in starters)

    profiles = get_all_player_profiles()
    formation = get_optimal_formation(TEAM_ID)
    manager_stats = get_manager_stats(TEAM_ID)
    next_deadline = get_next_deadline()
    watchlist = get_watchlist(MY_WATCHLIST)

    formations_by_label = {c["formation"]: c for c in formation.get("all_formations", [])}

    movers_json = json.dumps(movers)
    value_json = json.dumps(value_picks)
    table_json = json.dumps(full_table)
    profiles_json = json.dumps(profiles)
    squad_json = json.dumps(squad)
    formations_json = json.dumps(formations_by_label)
    best_formation_json = json.dumps(formation.get("formation"))
    photo_base_json = json.dumps(PLAYER_PHOTO_BASE)
    photo_legacy_base_json = json.dumps(PLAYER_PHOTO_LEGACY_BASE)
    photo_fallback_json = json.dumps(PLAYER_PHOTO_FALLBACK)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Real Bugsy United &mdash; FPL Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  :root {{
    --bg: #FAF7F1; --surface: #FFFFFF; --border: #E8E1D3;
    --ink: #2B2620; --ink-soft: #8A8074;
    --clay: #C1613C; --clay-tint: #F3E3D6;
    --good: #5B7B4F; --warn: #BF8B32; --bad: #B0402A;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    margin: 0; padding: 0 0 40px; background: var(--bg); color: var(--ink);
  }}
  h1, h2 {{ font-family: "Fraunces", Georgia, serif; font-weight: 600; margin: 0; }}
  header {{ padding: 20px 16px 12px; }}
  header h1 {{ font-size: 1.3rem; }}
  .updated {{ color: var(--ink-soft); font-size: 0.8rem; margin-top: 2px; }}

  .hero {{
    margin: 0 16px 16px; background: linear-gradient(135deg, var(--clay-tint), var(--surface));
    border: 1px solid var(--border); border-radius: 14px; padding: 18px; text-align: center;
  }}
  .hero h2 {{ font-size: 0.95rem; color: var(--ink-soft); font-weight: 500; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px; }}
  .countdown {{ font-family: "Fraunces", Georgia, serif; font-size: 2.4rem; font-weight: 700; color: var(--clay); letter-spacing: 0.01em; }}
  .countdown.urgent {{ color: var(--bad); }}

  .tabs {{ position: sticky; top: 0; z-index: 10; background: var(--bg); display: flex; gap: 6px;
           padding: 8px 16px; overflow-x: auto; border-bottom: 1px solid var(--border); }}
  .tab-btn {{ background: var(--surface); border: 1px solid var(--border); color: var(--ink-soft);
              border-radius: 20px; padding: 7px 16px; font-size: 0.85rem; font-weight: 600;
              cursor: pointer; white-space: nowrap; }}
  .tab-btn.active {{ background: var(--clay); color: #fff; border-color: var(--clay); }}

  .tab-panel {{ display: none; padding: 16px; }}
  .tab-panel.active {{ display: block; }}

  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
           padding: 16px; margin-bottom: 16px; }}
  .card h2 {{ font-size: 1.05rem; margin-bottom: 10px; }}
  .subtitle {{ color: var(--ink-soft); font-size: 0.75rem; font-weight: normal; font-family: inherit; }}
  .muted {{ color: var(--ink-soft); }}
  .text-good {{ color: var(--good); }} .text-bad {{ color: var(--bad); }} .text-warn {{ color: var(--warn); }}
  .block-label {{ display: block; margin: 12px 0 4px; }}

  canvas {{ max-height: 300px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th, td {{ text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--border); }}
  th {{ cursor: pointer; color: var(--clay); position: sticky; top: 0; background: var(--surface); font-weight: 600; }}
  .scroll {{ max-height: 420px; overflow-y: auto; }}

  .squad-row {{ padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 0.9rem;
                display: flex; align-items: center; gap: 8px; }}
  .player-pic {{ width: 32px; height: 40px; object-fit: cover; border-radius: 6px; background: transparent; flex-shrink: 0; }}
  .squad-row.bench {{ color: var(--ink-soft); }}
  .pos-tag {{ color: var(--clay); font-size: 0.72rem; font-weight: 600; }}

  .transfer-card {{ border: 1px solid var(--border); border-radius: 10px; padding: 12px; margin-bottom: 10px; font-size: 0.9rem; }}
  .urgency-badge {{ display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 0.7rem; font-weight: 700; color: #fff; margin-right: 6px; }}
  .badge-bad {{ background: var(--bad); }} .badge-warn {{ background: var(--warn); }} .badge-muted {{ background: var(--ink-soft); }}
  .budget-line {{ color: var(--ink-soft); font-size: 0.8rem; margin: 5px 0; }}
  .candidate-row {{ font-size: 0.85rem; color: var(--clay); padding-left: 6px; }}

  .player-link {{ color: var(--ink); text-decoration: underline dotted var(--clay); cursor: pointer; font-weight: 500; }}
  .player-link:hover {{ color: var(--clay); }}

  #profileModal {{ display: none; position: fixed; inset: 0; background: rgba(43,38,32,0.55); z-index: 100;
                    align-items: center; justify-content: center; padding: 16px; }}
  #profileModal.open {{ display: flex; }}
  .modal-content {{ background: var(--surface); border-radius: 16px; padding: 20px; max-width: 400px;
                     width: 100%; max-height: 80vh; overflow-y: auto; }}
  .modal-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }}
  .modal-header img {{ width: 56px; height: 70px; object-fit: cover; border-radius: 8px; background: transparent; }}
  .modal-header strong {{ font-family: "Fraunces", serif; font-size: 1.1rem; }}
  .modal-close {{ float: right; cursor: pointer; color: var(--ink-soft); font-size: 1.3rem; }}
  .modal-stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; font-size: 0.85rem; margin: 12px 0; }}
  .modal-stats div {{ display: flex; justify-content: space-between; border-bottom: 1px solid var(--border); padding: 4px 0; }}
  .modal-gw-table {{ width: 100%; font-size: 0.8rem; margin-top: 10px; }}
  .modal-gw-table th, .modal-gw-table td {{ padding: 4px 6px; text-align: center; border-bottom: 1px solid var(--border); }}

  .pitch {{ background: linear-gradient(180deg, #6B8F5C, #5B7B4F); border-radius: 12px; padding: 14px 6px; margin: 12px 0; }}
  .pitch-row {{ display: flex; justify-content: space-around; flex-wrap: wrap; margin: 8px 0; }}
  .pitch-player {{ background: rgba(255,255,255,0.92); border-radius: 8px; padding: 5px 9px; text-align: center; font-size: 0.75rem; min-width: 62px; }}
  .pitch-player.is-captain {{ border: 2px solid var(--clay); }}
  .pitch-player.is-vice-captain {{ border: 2px dashed var(--ink-soft); }}
  .pitch-pic {{ width: 40px; height: 50px; object-fit: cover; border-radius: 6px; background: transparent; display: block; margin: 0 auto 4px; }}
  .pitch-score {{ color: var(--ink-soft); font-size: 0.7rem; }}
  .formation-compare {{ margin-top: 12px; font-size: 0.8rem; }}
  .formation-compare-row {{ padding: 5px 8px; color: var(--ink-soft); cursor: pointer; border-radius: 6px; border: 1px solid transparent; }}
  .formation-compare-row:hover {{ background: var(--clay-tint); }}
  .formation-compare-row.best {{ color: var(--good); font-weight: 700; }}
  .formation-compare-row.selected {{ border-color: var(--clay); background: var(--clay-tint); }}
  .best-tag {{ font-size: 0.65rem; font-weight: 700; color: #fff; background: var(--good); border-radius: 8px; padding: 1px 8px; margin-left: 4px; }}

  .toggle-row {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }}
  .toggle-btn {{ background: var(--bg); border: 1px solid var(--border); color: var(--ink-soft); border-radius: 8px;
                  padding: 5px 10px; font-size: 0.75rem; cursor: pointer; }}
  .toggle-btn.active {{ background: var(--clay); color: #fff; border-color: var(--clay); font-weight: 700; }}

  .status-badge {{ font-size: 0.7rem; }}
  .status-red {{ color: var(--bad); }} .status-amber {{ color: var(--warn); }}

  .role-tag {{ font-size: 0.65rem; font-weight: 700; color: #fff; border-radius: 8px; padding: 1px 8px; margin-left: 4px; }}
  .role-captain {{ background: var(--clay); }}
  .role-vice {{ background: var(--ink-soft); }}

  .watchlist-row {{ display: grid; grid-template-columns: 46px 1fr 1fr; gap: 8px; padding: 9px 0;
                     border-bottom: 1px solid var(--border); font-size: 0.85rem; align-items: start; }}
  .watchlist-pos {{ font-weight: 700; color: var(--clay); }}
  .watchlist-col .subtitle {{ display: block; margin-bottom: 2px; }}
</style>
</head>
<body>
  <header>
    <h1>Real Bugsy United</h1>
    <div class="updated">Latest data: {latest_date}</div>
  </header>

  {"" if not next_deadline else f'''
  <div class="hero">
    <h2>Gameweek {next_deadline["gameweek"]} deadline</h2>
    <div id="countdown" class="countdown" data-deadline="{next_deadline["deadline_time"]}">calculating&hellip;</div>
  </div>
  '''}

  <div class="tabs" id="tabBar">
    <button class="tab-btn active" data-tab="squad">Squad</button>
    <button class="tab-btn" data-tab="today">Recommendations</button>
    <button class="tab-btn" data-tab="moves">Moves</button>
    <button class="tab-btn" data-tab="explore">Explore</button>
  </div>

  <div class="tab-panel active" id="tab-squad">
    <div class="card">
      <h2>Manager Stats &mdash; Gameweek {manager_stats.get('gameweek', '?')}</h2>
      {"<p>No manager data yet.</p>" if not manager_stats else f'''
      <div class="modal-stats">
        <div><span>Your points</span><span><strong>{manager_stats.get('gw_points', '-')}</strong></span></div>
        <div><span>League average</span><span>{manager_stats.get('average_score', '-')}</span></div>
        <div><span>League highest</span><span>{manager_stats.get('highest_score', '-')}</span></div>
        <div><span>GW rank</span><span>{f"{manager_stats['gw_rank']:,}" if manager_stats.get('gw_rank') else '-'}</span></div>
        <div><span>Overall rank</span><span>{f"{manager_stats['overall_rank']:,}" if manager_stats.get('overall_rank') else '-'}</span></div>
        <div><span>Squad value</span><span>£{manager_stats.get('team_value', '-')}m</span></div>
        <div><span>Bank</span><span>£{manager_stats.get('bank', '-')}m</span></div>
      </div>
      '''}
    </div>

    <div class="card">
      <h2>My Squad {"" if not starters else f"&mdash; {squad_total} pts this gameweek"}</h2>
      {"<p>No squad data yet - runs after the current gameweek's picks are published.</p>" if not squad else '''
      <div class="toggle-row" id="squadToggle">
        <button class="toggle-btn active" data-metric="points">Points</button>
        <button class="toggle-btn" data-metric="opponent">Opponent</button>
        <button class="toggle-btn" data-metric="price">Price</button>
        <button class="toggle-btn" data-metric="selling_price">Selling</button>
        <button class="toggle-btn" data-metric="difficulty">FDR</button>
        <button class="toggle-btn" data-metric="form">Form</button>
        <button class="toggle-btn" data-metric="selected_by_percent">Own%</button>
        <button class="toggle-btn" data-metric="price_change">Price &Delta;</button>
      </div>
      <div class="pitch" id="squadPitch"></div>
      '''}
    </div>
  </div>

  <div class="tab-panel" id="tab-today">
    <div class="card">
      <h2>Best Formation for Upcoming Fixtures</h2>
      {render_formation_card(formation)}
    </div>

    <div class="card">
      <h2>Captain Suggestions <span class="subtitle">(form &times; fixture favourability)</span></h2>
      {"<p>Not enough fixture data yet.</p>" if not captain_picks else "".join(
          f'<div class="squad-row">{i+1}. {player_link(c["player_id"], c["name"])}'
          f'{captain_role_tag(i)} vs {c["opponent"] or "?"} '
          f'({"H" if c["is_home"] else "A" if c["is_home"] is not None else "?"}, FDR {c["difficulty"] or "?"}) '
          f'&mdash; form {c["form"]}, score <strong>{c["score"]}</strong></div>'
          for i, c in enumerate(captain_picks)
      )}
      {vice_backup_note}
    </div>

    <div class="card">
      <h2>Chip Timing <span class="subtitle">(double/blank gameweek detection)</span></h2>
      <div class="squad-row">🎯 <strong>Triple Captain / Bench Boost:</strong> {chip_advice['triple_captain_bench_boost']}</div>
      <div class="squad-row">🃏 <strong>Free Hit:</strong> {chip_advice['free_hit']}</div>
      <div class="squad-row bench">Wildcard timing isn't scored here &mdash; that call depends on broader squad health and fixture swings, better discussed directly.</div>
    </div>
  </div>

  <div class="tab-panel" id="tab-moves">
    <div class="card">
      <h2>Transfer Suggestions <span class="subtitle">(form + price trend, budget-aware)</span></h2>
      {"<p>No players flagged - either everything's stable, or only one snapshot exists so far (trends need two).</p>" if not transfer_suggestions else "".join(render_transfer_suggestion(t) for t in transfer_suggestions)}

      <strong class="block-label" style="font-family:'Fraunces',serif;font-size:1rem;">Watchlist <span class="subtitle">(1 data-driven + 1 yours, per position)</span></strong>
      <p class="subtitle">Incoming transfer candidates &mdash; who the data likes, and who you're personally tracking.</p>
      {render_watchlist_card(watchlist)}
    </div>

    <div class="card">
      <h2>Movers &amp; Shakers <span class="subtitle">(form change since last week)</span></h2>
      <canvas id="moversChart"></canvas>
    </div>

    <div class="card">
      <h2>Top Value Picks <span class="subtitle">(points/game per £1m)</span></h2>
      <canvas id="valueChart"></canvas>
    </div>
  </div>

  <div class="tab-panel" id="tab-explore">
    <div class="card">
      <h2>Full Table</h2>
      <div class="toggle-row" id="positionFilter">
        <button class="toggle-btn active" data-position="All">All</button>
        <button class="toggle-btn" data-position="GK">GK</button>
        <button class="toggle-btn" data-position="DEF">DEF</button>
        <button class="toggle-btn" data-position="MID">MID</button>
        <button class="toggle-btn" data-position="FWD">FWD</button>
      </div>
      <div class="scroll">
        <table id="fullTable">
          <thead>
            <tr>
              <th onclick="sortTable(0)">Name</th>
              <th onclick="sortTable(1)">Pos</th>
              <th onclick="sortTable(2)">Price</th>
              <th onclick="sortTable(3)">Pts</th>
              <th onclick="sortTable(4)">Form</th>
              <th onclick="sortTable(5)">PPG</th>
              <th onclick="sortTable(6)">Own%</th>
            </tr>
          </thead>
          <tbody id="tableBody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <div id="profileModal" onclick="if(event.target===this) closeProfile()">
    <div class="modal-content">
      <span class="modal-close" onclick="closeProfile()">&times;</span>
      <div id="modalBody"></div>
    </div>
  </div>

<script>
const movers = {movers_json};
const valuePicks = {value_json};
const fullTable = {table_json};
const playerProfiles = {profiles_json};
const squadData = {squad_json};
const allFormations = {formations_json};
const bestFormationLabel = {best_formation_json};
const PLAYER_PHOTO_BASE = {photo_base_json};
const PLAYER_PHOTO_LEGACY_BASE = {photo_legacy_base_json};
const PLAYER_PHOTO_FALLBACK = {photo_fallback_json};
const POS_NAMES = {{1: 'GK', 2: 'DEF', 3: 'MID', 4: 'FWD'}};

// Current-season photo first (correct kit when it exists); if that 403s,
// fall back to the legacy bucket (a real photo, but can be a stale/wrong
// kit for anyone transferred or re-photographed this season); only show
// the silhouette if neither bucket has anything at all.
function photoOnErrorAttr(photoCode) {{
  const legacyUrl = `${{PLAYER_PHOTO_LEGACY_BASE}}/110x140/p${{photoCode}}.png`;
  return `this.onerror=function(){{this.onerror=null;this.src='${{PLAYER_PHOTO_FALLBACK}}';}};this.src='${{legacyUrl}}'`;
}}

document.getElementById('tabBar').addEventListener('click', (e) => {{
  if (!e.target.classList.contains('tab-btn')) return;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  e.target.classList.add('active');
  document.getElementById('tab-' + e.target.dataset.tab).classList.add('active');
}});

function statusDotHTML(status, news) {{
  if (status === 'i' || status === 's' || status === 'u' || status === 'n') {{
    return `<span class="status-badge status-red" title="${{news || ''}}">&#9679;</span>`;
  }}
  if (status === 'd') {{
    return `<span class="status-badge status-amber" title="${{news || ''}}">&#9679;</span>`;
  }}
  return '';
}}

function pitchPhotoUrl(photoCode) {{
  return `${{PLAYER_PHOTO_BASE}}/110x140/${{photoCode}}.png`;
}}

function buildPitchHTML(xi, captainId, viceCaptainId) {{
  const byPos = {{1: [], 2: [], 3: [], 4: []}};
  xi.forEach(p => byPos[p.position].push(p));
  return [1, 2, 3, 4].map(pos => {{
    const cells = byPos[pos].map(p => {{
      const isCaptain = p.player_id === captainId;
      const isVice = p.player_id === viceCaptainId;
      return `
      <div class="pitch-player${{isCaptain ? ' is-captain' : isVice ? ' is-vice-captain' : ''}}">
        <img class="pitch-pic" src="${{pitchPhotoUrl(p.photo_code)}}" onerror="${{photoOnErrorAttr(p.photo_code)}}" alt="">
        ${{statusDotHTML(p.status)}}
        <span class="player-link" onclick="openProfile(${{p.player_id}})">${{p.name}}</span><br>
        <span class="pitch-score">${{p.score}}${{isCaptain ? ' (C)' : isVice ? ' (VC)' : ''}}</span>
      </div>`;
    }}).join('');
    return `<div class="pitch-row">${{cells}}</div>`;
  }}).join('');
}}

function buildBenchHTML(bench) {{
  return bench.map(p => `
    <div class="squad-row bench">
      <img class="player-pic" src="${{pitchPhotoUrl(p.photo_code)}}" onerror="${{photoOnErrorAttr(p.photo_code)}}" alt="">
      ${{statusDotHTML(p.status)}}
      <span class="player-link" onclick="openProfile(${{p.player_id}})">${{p.name}}</span>
      <span class="pos-tag">${{POS_NAMES[p.position] || '?'}}</span> &mdash; score ${{p.score}}
    </div>`).join('');
}}

function selectFormation(label) {{
  const f = allFormations[label];
  if (!f) return;

  const isBest = label === bestFormationLabel;
  const viceHTML = f.suggested_vice_captain
    ? ` &mdash; vice-captain <strong><span class="player-link" onclick="openProfile(${{f.suggested_vice_captain.player_id}})">${{f.suggested_vice_captain.name}}</span></strong> (steps up if the captain doesn't register a score)`
    : '';
  document.getElementById('formationSummary').innerHTML =
    `<strong>${{isBest ? 'Recommended' : 'Selected'}}: ${{f.formation}}</strong> &mdash; projected ${{f.projected_total}} pts, ` +
    `captain <strong><span class="player-link" onclick="openProfile(${{f.suggested_captain.player_id}})">${{f.suggested_captain.name}}</span></strong>${{viceHTML}}`;
  document.getElementById('formationPitch').innerHTML = buildPitchHTML(f.starting_xi, f.suggested_captain.player_id, f.suggested_vice_captain ? f.suggested_vice_captain.player_id : null);
  document.getElementById('formationBench').innerHTML = buildBenchHTML(f.bench);

  document.querySelectorAll('.formation-compare-row').forEach(el => {{
    el.classList.toggle('selected', el.dataset.formation === label);
  }});
}}

if (bestFormationLabel) selectFormation(bestFormationLabel);

const SQUAD_METRICS = {{
  points: {{ label: 'Pts', value: r => r.multiplier > 0 ? (r.gw_points || 0) * r.multiplier : (r.gw_points || 0) }},
  opponent: {{ label: 'Opp', value: r => r.opponent || '-' }},
  price: {{ label: 'Price', value: r => `£${{r.price}}m` }},
  selling_price: {{ label: 'Selling', value: r => r.selling_price != null ? `£${{r.selling_price}}m` : '-' }},
  difficulty: {{ label: 'FDR', value: r => r.difficulty != null ? r.difficulty : '-' }},
  form: {{ label: 'Form', value: r => r.form }},
  selected_by_percent: {{ label: 'Own%', value: r => `${{r.selected_by_percent}}%` }},
  price_change: {{ label: 'Price Δ', value: r => {{
    if (r.price_change == null) return '-';
    if (r.price_change > 0) return `+£${{r.price_change}}m`;
    if (r.price_change < 0) return `-£${{Math.abs(r.price_change)}}m`;
    return '£0.0m';
  }} }},
}};

const SQUAD_POS_ORDER = ['GK', 'DEF', 'MID', 'FWD'];

function squadPitchPlayerHTML(r, metric) {{
  const m = SQUAD_METRICS[metric];
  const tag = r.is_captain ? ' (C)' : r.is_vice_captain ? ' (V)' : '';
  const photoUrl = `${{PLAYER_PHOTO_BASE}}/110x140/${{r.photo_code}}.png`;
  return `
    <div class="pitch-player${{r.is_captain ? ' is-captain' : ''}}">
      <img class="pitch-pic" src="${{photoUrl}}" onerror="${{photoOnErrorAttr(r.photo_code)}}" alt="">
      ${{statusDotHTML(r.status, r.news)}}
      <span class="player-link" onclick="openProfile(${{r.player_id}})">${{r.name}}</span>${{tag}}<br>
      <span class="pitch-score">${{m.label}} ${{m.value(r)}}</span>
    </div>`;
}}

function renderSquadPitch(metric) {{
  const byPos = {{GK: [], DEF: [], MID: [], FWD: []}};
  squadData.forEach(r => {{ if (byPos[r.position]) byPos[r.position].push(r); }});
  document.getElementById('squadPitch').innerHTML = SQUAD_POS_ORDER.map(pos =>
    `<div class="pitch-row">${{byPos[pos].map(r => squadPitchPlayerHTML(r, metric)).join('')}}</div>`
  ).join('');
}}

const squadToggle = document.getElementById('squadToggle');
if (squadToggle) {{
  squadToggle.querySelectorAll('.toggle-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      squadToggle.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderSquadPitch(btn.dataset.metric);
    }});
  }});
}}

if (squadData.length) renderSquadPitch('points');

const CLAY = '#C1613C', GOOD = '#5B7B4F', BAD = '#B0402A';

new Chart(document.getElementById('moversChart'), {{
  type: 'bar',
  data: {{
    labels: movers.map(m => m.name),
    datasets: [{{
      label: 'Form change',
      data: movers.map(m => m.form_change),
      backgroundColor: movers.map(m => m.form_change >= 0 ? GOOD : BAD),
      borderRadius: 4,
    }}]
  }},
  options: {{
    indexAxis: 'y', plugins: {{ legend: {{ display: false }} }},
    onClick: (evt, elements) => {{ if (elements.length) openProfile(movers[elements[0].index].player_id); }}
  }}
}});

new Chart(document.getElementById('valueChart'), {{
  type: 'bar',
  data: {{
    labels: valuePicks.map(v => v.name),
    datasets: [{{
      label: 'Value score',
      data: valuePicks.map(v => v.value_score),
      backgroundColor: CLAY,
      borderRadius: 4,
    }}]
  }},
  options: {{
    indexAxis: 'y', plugins: {{ legend: {{ display: false }} }},
    onClick: (evt, elements) => {{ if (elements.length) openProfile(valuePicks[elements[0].index].player_id); }}
  }}
}});

const tbody = document.getElementById('tableBody');
function renderTable(rows) {{
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td><span class="player-link" onclick="openProfile(${{r.player_id}})">${{r.name}}</span></td>
      <td>${{r.position}}</td><td>£${{r.price}}m</td>
      <td>${{r.total_points}}</td><td>${{r.form}}</td>
      <td>${{r.points_per_game}}</td><td>${{r.selected_by_percent}}%</td>
    </tr>`).join('');
}}

const keys = ['name','position','price','total_points','form','points_per_game','selected_by_percent'];
let sortDir = {{}};
let sortKey = null;
let positionFilter = 'All';

function applyTableFilterAndSort() {{
  let rows = positionFilter === 'All' ? fullTable : fullTable.filter(r => r.position === positionFilter);
  if (sortKey) {{
    rows = [...rows].sort((a, b) =>
      sortDir[sortKey] ? (a[sortKey] > b[sortKey] ? 1 : -1) : (a[sortKey] < b[sortKey] ? 1 : -1)
    );
  }}
  renderTable(rows);
}}

function sortTable(colIndex) {{
  sortKey = keys[colIndex];
  sortDir[sortKey] = !sortDir[sortKey];
  applyTableFilterAndSort();
}}

document.getElementById('positionFilter').querySelectorAll('.toggle-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    positionFilter = btn.dataset.position;
    document.getElementById('positionFilter').querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    applyTableFilterAndSort();
  }});
}});

applyTableFilterAndSort();

function openProfile(playerId) {{
  const p = playerProfiles[playerId];
  if (!p) return;

  const photoUrl = `${{PLAYER_PHOTO_BASE}}/110x140/${{p.photo_code}}.png`;
  let gwRows = '';
  if (p.gw_history && p.gw_history.length) {{
    gwRows = `<strong class="block-label" style="font-size:0.85rem;">This Season</strong>
      <table class="modal-gw-table"><thead><tr><th>GW</th><th>Pts</th><th>Min</th><th>G</th><th>A</th></tr></thead><tbody>` +
      p.gw_history.map(h => `<tr><td>${{h.gameweek}}</td><td>${{h.total_points}}</td><td>${{h.minutes}}</td><td>${{h.goals_scored}}</td><td>${{h.assists}}</td></tr>`).join('') +
      `</tbody></table>`;
  }} else {{
    gwRows = `<p class="muted" style="font-size:0.85rem;">Gameweek-by-gameweek history not available yet &mdash; run the historical backfill to populate this.</p>`;
  }}

  let seasonRows = '';
  if (p.season_history && p.season_history.length) {{
    seasonRows = `<strong class="block-label" style="font-size:0.85rem;">Past Seasons</strong>
      <table class="modal-gw-table"><thead><tr><th>Season</th><th>Pts</th><th>Min</th><th>G</th><th>A</th></tr></thead><tbody>` +
      p.season_history.map(h => `<tr><td>${{h.season}}</td><td>${{h.total_points}}</td><td>${{h.minutes}}</td><td>${{h.goals_scored}}</td><td>${{h.assists}}</td></tr>`).join('') +
      `</tbody></table>`;
  }}

  let statusHtml = '';
  if (p.status && p.status !== 'a') {{
    const badgeColor = (p.status === 'd') ? '#BF8B32' : '#B0402A';
    statusHtml = `<div style="background:${{badgeColor}}1A;border:1px solid ${{badgeColor}};border-radius:8px;padding:8px;margin:10px 0;font-size:0.85rem;">
      <strong style="color:${{badgeColor}}">${{p.status_label}}</strong>${{p.chance_of_playing !== null && p.chance_of_playing !== undefined ? ` &mdash; ${{p.chance_of_playing}}% chance of playing` : ''}}
      ${{p.news ? `<br>${{p.news}}` : ''}}
    </div>`;
  }}

  document.getElementById('modalBody').innerHTML = `
    <div class="modal-header">
      <img src="${{photoUrl}}" onerror="${{photoOnErrorAttr(p.photo_code)}}" alt="">
      <div><strong>${{p.full_name || p.name}}</strong><br><span class="muted">${{p.team || ''}} &middot; ${{POS_NAMES[p.position] || '?'}}</span></div>
    </div>
    ${{statusHtml}}
    <div class="modal-stats">
      <div><span>Price</span><span>£${{p.price}}m</span></div>
      <div><span>Total points</span><span>${{p.total_points}}</span></div>
      <div><span>Form</span><span>${{p.form}}</span></div>
      <div><span>Points/game</span><span>${{p.points_per_game}}</span></div>
      <div><span>Minutes</span><span>${{p.minutes}}</span></div>
      <div><span>Selected by</span><span>${{p.selected_by_percent}}%</span></div>
    </div>
    ${{gwRows}}
    ${{seasonRows}}
  `;
  document.getElementById('profileModal').classList.add('open');
}}

function closeProfile() {{
  document.getElementById('profileModal').classList.remove('open');
}}

const countdownEl = document.getElementById('countdown');
if (countdownEl) {{
  const deadline = new Date(countdownEl.dataset.deadline).getTime();
  function updateCountdown() {{
    const diff = deadline - Date.now();
    if (diff <= 0) {{
      countdownEl.textContent = 'Deadline has passed';
      countdownEl.classList.add('urgent');
      return;
    }}
    const days = Math.floor(diff / 86400000);
    const hours = Math.floor((diff % 86400000) / 3600000);
    const mins = Math.floor((diff % 3600000) / 60000);
    const secs = Math.floor((diff % 60000) / 1000);
    countdownEl.textContent = days > 0
      ? `${{days}}d ${{hours}}h ${{mins}}m ${{secs}}s`
      : `${{hours}}h ${{mins}}m ${{secs}}s`;
    countdownEl.classList.toggle('urgent', diff < 3 * 3600000);
  }}
  updateCountdown();
  setInterval(updateCountdown, 1000);
}}
</script>
</body>
</html>"""


def main():
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(build_html())
    print(f"Dashboard written -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
