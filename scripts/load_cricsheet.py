"""Load Cricsheet JSON match data into DuckDB.

Normalizes the nested ball-by-ball JSON into relational tables:
  - matches       (one row per match)
  - innings       (one row per innings)
  - deliveries    (one row per ball)
  - wickets       (one row per dismissal)
  - players       (registry: people.csv)

Uses CSV intermediary files for fast bulk loading.
"""

import csv
import json
import os
import glob
import sys
import duckdb

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(BASE_DIR, "data", "raw", "cricsheet")
JSON_DIR = os.path.join(RAW_DIR, "all_json")
DB_PATH = os.path.join(BASE_DIR, "data", "db", "cricket.duckdb")

DDL_FAST = """
DROP TABLE IF EXISTS wickets;
DROP TABLE IF EXISTS deliveries;
DROP TABLE IF EXISTS innings;
DROP TABLE IF EXISTS matches;
DROP TABLE IF EXISTS players;

CREATE TABLE matches (
    match_id        VARCHAR,
    match_type      VARCHAR,
    match_type_number INTEGER,
    gender          VARCHAR,
    season          VARCHAR,
    date_start      DATE,
    date_end        DATE,
    venue           VARCHAR,
    city            VARCHAR,
    event_name      VARCHAR,
    event_match_number INTEGER,
    event_group     VARCHAR,
    event_stage     VARCHAR,
    team1           VARCHAR,
    team2           VARCHAR,
    toss_winner     VARCHAR,
    toss_decision   VARCHAR,
    outcome_winner  VARCHAR,
    outcome_result  VARCHAR,
    outcome_by_runs     INTEGER,
    outcome_by_wickets  INTEGER,
    outcome_by_innings  INTEGER,
    outcome_method  VARCHAR,
    player_of_match VARCHAR,
    team_type       VARCHAR,
    overs           INTEGER,
    balls_per_over  INTEGER
);

CREATE TABLE innings (
    match_id        VARCHAR,
    innings_num     INTEGER,
    batting_team    VARCHAR,
    declared        BOOLEAN DEFAULT FALSE,
    forfeited       BOOLEAN DEFAULT FALSE,
    super_over      BOOLEAN DEFAULT FALSE,
    target_runs     INTEGER,
    target_overs    INTEGER
);

CREATE TABLE deliveries (
    match_id        VARCHAR,
    innings_num     INTEGER,
    over_num        INTEGER,
    ball_num        INTEGER,
    batter          VARCHAR,
    bowler          VARCHAR,
    non_striker     VARCHAR,
    runs_batter     INTEGER,
    runs_extras     INTEGER,
    runs_total      INTEGER,
    non_boundary    BOOLEAN DEFAULT FALSE,
    extras_wides    INTEGER DEFAULT 0,
    extras_noballs  INTEGER DEFAULT 0,
    extras_byes     INTEGER DEFAULT 0,
    extras_legbyes  INTEGER DEFAULT 0,
    extras_penalty  INTEGER DEFAULT 0
);

CREATE TABLE wickets (
    id              INTEGER,
    match_id        VARCHAR,
    innings_num     INTEGER,
    over_num        INTEGER,
    ball_num        INTEGER,
    player_out      VARCHAR,
    kind            VARCHAR,
    fielder1        VARCHAR,
    fielder2        VARCHAR
);

CREATE TABLE players (
    cricsheet_id    VARCHAR,
    name            VARCHAR,
    unique_name     VARCHAR,
    key_cricinfo    VARCHAR,
    key_cricketarchive VARCHAR,
    key_bcci        VARCHAR,
    key_pulse       VARCHAR
);
"""

POST_LOAD_DDL = """
CREATE UNIQUE INDEX idx_matches_pk ON matches(match_id);
CREATE INDEX idx_deliveries_match ON deliveries(match_id, innings_num);
CREATE INDEX idx_deliveries_batter ON deliveries(batter);
CREATE INDEX idx_deliveries_bowler ON deliveries(bowler);
CREATE INDEX idx_wickets_match ON wickets(match_id, innings_num);
CREATE INDEX idx_innings_match ON innings(match_id);
"""


