"""
generate_dashboard.py
----------------------
Builds a single, self-contained dashboard.html - no server, no
build step, just a file you open (or that GitHub Pages hosts for
you). Charts are drawn with Chart.js, loaded from a CDN, so the
whole page is lightweight.

Three panels:
    1. Form risers/fallers this week (bar chart)
    2. Top value picks (points per game, per £1m) (bar chart)
    3. A plain table of the latest snapshot, sortable by clicking
       column headers (no framework needed - a few lines of JS)

Run this after main.py in the workflow; it reads straight from
data/fpl.db.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from db import get_connection, get_movers, get_top_value, get_latest_squad, get_captain_suggestions, get_chip_suggestions, get_transfer_suggestions, get_all_player_profiles, get_optimal_formation
from config import TEAM_ID

OUTPUT_PATH = Path(__file__).parent / "docs" / "index.html"
POSITION_NAMES = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


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
    """
    Wraps a player's name in a clickable span that opens the shared
    profile popup. Used everywhere a name appears server-side (the
    client-side table renderer and chart click handlers do the
    equivalent in JS - see the <script> block in build_html).
    """
    return f'<span class="player-link" onclick="openProfile({player_id})">{name}</span>'


def render_formation_card(formation: dict) -> str:
    """
    Renders the comparison table server-side (so it's visible even
    if JS fails to load) but leaves the pitch/bench/summary as empty
    placeholders - selectFormation() in the <script> block below
    fills those in from the embedded per-formation JSON, and swaps
    them again whenever a comparison row is clicked. Keeps the actual
    pitch-building logic in exactly one place (JS) instead of
    duplicating it between Python and JS.
    """
    if not formation.get("formation"):
        return f'<p>{formation.get("reason", "Not enough data yet.")}</p>'

    def comparison_row(c: dict) -> str:
        is_best = c["formation"] == formation["formation"]
        best_tag = " <span class='best-tag'>Recommended</span>" if is_best else ""
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
    <strong style="display:block;margin-top:12px;">Bench</strong>
    <div id="formationBench"></div>
    '''


def render_transfer_suggestion(t: dict) -> str:
    urgency_colors = {"High": "#f85149", "Medium": "#d29922", "Low": "#8b949e"}
    color = urgency_colors.get(t["urgency"], "#8b949e")
    reasons = []
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
      <span class="urgency-badge" style="background:{color}">{t["urgency"]}</span>
      <strong>{player_link(t["player_id"], t["name"])}</strong> ({POSITION_NAMES.get(t["position"], "?")}) &mdash; {reason_text}
      <div class="budget-line">Budget if sold: £{t["budget_available"]}m</div>
      {candidates_html}
    </div>'''


def render_squad_row(r: dict, is_bench: bool = False) -> str:
    """Builds one squad-row div. A plain function instead of a giant
    inline f-string, so quote-escaping doesn't turn into a mess."""
    photo_url = f"https://resources.premierleague.com/premierleague/photos/players/110x140/p{r['photo_code']}.png"
    tag = " (C)" if r.get("is_captain") else " (V)" if r.get("is_vice_captain") else ""
    points = (r["gw_points"] or 0) * r["multiplier"] if not is_bench else (r["gw_points"] or 0)
    bench_class = " bench" if is_bench else ""
    return (
        f'<div class="squad-row{bench_class}">'
        f'<img class="player-pic" src="{photo_url}" onerror="this.style.display=\'none\'" alt="">'
        f'{player_link(r["player_id"], r["name"])} <span class="pos-tag">{r["position"]}</span>{tag} &mdash; {points} pts'
        f'</div>'
    )


