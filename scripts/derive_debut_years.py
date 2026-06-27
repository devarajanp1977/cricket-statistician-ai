"""Derive player_profiles.debut_year from match data — no external API calls.

debut_year = earliest year a player appears in either source:
  - Cricsheet ball-by-ball (batter / bowler / non-striker), 2001-present
  - Kaggle Test scorecards (batting + bowling cards), 1877-present

Kaggle supplies true historical Test debuts (e.g. Tendulkar 1989, Akram 1985);
Cricsheet covers the modern limited-overs era. Idempotent and safe to re-run
after any data reload, which is why it is wired into the refresh pipelines.

Run standalone:  python scripts/derive_debut_years.py
"""

import os

import duckdb

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "db", "cricket.duckdb")

_DERIVE_SQL = """
CREATE OR REPLACE TEMP TABLE _debut AS
WITH cs AS (
    SELECT nm, MIN(yr) AS yr FROM (
        SELECT batter AS nm, EXTRACT(year FROM m.date_start) AS yr
            FROM deliveries d JOIN matches m ON d.match_id = m.match_id
        UNION ALL
        SELECT bowler, EXTRACT(year FROM m.date_start)
            FROM deliveries d JOIN matches m ON d.match_id = m.match_id
        UNION ALL
        SELECT non_striker, EXTRACT(year FROM m.date_start)
            FROM deliveries d JOIN matches m ON d.match_id = m.match_id
    ) GROUP BY nm
),
csid AS (
    SELECT p.cricsheet_id AS cid, MIN(cs.yr) AS yr
    FROM players p JOIN cs ON cs.nm = p.name GROUP BY p.cricsheet_id
),
kg AS (
    SELECT cid, MIN(yr) AS yr FROM (
        SELECT pm.cricsheet_id AS cid, EXTRACT(year FROM km."Match Start Date") AS yr
            FROM kaggle_batting kb JOIN kaggle_matches km ON kb."Match ID" = km."Match ID"
            JOIN player_map pm ON pm.kaggle_player_id = kb.batsman
            WHERE km."Match Start Date" IS NOT NULL AND pm.cricsheet_id IS NOT NULL
        UNION ALL
        SELECT pm.cricsheet_id, EXTRACT(year FROM km."Match Start Date")
            FROM kaggle_bowling kbo JOIN kaggle_matches km ON kbo."Match ID" = km."Match ID"
            JOIN player_map pm ON pm.kaggle_player_id = kbo."bowler id"
            WHERE km."Match Start Date" IS NOT NULL AND pm.cricsheet_id IS NOT NULL
    ) GROUP BY cid
)
SELECT pp.cricsheet_id AS cid,
       LEAST(COALESCE(csid.yr, 9999), COALESCE(kg.yr, 9999)) AS dy
FROM player_profiles pp
LEFT JOIN csid ON csid.cid = pp.cricsheet_id
LEFT JOIN kg ON kg.cid = pp.cricsheet_id;
"""


def derive_debut_years(db_path: str = DB_PATH) -> dict:
    """Recompute debut_year for every profile from match data. Idempotent."""
    con = duckdb.connect(db_path)
    try:
        con.execute(_DERIVE_SQL)
        con.execute(
            "UPDATE player_profiles SET debut_year = CAST(d.dy AS INTEGER) "
            "FROM _debut d WHERE d.cid = player_profiles.cricsheet_id AND d.dy < 9999"
        )
        filled, total = con.execute(
            "SELECT COUNT(*) FILTER (WHERE debut_year IS NOT NULL), COUNT(*) "
            "FROM player_profiles"
        ).fetchone()
    finally:
        con.close()
    pct = round(100 * filled / total, 1) if total else 0.0
    print(f"[debut] derived debut_year for {filled}/{total} profiles ({pct}%)")
    return {"filled": filled, "total": total, "pct": pct}


if __name__ == "__main__":
    derive_debut_years()