def _parse_match(filepath: str):
    """Parse a single Cricsheet JSON file and yield table rows."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    match_id = os.path.splitext(os.path.basename(filepath))[0]
    info = data.get("info", {})

    dates = info.get("dates", [])
    event = info.get("event", {})
    outcome = info.get("outcome", {})
    outcome_by = outcome.get("by", {})
    toss = info.get("toss", {})
    teams = info.get("teams", [])
    pom = info.get("player_of_match", [])

    match_row = {
        "match_id": match_id,
        "match_type": info.get("match_type"),
        "match_type_number": info.get("match_type_number"),
        "gender": info.get("gender"),
        "season": info.get("season"),
        "date_start": dates[0] if dates else None,
        "date_end": dates[-1] if dates else None,
        "venue": info.get("venue"),
        "city": info.get("city"),
        "event_name": event.get("name"),
        "event_match_number": event.get("match_number"),
        "event_group": str(event["group"]) if "group" in event else None,
        "event_stage": event.get("stage"),
        "team1": teams[0] if len(teams) > 0 else None,
        "team2": teams[1] if len(teams) > 1 else None,
        "toss_winner": toss.get("winner"),
        "toss_decision": toss.get("decision"),
        "outcome_winner": outcome.get("winner"),
        "outcome_result": outcome.get("result"),
        "outcome_by_runs": outcome_by.get("runs"),
        "outcome_by_wickets": outcome_by.get("wickets"),
        "outcome_by_innings": outcome_by.get("innings"),
        "outcome_method": outcome.get("method"),
        "player_of_match": pom[0] if pom else None,
        "team_type": info.get("team_type"),
        "overs": info.get("overs"),
        "balls_per_over": info.get("balls_per_over"),
    }

    innings_rows = []
    delivery_rows = []
    wicket_rows = []
    wicket_id = 0

    for inn_idx, inn in enumerate(data.get("innings", []), start=1):
        target = inn.get("target", {})
        innings_rows.append({
            "match_id": match_id,
            "innings_num": inn_idx,
            "batting_team": inn.get("team"),
            "declared": inn.get("declared", False),
            "forfeited": inn.get("forfeited", False),
            "super_over": inn.get("super_over", False),
            "target_runs": target.get("runs"),
            "target_overs": target.get("overs"),
        })

        for over_data in inn.get("overs", []):
            over_num = over_data["over"]
            for ball_idx, delivery in enumerate(over_data["deliveries"], start=1):
                runs = delivery.get("runs", {})
                extras = delivery.get("extras", {})

                delivery_rows.append({
                    "match_id": match_id,
                    "innings_num": inn_idx,
                    "over_num": over_num,
                    "ball_num": ball_idx,
                    "batter": delivery.get("batter"),
                    "bowler": delivery.get("bowler"),
                    "non_striker": delivery.get("non_striker"),
                    "runs_batter": runs.get("batter", 0),
                    "runs_extras": runs.get("extras", 0),
                    "runs_total": runs.get("total", 0),
                    "non_boundary": runs.get("non_boundary", False),
                    "extras_wides": extras.get("wides", 0),
                    "extras_noballs": extras.get("noballs", 0),
                    "extras_byes": extras.get("byes", 0),
                    "extras_legbyes": extras.get("legbyes", 0),
                    "extras_penalty": extras.get("penalty", 0),
                })

                for w in delivery.get("wickets", []):
                    fielders = w.get("fielders", [])
                    wicket_id += 1
                    wicket_rows.append({
                        "id": wicket_id,
                        "match_id": match_id,
                        "innings_num": inn_idx,
                        "over_num": over_num,
                        "ball_num": ball_idx,
                        "player_out": w.get("player_out"),
                        "kind": w.get("kind"),
                        "fielder1": fielders[0].get("name") if len(fielders) > 0 else None,
                        "fielder2": fielders[1].get("name") if len(fielders) > 1 else None,
                    })

    return match_row, innings_rows, delivery_rows, wicket_rows


def _load_register(con: duckdb.DuckDBPyConnection):
    """Load people.csv from the Cricsheet register."""
    people_csv = os.path.join(RAW_DIR, "people.csv")
    if not os.path.exists(people_csv):
        print("[loader] people.csv not found, skipping register load.")
        return

    con.execute("DELETE FROM players")
    con.execute(f"""
        INSERT INTO players
        SELECT
            identifier AS cricsheet_id,
            name,
            unique_name,
            key_cricinfo,
            key_cricketarchive,
            key_bcci,
            key_pulse
        FROM read_csv_auto('{people_csv.replace(os.sep, '/')}', header=true)
    """)
    count = con.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    print(f"[loader] Loaded {count:,} players from register.")


def load_cricsheet(force: bool = False):
    """Parse all Cricsheet JSON files and load into DuckDB via CSV bulk load."""
    if not os.path.isdir(JSON_DIR):
        print(f"[loader] JSON dir not found: {JSON_DIR}")
        print("[loader] Run: python scripts/download_data.py --source cricsheet")
        return

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    tmp_dir = os.path.join(os.path.dirname(DB_PATH), "tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    json_files = sorted(glob.glob(os.path.join(JSON_DIR, "*.json")))
    print(f"[loader] Found {len(json_files):,} JSON files. Parsing to CSV ...")

    # CSV column definitions
    match_cols = [
        "match_id", "match_type", "match_type_number", "gender", "season",
        "date_start", "date_end", "venue", "city", "event_name",
        "event_match_number", "event_group", "event_stage", "team1", "team2",
        "toss_winner", "toss_decision", "outcome_winner", "outcome_result",
        "outcome_by_runs", "outcome_by_wickets", "outcome_by_innings",
        "outcome_method", "player_of_match", "team_type", "overs", "balls_per_over",
    ]
    innings_cols = [
        "match_id", "innings_num", "batting_team", "declared", "forfeited",
        "super_over", "target_runs", "target_overs",
    ]
    delivery_cols = [
        "match_id", "innings_num", "over_num", "ball_num", "batter", "bowler",
        "non_striker", "runs_batter", "runs_extras", "runs_total", "non_boundary",
        "extras_wides", "extras_noballs", "extras_byes", "extras_legbyes", "extras_penalty",
    ]
    wicket_cols = [
        "id", "match_id", "innings_num", "over_num", "ball_num",
        "player_out", "kind", "fielder1", "fielder2",
    ]

    # Open CSV writers
    matches_csv = os.path.join(tmp_dir, "matches.csv")
    innings_csv = os.path.join(tmp_dir, "innings.csv")
    deliveries_csv = os.path.join(tmp_dir, "deliveries.csv")
    wickets_csv = os.path.join(tmp_dir, "wickets.csv")

    f_m = open(matches_csv, "w", newline="", encoding="utf-8")
    f_i = open(innings_csv, "w", newline="", encoding="utf-8")
    f_d = open(deliveries_csv, "w", newline="", encoding="utf-8")
    f_w = open(wickets_csv, "w", newline="", encoding="utf-8")

    w_m = csv.DictWriter(f_m, fieldnames=match_cols); w_m.writeheader()
    w_i = csv.DictWriter(f_i, fieldnames=innings_cols); w_i.writeheader()
    w_d = csv.DictWriter(f_d, fieldnames=delivery_cols); w_d.writeheader()
    w_w = csv.DictWriter(f_w, fieldnames=wicket_cols); w_w.writeheader()

    def _clean(d):
        return {k: ('' if v is None else v) for k, v in d.items()}

    errors = 0
    for i, filepath in enumerate(json_files, 1):
        try:
            m, inns, dels, wkts = _parse_match(filepath)
            w_m.writerow(_clean(m))
            for r in inns: w_i.writerow(_clean(r))
            for r in dels: w_d.writerow(_clean(r))
            for r in wkts: w_w.writerow(_clean(r))
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"[loader] WARN: skipping {os.path.basename(filepath)}: {e}")

        if i % 5000 == 0:
            print(f"[loader] Parsed {i:,}/{len(json_files):,} files ...")
            sys.stdout.flush()

    f_m.close(); f_i.close(); f_d.close(); f_w.close()
    print(f"[loader] Parsed all {len(json_files):,} files ({errors} errors). Loading into DuckDB ...")
    sys.stdout.flush()

    # Create DB and bulk load from CSVs
    con = duckdb.connect(DB_PATH)
    for stmt in DDL_FAST.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            con.execute(stmt)

    def csv_path(p):
        return p.replace(os.sep, "/")

    copy_opts = "HEADER, DELIMITER ',', NULL ''"
    con.execute(f"COPY matches FROM '{csv_path(matches_csv)}' ({copy_opts})")
    print(f"[loader] matches: {con.execute('SELECT COUNT(*) FROM matches').fetchone()[0]:,} rows")

    con.execute(f"COPY innings FROM '{csv_path(innings_csv)}' ({copy_opts})")
    print(f"[loader] innings: {con.execute('SELECT COUNT(*) FROM innings').fetchone()[0]:,} rows")

    con.execute(f"COPY deliveries FROM '{csv_path(deliveries_csv)}' ({copy_opts})")
    print(f"[loader] deliveries: {con.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0]:,} rows")

    con.execute(f"COPY wickets FROM '{csv_path(wickets_csv)}' ({copy_opts})")
    print(f"[loader] wickets: {con.execute('SELECT COUNT(*) FROM wickets').fetchone()[0]:,} rows")

    # Create indexes
    print("[loader] Creating indexes ...")
    for stmt in POST_LOAD_DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            con.execute(stmt)

    _load_register(con)

    # Print summary
    for table in ("matches", "innings", "deliveries", "wickets", "players"):
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"[loader] {table}: {count:,} rows")

    # Cleanup temp CSVs
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("[loader] Cleaned up temp CSV files.")

    con.close()
    print(f"[loader] Database saved to {DB_PATH}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Load Cricsheet data into DuckDB")
    parser.add_argument("--force", action="store_true", help="Drop and reload all data")
    args = parser.parse_args()
    load_cricsheet(force=args.force)
