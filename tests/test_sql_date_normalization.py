import os
import tempfile
import unittest

import duckdb

from app.engine import CricketQueryEngine


class SqlDateNormalizationTests(unittest.TestCase):
    def test_apply_question_sql_guards_rewrites_non_duckdb_date_functions(self):
        sql = """
        SELECT *
        FROM matches
        WHERE date_start >= DATE_SUB('year', 1, CURRENT_DATE)
           OR date_start >= DATE_ADD('month', -3, CURRENT_DATE)
           OR date_start >= DATE_SUB(CURRENT_DATE, INTERVAL 30 DAY)
        """.strip()

        normalized = CricketQueryEngine._apply_question_sql_guards(sql, "recent matches")

        self.assertNotIn("DATE_SUB(", normalized.upper())
        self.assertNotIn("DATE_ADD(", normalized.upper())
        self.assertIn("CURRENT_DATE - INTERVAL 1 YEAR", normalized)
        self.assertIn("CURRENT_DATE - INTERVAL 3 MONTH", normalized)
        self.assertIn("CURRENT_DATE - INTERVAL 30 DAY", normalized)

    def test_execute_sql_accepts_three_argument_date_sub_after_normalization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.duckdb")
            duckdb.connect(db_path).close()

            engine = CricketQueryEngine.__new__(CricketQueryEngine)
            engine.db_path = db_path

            columns, rows = engine._execute_sql(
                "SELECT DATE_SUB('year', 1, CURRENT_DATE) AS start_date"
            )

        self.assertEqual(columns, ["start_date"])
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0][0])

    def test_prune_empty_metric_rows_keeps_only_rows_with_real_stats(self):
        columns = ["player_name", "avg_runs", "avg_strike_rate"]
        rows = [
            ["N Pooran", None, None],
            ["N Pooran", 32.42, 133.52],
        ]

        filtered = CricketQueryEngine._prune_empty_metric_rows(columns, rows)

        self.assertEqual(filtered, [["N Pooran", 32.42, 133.52]])


if __name__ == "__main__":
    unittest.main()