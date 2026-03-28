"""Backfill Test match scorecard data from Cricsheet into Kaggle-format tables.

This script:
1. Builds a unified player_map table (cricsheet_id <-> kaggle player_id <-> name)
2. Finds Test matches in Cricsheet that are missing from Kaggle tables
3. Computes scorecard-level batting/bowling/fow data from Cricsheet deliveries
4. Inserts them into kaggle_batting, kaggle_bowling, kaggle_fow, kaggle_matches, kaggle_partnerships
"""

import os
import duckdb

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "db", "cricket.duckdb")


def backfill(db_path: str = DB_PATH):
    con = duckdb.connect(db_path)

    # ── Step 1: Build unified player_map ────────────────────────────────────
    print("[backfill] Building player_map ...")
    con.execute("DROP TABLE IF EXISTS player_map")
    con.execute("""
        CREATE TABLE player_map AS
        WITH cricsheet_with_key AS (
            SELECT
                cricsheet_id,
                name AS cricsheet_name,
                CAST(key_cricinfo AS BIGINT) AS cricinfo_id
            FROM players
            WHERE key_cricinfo IS NOT NULL AND key_cricinfo != ''
        ),
        kaggle_lookup AS (
            SELECT player_id AS kaggle_player_id,
                   player_object_id AS kaggle_object_id,
                   player_name AS kaggle_name
            FROM kaggle_players
        )
        SELECT
            COALESCE(c.cricsheet_id, NULL) AS cricsheet_id,
            c.cricsheet_name,
            COALESCE(c.cricsheet_name, k.kaggle_name) AS player_name,
            COALESCE(k.kaggle_player_id, NULL) AS kaggle_player_id,
            COALESCE(c.cricinfo_id, k.kaggle_object_id) AS cricinfo_id
        FROM cricsheet_with_key c
        FULL OUTER JOIN kaggle_lookup k
            ON c.cricinfo_id = k.kaggle_object_id
    """)
    count = con.execute("SELECT COUNT(*) FROM player_map").fetchone()[0]
    mapped = con.execute("SELECT COUNT(*) FROM player_map WHERE cricsheet_id IS NOT NULL AND kaggle_player_id IS NOT NULL").fetchone()[0]
    print(f"[backfill] player_map: {count:,} total, {mapped:,} fully mapped (both sources)")

    # ── Step 2: Find missing Test matches ───────────────────────────────────
    print("[backfill] Finding Test matches in Cricsheet missing from Kaggle ...")
    con.execute("""
        CREATE TEMP TABLE missing_tests AS
        SELECT m.match_id, m.date_start, m.date_end, m.team1, m.team2,
               m.venue, m.city, m.toss_winner, m.toss_decision,
               m.outcome_winner, m.outcome_result, m.outcome_by_runs,
               m.outcome_by_wickets, m.outcome_by_innings,
               m.player_of_match, m.season
        FROM matches m
        LEFT JOIN kaggle_matches km ON CAST(m.match_id AS BIGINT) = km."Match ID"
        WHERE m.match_type = 'Test' AND km."Match ID" IS NULL
        ORDER BY m.date_start
    """)
    missing_count = con.execute("SELECT COUNT(*) FROM missing_tests").fetchone()[0]
    print(f"[backfill] Found {missing_count} missing Test matches to backfill")

    if missing_count == 0:
        print("[backfill] Nothing to backfill. Done.")
        con.close()
        return

    # Show date range of missing matches
    r = con.execute("SELECT MIN(date_start), MAX(date_start) FROM missing_tests").fetchone()
    print(f"[backfill] Missing matches range: {r[0]} to {r[1]}")

    # ── Step 3: Backfill kaggle_matches ─────────────────────────────────────
    print("[backfill] Backfilling kaggle_matches ...")

    # Get the max TEST Match No to continue numbering
    max_test_no = con.execute('SELECT MAX("TEST Match No") FROM kaggle_matches').fetchone()[0] or 0

    con.execute(f"""
        INSERT INTO kaggle_matches
        SELECT
            {max_test_no} + ROW_NUMBER() OVER (ORDER BY mt.date_start) AS "TEST Match No",
            CAST(mt.match_id AS BIGINT) AS "Match ID",
            mt.team1 || ' Vs ' || mt.team2 AS "Match Name",
            NULL AS "Series ID",
            mt.season AS "Series Name",
            mt.date_start AS "Match Start Date",
            mt.date_end AS "Match End Date",
            'TEST' AS "Match Format",
            NULL AS "Team1 ID",
            mt.team1 AS "Team1 Name",
            NULL AS "Team1 Captain",
            -- Innings scores from Cricsheet deliveries
            (SELECT SUM(runs_total) FROM deliveries d WHERE d.match_id=mt.match_id AND d.innings_num=1) AS "Innings1 Team1 Runs Scored",
            (SELECT COUNT(*) FROM wickets w JOIN deliveries d2 ON w.match_id=d2.match_id AND w.innings_num=d2.innings_num AND w.over_num=d2.over_num AND w.ball_num=d2.ball_num WHERE w.match_id=mt.match_id AND w.innings_num=1) AS "Innings1 Team1 Wickets Fell",
            (SELECT SUM(runs_extras) FROM deliveries d WHERE d.match_id=mt.match_id AND d.innings_num=1) AS "Innings1 Team1 Extras Rec",
            (SELECT SUM(runs_total) FROM deliveries d WHERE d.match_id=mt.match_id AND d.innings_num=2) AS "Innings2 Team1 Runs Scored",
            (SELECT COUNT(*) FROM wickets w WHERE w.match_id=mt.match_id AND w.innings_num=2) AS "Innings2 Team1 Wickets Fell",
            (SELECT SUM(runs_extras) FROM deliveries d WHERE d.match_id=mt.match_id AND d.innings_num=2) AS "Innings2 Team1 Extras Rec",
            NULL AS "Team2 ID",
            mt.team2 AS "Team2 Name",
            NULL AS "Team2 Captain",
            (SELECT SUM(runs_total) FROM deliveries d WHERE d.match_id=mt.match_id AND d.innings_num=3) AS "Innings1 Team2 Runs Scored",
            (SELECT COUNT(*) FROM wickets w WHERE w.match_id=mt.match_id AND w.innings_num=3) AS "Innings1 Team2 Wickets Fell",
            (SELECT SUM(runs_extras) FROM deliveries d WHERE d.match_id=mt.match_id AND d.innings_num=3) AS "Innings1 Team2 Extras Rec",
            (SELECT SUM(runs_total) FROM deliveries d WHERE d.match_id=mt.match_id AND d.innings_num=4) AS "Innings2 Team2 Runs Scored",
            (SELECT COUNT(*) FROM wickets w WHERE w.match_id=mt.match_id AND w.innings_num=4) AS "Innings2 Team2 Wickets Fell",
            (SELECT SUM(runs_extras) FROM deliveries d WHERE d.match_id=mt.match_id AND d.innings_num=4) AS "Innings2 Team2 Extras Rec",
            mt.venue AS "Match Venue (Stadium)",
            mt.city AS "Match Venue (City)",
            NULL AS "Match Venue (Country)",
            NULL AS "Umpire 1",
            NULL AS "Umpire 2",
            NULL AS "Match Referee",
            mt.toss_winner AS "Toss Winner",
            mt.toss_decision AS "Toss Winner Choice",
            mt.outcome_winner AS "Match Winner",
            CASE
                WHEN mt.outcome_by_runs IS NOT NULL THEN mt.outcome_winner || ' won by ' || mt.outcome_by_runs || ' runs'
                WHEN mt.outcome_by_wickets IS NOT NULL THEN mt.outcome_winner || ' won by ' || mt.outcome_by_wickets || ' wickets'
                WHEN mt.outcome_by_innings IS NOT NULL THEN mt.outcome_winner || ' won by an innings and ' || mt.outcome_by_runs || ' runs'
                WHEN mt.outcome_result IS NOT NULL THEN mt.outcome_result
                ELSE 'Unknown'
            END AS "Match Result Text",
            NULL AS "MOM Player",
            NULL AS "Team1 Playing 11",
            NULL AS "Team2 Playing 11",
            NULL AS "Debut Players"
        FROM missing_tests mt
    """)
    inserted = con.execute(f"""
        SELECT COUNT(*) FROM kaggle_matches WHERE "TEST Match No" > {max_test_no}
    """).fetchone()[0]
    print(f"[backfill] Inserted {inserted} rows into kaggle_matches")

    # ── Step 4: Backfill kaggle_batting ─────────────────────────────────────
    print("[backfill] Backfilling kaggle_batting ...")

    # For Test innings mapping: Cricsheet innings 1,2 = team1 batting; 3,4=team2 batting
    # Actually in Cricsheet, innings_num just goes 1,2,3,4 in order. The batting_team
    # is in the innings table. Let's use the proper team mapping.
    con.execute("""
        INSERT INTO kaggle_batting
        SELECT
            CAST(d.match_id AS BIGINT) AS "Match ID",
            i.innings_num AS innings,
            i.batting_team AS team,
            COALESCE(pm.kaggle_player_id, -1) AS batsman,
            SUM(d.runs_batter) AS runs,
            COUNT(*) FILTER (WHERE d.extras_wides IS NULL OR d.extras_wides = 0) AS balls,
            SUM(CASE WHEN d.runs_batter = 4 AND (d.non_boundary IS NULL OR d.non_boundary = FALSE) THEN 1 ELSE 0 END) AS fours,
            SUM(CASE WHEN d.runs_batter = 6 THEN 1 ELSE 0 END) AS sixes,
            CASE WHEN COUNT(*) FILTER (WHERE d.extras_wides IS NULL OR d.extras_wides = 0) > 0
                 THEN ROUND(SUM(d.runs_batter) * 100.0 / COUNT(*) FILTER (WHERE d.extras_wides IS NULL OR d.extras_wides = 0), 2)
                 ELSE 0 END AS strikeRate,
            CASE WHEN EXISTS (
                SELECT 1 FROM wickets w
                WHERE w.match_id = d.match_id AND w.innings_num = d.innings_num
                AND w.player_out = d.batter AND w.kind != 'retired hurt'
            ) THEN TRUE ELSE FALSE END AS isOut,
            (SELECT w.kind FROM wickets w
             WHERE w.match_id = d.match_id AND w.innings_num = d.innings_num
             AND w.player_out = d.batter AND w.kind != 'retired hurt'
             LIMIT 1) AS wicketType,
            (SELECT w.fielder1 FROM wickets w
             WHERE w.match_id = d.match_id AND w.innings_num = d.innings_num
             AND w.player_out = d.batter AND w.kind != 'retired hurt'
             LIMIT 1) AS fielders,
            NULL AS bowler
        FROM deliveries d
        JOIN innings i ON d.match_id = i.match_id AND d.innings_num = i.innings_num
        JOIN missing_tests mt ON d.match_id = mt.match_id
        LEFT JOIN player_map pm ON d.batter = pm.cricsheet_name
        WHERE (i.super_over = FALSE OR i.super_over IS NULL)
        GROUP BY d.match_id, d.innings_num, d.batter, i.batting_team, i.innings_num, pm.kaggle_player_id
    """)
    bat_count = con.execute("""
        SELECT COUNT(*) FROM kaggle_batting b
        JOIN missing_tests mt ON CAST(mt.match_id AS BIGINT) = b."Match ID"
    """).fetchone()[0]
    print(f"[backfill] Inserted {bat_count} batting rows")

    # ── Step 5: Backfill kaggle_bowling ─────────────────────────────────────
    print("[backfill] Backfilling kaggle_bowling ...")
    con.execute("""
        INSERT INTO kaggle_bowling
        SELECT
            CAST(d.match_id AS BIGINT) AS "Match ID",
            d.innings_num AS innings,
            i.batting_team AS team,
            (SELECT mt2.team1 FROM missing_tests mt2 WHERE mt2.match_id = d.match_id
             AND mt2.team1 != i.batting_team
             UNION
             SELECT mt2.team2 FROM missing_tests mt2 WHERE mt2.match_id = d.match_id
             AND mt2.team2 != i.batting_team
             LIMIT 1) AS opposition,
            COALESCE(pm.kaggle_player_id, -1) AS "bowler id",
            ROUND(COUNT(*) / 6.0, 1) AS overs,
            COUNT(*) AS balls,
            -- maidens: overs where total runs = 0
            (SELECT COUNT(*) FROM (
                SELECT d2.over_num FROM deliveries d2
                WHERE d2.match_id=d.match_id AND d2.innings_num=d.innings_num AND d2.bowler=d.bowler
                GROUP BY d2.over_num
                HAVING SUM(d2.runs_total) = 0
                   AND COUNT(*) >= 6
            )) AS maidens,
            SUM(d.runs_total) AS conceded,
            (SELECT COUNT(*) FROM wickets w
             WHERE w.match_id=d.match_id AND w.innings_num=d.innings_num
             AND EXISTS (
                SELECT 1 FROM deliveries d3
                WHERE d3.match_id=w.match_id AND d3.innings_num=w.innings_num
                AND d3.over_num=w.over_num AND d3.ball_num=w.ball_num
                AND d3.bowler=d.bowler
             )
             AND w.kind NOT IN ('run out','retired hurt','obstructing the field')
            ) AS wickets,
            CASE WHEN COUNT(*) > 0
                 THEN ROUND(SUM(d.runs_total) * 6.0 / COUNT(*), 2)
                 ELSE 0 END AS economy,
            SUM(CASE WHEN d.runs_total = 0 THEN 1 ELSE 0 END) AS dots,
            SUM(CASE WHEN d.runs_batter = 4 AND (d.non_boundary IS NULL OR d.non_boundary = FALSE) THEN 1 ELSE 0 END) AS fours,
            SUM(CASE WHEN d.runs_batter = 6 THEN 1 ELSE 0 END) AS sixes,
            SUM(CASE WHEN d.extras_wides > 0 THEN 1 ELSE 0 END) AS wides,
            SUM(CASE WHEN d.extras_noballs > 0 THEN 1 ELSE 0 END) AS noballs
        FROM deliveries d
        JOIN innings i ON d.match_id = i.match_id AND d.innings_num = i.innings_num
        JOIN missing_tests mt ON d.match_id = mt.match_id
        LEFT JOIN player_map pm ON d.bowler = pm.cricsheet_name
        WHERE (i.super_over = FALSE OR i.super_over IS NULL)
        GROUP BY d.match_id, d.innings_num, d.bowler, i.batting_team, pm.kaggle_player_id
    """)
    bowl_count = con.execute("""
        SELECT COUNT(*) FROM kaggle_bowling b
        JOIN missing_tests mt ON CAST(mt.match_id AS BIGINT) = b."Match ID"
    """).fetchone()[0]
    print(f"[backfill] Inserted {bowl_count} bowling rows")

    # ── Step 6: Backfill kaggle_fow ─────────────────────────────────────────
    print("[backfill] Backfilling kaggle_fow ...")
    con.execute("""
        INSERT INTO kaggle_fow
        SELECT
            CAST(w.match_id AS BIGINT) AS "Match ID",
            w.innings_num AS innings,
            i.batting_team AS team,
            COALESCE(pm.kaggle_player_id, -1) AS player,
            ROW_NUMBER() OVER (
                PARTITION BY w.match_id, w.innings_num
                ORDER BY w.over_num, w.ball_num
            ) AS wicket,
            w.over_num + (w.ball_num::DOUBLE / 10) AS over,
            (SELECT SUM(d.runs_total) FROM deliveries d
             WHERE d.match_id=w.match_id AND d.innings_num=w.innings_num
             AND (d.over_num < w.over_num OR (d.over_num = w.over_num AND d.ball_num <= w.ball_num))
            ) AS runs
        FROM wickets w
        JOIN innings i ON w.match_id = i.match_id AND w.innings_num = i.innings_num
        JOIN missing_tests mt ON w.match_id = mt.match_id
        LEFT JOIN player_map pm ON w.player_out = pm.cricsheet_name
        WHERE w.kind NOT IN ('retired hurt')
          AND (i.super_over = FALSE OR i.super_over IS NULL)
    """)
    fow_count = con.execute("""
        SELECT COUNT(*) FROM kaggle_fow f
        JOIN missing_tests mt ON CAST(mt.match_id AS BIGINT) = f."Match ID"
    """).fetchone()[0]
    print(f"[backfill] Inserted {fow_count} fall-of-wicket rows")

    # ── Summary ─────────────────────────────────────────────────────────────
    print("\n[backfill] Summary:")
    for table in ["kaggle_matches", "kaggle_batting", "kaggle_bowling", "kaggle_fow", "kaggle_partnerships", "kaggle_players", "player_map"]:
        try:
            c = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {c:,} rows")
        except Exception:
            pass

    # Quick Sachin check
    print("\n[backfill] Sachin Tendulkar centuries check:")
    r = con.execute("""
        SELECT COUNT(*) FROM kaggle_batting b
        JOIN player_map pm ON b.batsman = pm.kaggle_player_id
        WHERE pm.player_name ILIKE '%tendulkar%'
        AND b.runs >= 100
    """).fetchone()[0]
    print(f"  Test centuries: {r}")

    con.close()
    print(f"\n[backfill] Done. Database: {db_path}")


if __name__ == "__main__":
    backfill()
