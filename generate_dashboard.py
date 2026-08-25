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

from db import get_connection, get_movers, get_top_value

OUTPUT_PATH = Path(__file__).parent / "docs" / "index.html"
POSITION_NAMES = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def get_latest_full_table() -> list[dict]:
    conn = get_connection()
    conn.row_factory = None
    latest_date = conn.execute("SELECT MAX(snapshot_date) FROM player_snapshots").fetchone()[0]
    rows = conn.execute(
        """SELECT name, team, position, price, total_points, form,
                  points_per_game, selected_by_percent
           FROM player_snapshots WHERE snapshot_date = ?
           ORDER BY total_points DESC""",
        (latest_date,),
    ).fetchall()
    conn.close()
    cols = ["name", "team", "position", "price", "total_points", "form", "points_per_game", "selected_by_percent"]
    return [dict(zip(cols, r)) for r in rows], latest_date


def build_html() -> str:
    movers = get_movers(limit=10)
    value_picks = get_top_value(limit=10)
    full_table, latest_date = get_latest_full_table()

    for row in full_table:
        row["position"] = POSITION_NAMES.get(row["position"], "?")

    movers_json = json.dumps(movers)
    value_json = json.dumps(value_picks)
    table_json = json.dumps(full_table)

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
</style>
</head>
<body>
  <h1>⚽ FPL Agent Dashboard</h1>
  <div class="updated">Latest data: {latest_date}</div>

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

<script>
const movers = {movers_json};
const valuePicks = {value_json};
const fullTable = {table_json};

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
  options: {{ indexAxis: 'y', plugins: {{ legend: {{ display: false }} }} }}
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
  options: {{ indexAxis: 'y', plugins: {{ legend: {{ display: false }} }} }}
}});

const tbody = document.getElementById('tableBody');
function renderTable(rows) {{
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td>${{r.name}}</td><td>${{r.position}}</td><td>£${{r.price}}m</td>
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
</script>
</body>
</html>"""


def main():
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(build_html())
    print(f"Dashboard written -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
