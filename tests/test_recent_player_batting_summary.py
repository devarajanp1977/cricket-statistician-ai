import os
import tempfile
import unittest

import duckdb

from app.engine import CricketQueryEngine


class RecentPlayerBattingSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.duckdb")
        self.cache_path = os.path.join(self.temp_dir.name, "cache.duckdb")

        con = duckdb.connect(self.db_path)
        con.execute("""
            CREATE TABLE player_aliases (
                alias VARCHAR,
                canonical_name VARCHAR,
                cricsheet_name VARCHAR,
                team VARCHAR,
                alias_type VARCHAR
            )
        """)
        con.execute("CREATE TABLE player_map (cricsheet_name VARCHAR, kaggle_player_id INTEGER)")
        con.execute("""
            CREATE TABLE matches (
                match_id INTEGER,
                date_start DATE,
                event_name VARCHAR,
                match_type VARCHAR,
                team_type VARCHAR
            )
        """)
        con.execute("""
            CREATE TABLE deliveries (
                match_id INTEGER,
                innings_num INTEGER,
                over_num INTEGER,
                ball_num INTEGER,
                batter VARCHAR,
                bowler VARCHAR,
                runs_batter INTEGER,
                runs_total INTEGER,
                extras_wides INTEGER,
                extras_noballs INTEGER
            )
        """)
        con.execute("""
            CREATE TABLE wickets (
                match_id INTEGER,
                innings_num INTEGER,
                over_num INTEGER,
                ball_num INTEGER,
                player_out VARCHAR,
                kind VARCHAR
            )
        """)

        con.execute(
            "INSERT INTO player_aliases VALUES (?, ?, ?, ?, ?)",
            ["Nicholas Pooran", "Nicholas Pooran", "N Pooran", "West Indies", "full_name"],
        )
        con.execute(
            "INSERT INTO player_aliases VALUES (?, ?, ?, ?, ?)",
            ["Pooran", "Nicholas Pooran", "N Pooran", "West Indies", "surname"],
        )
        con.execute("INSERT INTO player_map VALUES (?, ?)", ["N Pooran", None])

        con.execute(
            "INSERT INTO matches VALUES (1, CURRENT_DATE - INTERVAL 10 DAY, 'League A', 'T20', 'domestic')"
        )
        con.execute(
            "INSERT INTO matches VALUES (2, CURRENT_DATE - INTERVAL 40 DAY, 'League B', 'T20', 'domestic')"
        )
        con.execute(
            "INSERT INTO matches VALUES (3, CURRENT_DATE - INTERVAL 2 YEAR, 'League C', 'T20', 'domestic')"
        )

        con.executemany(
            "INSERT INTO deliveries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, 1, 0, 1, "N Pooran", "Bowler A", 4, 4, 0, 0),
                (1, 1, 0, 2, "N Pooran", "Bowler A", 1, 1, 0, 0),
                (1, 1, 0, 3, "N Pooran", "Bowler A", 0, 0, 0, 0),
                (1, 1, 0, 4, "N Pooran", "Bowler A", 2, 2, 0, 0),
                (2, 1, 0, 1, "N Pooran", "Bowler B", 6, 6, 0, 0),
                (2, 1, 0, 2, "N Pooran", "Bowler B", 1, 1, 0, 0),
                (2, 1, 0, 3, "N Pooran", "Bowler B", 0, 1, 1, 0),
                (2, 1, 0, 4, "N Pooran", "Bowler B", 2, 2, 0, 0),
                (3, 1, 0, 1, "N Pooran", "Bowler C", 6, 6, 0, 0),
                (3, 1, 0, 2, "N Pooran", "Bowler C", 4, 4, 0, 0),
            ],
        )
        con.execute(
            "INSERT INTO wickets VALUES (?, ?, ?, ?, ?, ?)",
            [1, 1, 0, 4, "N Pooran", "bowled"],
        )
        con.close()

        self.engine = CricketQueryEngine.__new__(CricketQueryEngine)
        self.engine.db_path = self.db_path
        self.engine.cache_path = self.cache_path
        self.engine._init_cache()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_recent_batting_summary_query_uses_deterministic_template(self):
        result = self.engine.ask(
            "Nicholas Pooran batting avg and strike rate across all formats in the last 1 year",
            history=[],
        )

        self.assertEqual(result["model_used"], "deterministic-player-batting")
        self.assertEqual(
            result["columns"],
            [
                "player_name",
                "matches",
                "innings",
                "runs",
                "balls_faced",
                "dismissals",
                "batting_avg",
                "strike_rate",
            ],
        )
        self.assertEqual(result["rows"], [["N Pooran", 2, 2, 16, 7, 1, 16.0, 228.57]])
        self.assertIn("averaging 16.00", result["answer"])
        self.assertIn("strike rate of 228.57", result["answer"])

    def test_recent_batting_summary_is_consistent_across_phrasings(self):
        queries = [
            "Nicholas Pooran batting avg and strike rate across all formats in the last 1 year",
            "Nicholas Pooran avg and strike rate last year",
            "What has Nicholas Pooran's batting average and strike rate been over the past 1 year across all formats",
        ]

        results = [self.engine.ask(query, history=[]) for query in queries]

        for result in results:
            self.assertEqual(result["model_used"], "deterministic-player-batting")
            self.assertEqual(result["rows"], [["N Pooran", 2, 2, 16, 7, 1, 16.0, 228.57]])


if __name__ == "__main__":
    unittest.main()