def build_html() -> str:
    movers = get_movers(limit=10)
    value_picks = get_top_value(limit=10)
    full_table, latest_date = get_latest_full_table()
    squad = get_latest_squad(TEAM_ID)
    captain_picks = get_captain_suggestions(TEAM_ID, limit=5)
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

    formations_by_label = {c["formation"]: c for c in formation.get("all_formations", [])}

    movers_json = json.dumps(movers)
    value_json = json.dumps(value_picks)
    table_json = json.dumps(full_table)
    profiles_json = json.dumps(profiles)
    formations_json = json.dumps(formations_by_label)
    best_formation_json = json.dumps(formation.get("formation"))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FPL Agent Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 0; padding: 16px; background: #0d1117; color: #e6edf3; }}
  h1 {{ font-size: 1.4rem; }}
  .updated {{ color: #8b949e; font-size: 0.85rem; margin-bottom: 24px; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin-bottom: 20px; }}
  canvas {{ max-height: 320px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #30363d; }}
  th {{ cursor: pointer; color: #58a6ff; position: sticky; top: 0; background: #161b22; }}
  .scroll {{ max-height: 400px; overflow-y: auto; }}
  .squad-row {{ padding: 4px 0; border-bottom: 1px solid #21262d; font-size: 0.9rem; display: flex; align-items: center; gap: 8px; }}
  .player-pic {{ width: 32px; height: 40px; object-fit: cover; border-radius: 4px; background: #21262d; flex-shrink: 0; }}
  .squad-row.bench {{ color: #8b949e; }}
  .pos-tag {{ color: #58a6ff; font-size: 0.75rem; }}
  .squad-list strong {{ display: block; margin: 10px 0 4px; }}
  .subtitle {{ color: #8b949e; font-size: 0.75rem; font-weight: normal; }}
  .transfer-card {{ border: 1px solid #30363d; border-radius: 6px; padding: 10px; margin-bottom: 10px; font-size: 0.9rem; }}
  .urgency-badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.7rem; font-weight: bold; color: #0d1117; margin-right: 6px; }}
  .budget-line {{ color: #8b949e; font-size: 0.8rem; margin: 4px 0; }}
  .candidate-row {{ font-size: 0.85rem; color: #58a6ff; padding-left: 6px; }}
  .player-link {{ color: #e6edf3; text-decoration: underline dotted #58a6ff; cursor: pointer; }}
  .player-link:hover {{ color: #58a6ff; }}
  #profileModal {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 100;
                    align-items: center; justify-content: center; padding: 16px; }}
  #profileModal.open {{ display: flex; }}
  .modal-content {{ background: #161b22; border: 1px solid #30363d; border-radius: 10px;
                     padding: 20px; max-width: 400px; width: 100%; max-height: 80vh; overflow-y: auto; }}
  .modal-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }}
  .modal-header img {{ width: 56px; height: 70px; object-fit: cover; border-radius: 6px; background: #21262d; }}
  .modal-close {{ float: right; cursor: pointer; color: #8b949e; font-size: 1.2rem; }}
  .modal-stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; font-size: 0.85rem; margin: 12px 0; }}
  .modal-stats div {{ display: flex; justify-content: space-between; border-bottom: 1px solid #21262d; padding: 3px 0; }}
  .modal-gw-table {{ width: 100%; font-size: 0.8rem; margin-top: 10px; }}
  .modal-gw-table th, .modal-gw-table td {{ padding: 3px 6px; text-align: center; border-bottom: 1px solid #21262d; }}
  .pitch {{ background: #1a3d1a; border-radius: 8px; padding: 12px 4px; margin: 12px 0; }}
  .pitch-row {{ display: flex; justify-content: space-around; flex-wrap: wrap; margin: 8px 0; }}
  .pitch-player {{ background: #21262d; border-radius: 6px; padding: 6px 8px; text-align: center; font-size: 0.75rem; min-width: 64px; }}
  .pitch-player.is-captain {{ border: 1px solid #d29922; }}
  .pitch-pic {{ width: 40px; height: 50px; object-fit: cover; border-radius: 4px; background: #30363d; display: block; margin: 0 auto 4px; }}
  .pitch-score {{ color: #8b949e; font-size: 0.7rem; }}
  .formation-compare {{ margin-top: 12px; font-size: 0.8rem; }}
  .formation-compare-row {{ padding: 5px 8px; color: #8b949e; cursor: pointer; border-radius: 4px; border: 1px solid transparent; }}
  .formation-compare-row:hover {{ background: #21262d; }}
  .formation-compare-row.best {{ color: #3fb950; font-weight: bold; }}
  .formation-compare-row.selected {{ border-color: #58a6ff; background: #21262d; }}
  .best-tag {{ font-size: 0.65rem; font-weight: normal; color: #0d1117; background: #3fb950; border-radius: 8px; padding: 1px 6px; margin-left: 4px; }}
</style>
</head>
<body>
  <h1>⚽ FPL Agent Dashboard</h1>
  <div class="updated">Latest data: {latest_date}</div>

  <div class="card">
    <h2>Best Formation for Upcoming Fixtures</h2>
    {render_formation_card(formation)}
  </div>

  <div class="card">
    <h2>My Squad {"" if not starters else f"&mdash; {squad_total} pts this gameweek"}</h2>
    {"<p>No squad data yet - runs after the current gameweek's picks are published.</p>" if not squad else
     "<div class='squad-list'><strong>Starting XI</strong>" +
     "".join(render_squad_row(r) for r in starters) +
     "<strong>Bench</strong>" +
     "".join(render_squad_row(r, is_bench=True) for r in bench) +
     "</div>"}
  </div>

  <div class="card">
    <h2>Captain Suggestions <span class="subtitle">(form &times; fixture favourability)</span></h2>
    {"<p>Not enough fixture data yet.</p>" if not captain_picks else "".join(
        f'<div class="squad-row">{i+1}. {player_link(c["player_id"], c["name"])} vs {c["opponent"] or "?"} '
        f'({"H" if c["is_home"] else "A" if c["is_home"] is not None else "?"}, FDR {c["difficulty"] or "?"}) '
        f'&mdash; form {c["form"]}, score <strong>{c["score"]}</strong></div>'
        for i, c in enumerate(captain_picks)
    )}
  </div>

  <div class="card">
    <h2>Chip Timing <span class="subtitle">(double/blank gameweek detection)</span></h2>
    <div class="squad-row">🎯 <strong>Triple Captain / Bench Boost:</strong> {chip_advice['triple_captain_bench_boost']}</div>
    <div class="squad-row">🃏 <strong>Free Hit:</strong> {chip_advice['free_hit']}</div>
    <div class="squad-row bench">Wildcard timing isn't scored here - that call depends on broader squad health and fixture swings, better discussed directly.</div>
  </div>

  <div class="card">
    <h2>Transfer Suggestions <span class="subtitle">(form + price trend, budget-aware)</span></h2>
    {"<p>No players flagged - either everything's stable, or only one snapshot exists so far (trends need two).</p>" if not transfer_suggestions else "".join(render_transfer_suggestion(t) for t in transfer_suggestions)}
  </div>

  <div class="card">
    <h2>Movers &amp; Shakers (form change since last week)</h2>
    <canvas id="moversChart"></canvas>
  </div>

  <div class="card">
    <h2>Top Value Picks (points/game per £1m)</h2>
    <canvas id="valueChart"></canvas>
  </div>

  <div class="card">
    <h2>Full Table</h2>
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
const allFormations = {formations_json};
const bestFormationLabel = {best_formation_json};

new Chart(document.getElementById('moversChart'), {{
  type: 'bar',
  data: {{
    labels: movers.map(m => m.name),
    datasets: [{{
      label: 'Form change',
      data: movers.map(m => m.form_change),
      backgroundColor: movers.map(m => m.form_change >= 0 ? '#3fb950' : '#f85149'),
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
      backgroundColor: '#58a6ff',
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
renderTable(fullTable);

const keys = ['name','position','price','total_points','form','points_per_game','selected_by_percent'];
let sortDir = {{}};
function sortTable(colIndex) {{
  const key = keys[colIndex];
  sortDir[key] = !sortDir[key];
  const sorted = [...fullTable].sort((a, b) =>
    sortDir[key] ? (a[key] > b[key] ? 1 : -1) : (a[key] < b[key] ? 1 : -1)
  );
  renderTable(sorted);
}}

const POS_NAMES = {{1: 'GK', 2: 'DEF', 3: 'MID', 4: 'FWD'}};

function openProfile(playerId) {{
  const p = playerProfiles[playerId];
  if (!p) return;  // no profile data for this id - fail quietly rather than break the page

  const photoUrl = `https://resources.premierleague.com/premierleague/photos/players/250x250/p${{p.photo_code}}.png`;
  let gwRows = '';
  if (p.gw_history && p.gw_history.length) {{
    gwRows = `<table class="modal-gw-table"><thead><tr><th>GW</th><th>Pts</th><th>Min</th><th>G</th><th>A</th></tr></thead><tbody>` +
      p.gw_history.map(h => `<tr><td>${{h.gameweek}}</td><td>${{h.total_points}}</td><td>${{h.minutes}}</td><td>${{h.goals_scored}}</td><td>${{h.assists}}</td></tr>`).join('') +
      `</tbody></table>`;
  }} else {{
    gwRows = `<p style="color:#8b949e;font-size:0.85rem;">Gameweek-by-gameweek history not available yet - run the historical backfill to populate this.</p>`;
  }}

  document.getElementById('modalBody').innerHTML = `
    <div class="modal-header">
      <img src="${{photoUrl}}" onerror="this.style.display='none'" alt="">
      <div><strong>${{p.name}}</strong><br><span style="color:#8b949e">${{p.team || ''}} &middot; ${{POS_NAMES[p.position] || '?'}}</span></div>
    </div>
    <div class="modal-stats">
      <div><span>Price</span><span>£${{p.price}}m</span></div>
      <div><span>Total points</span><span>${{p.total_points}}</span></div>
      <div><span>Form</span><span>${{p.form}}</span></div>
      <div><span>Points/game</span><span>${{p.points_per_game}}</span></div>
      <div><span>Minutes</span><span>${{p.minutes}}</span></div>
      <div><span>Selected by</span><span>${{p.selected_by_percent}}%</span></div>
    </div>
    ${{gwRows}}
  `;
  document.getElementById('profileModal').classList.add('open');
}}

function closeProfile() {{
  document.getElementById('profileModal').classList.remove('open');
}}

function pitchPhotoUrl(photoCode) {{
  return `https://resources.premierleague.com/premierleague/photos/players/110x140/p${{photoCode}}.png`;
}}

function buildPitchHTML(xi, captainId) {{
  const byPos = {{1: [], 2: [], 3: [], 4: []}};
  xi.forEach(p => byPos[p.position].push(p));
  return [1, 2, 3, 4].map(pos => {{
    const cells = byPos[pos].map(p => `
      <div class="pitch-player${{p.player_id === captainId ? ' is-captain' : ''}}">
        <img class="pitch-pic" src="${{pitchPhotoUrl(p.photo_code)}}" onerror="this.style.display='none'" alt="">
        <span class="player-link" onclick="openProfile(${{p.player_id}})">${{p.name}}</span><br>
        <span class="pitch-score">${{p.score}}${{p.player_id === captainId ? ' (C)' : ''}}</span>
      </div>`).join('');
    return `<div class="pitch-row">${{cells}}</div>`;
  }}).join('');
}}

function buildBenchHTML(bench) {{
  return bench.map(p => `
    <div class="squad-row bench">
      <img class="player-pic" src="${{pitchPhotoUrl(p.photo_code)}}" onerror="this.style.display='none'" alt="">
      <span class="player-link" onclick="openProfile(${{p.player_id}})">${{p.name}}</span>
      <span class="pos-tag">${{POS_NAMES[p.position] || '?'}}</span> &mdash; score ${{p.score}}
    </div>`).join('');
}}

function selectFormation(label) {{
  const f = allFormations[label];
  if (!f) return;

  const isBest = label === bestFormationLabel;
  document.getElementById('formationSummary').innerHTML =
    `<strong>${{isBest ? 'Recommended' : 'Selected'}}: ${{f.formation}}</strong> &mdash; projected ${{f.projected_total}} pts, ` +
    `captain <strong><span class="player-link" onclick="openProfile(${{f.suggested_captain.player_id}})">${{f.suggested_captain.name}}</span></strong>`;
  document.getElementById('formationPitch').innerHTML = buildPitchHTML(f.starting_xi, f.suggested_captain.player_id);
  document.getElementById('formationBench').innerHTML = buildBenchHTML(f.bench);

  document.querySelectorAll('.formation-compare-row').forEach(el => {{
    el.classList.toggle('selected', el.dataset.formation === label);
  }});
}}

if (bestFormationLabel) selectFormation(bestFormationLabel);
</script>
</body>
</html>"""


def main():
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(build_html())
    print(f"Dashboard written -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
