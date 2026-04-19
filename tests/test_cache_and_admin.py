import os
import tempfile
import unittest
from unittest import mock

from app.engine import CricketQueryEngine
from app import main as app_main


class QueryCacheRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = CricketQueryEngine.__new__(CricketQueryEngine)
        self.engine.cache_path = os.path.join(self.temp_dir.name, "cache.duckdb")
        self.engine._init_cache()

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _result(answer: str, player_name: str) -> dict:
        return {
            "question": "And bowling?",
            "sql": f"SELECT '{player_name}' AS player_name, 10 AS wickets",
            "columns": ["player_name", "wickets"],
            "rows": [[player_name, 10]],
            "answer": answer,
            "error": None,
            "chart_config": None,
            "context_summary": answer,
            "display_hint": None,
            "model_used": "test-model",
        }

    @staticmethod
    def _history(question: str, summary: str, sql: str) -> list[dict]:
        return [{
            "question": question,
            "context_summary": summary,
            "sql": sql,
        }]

    def test_cache_lookup_respects_conversation_history(self):
        hardik_history = self._history(
            "Show me Hardik Pandya's IPL batting record",
            "Hardik Pandya IPL batting record",
            "SELECT * FROM batting WHERE player = 'HH Pandya'",
        )
        sai_history = self._history(
            "Show me Sai Sudharsan's IPL batting record",
            "Sai Sudharsan IPL batting record",
            "SELECT * FROM batting WHERE player = 'B Sai Sudharsan'",
        )

        self.engine._cache_store(
            "And bowling?",
            self._result("Hardik bowling answer", "HH Pandya"),
            history=hardik_history,
        )

        hardik_cached = self.engine._cache_lookup("And bowling?", history=hardik_history)
        sai_cached = self.engine._cache_lookup("And bowling?", history=sai_history)

        self.assertIsNotNone(hardik_cached)
        self.assertEqual(hardik_cached["answer"], "Hardik bowling answer")
        self.assertIsNone(sai_cached)

    def test_invalidate_cache_bumps_data_version_and_clears_entries(self):
        history = self._history(
            "Show me Hardik Pandya's IPL batting record",
            "Hardik Pandya IPL batting record",
            "SELECT * FROM batting WHERE player = 'HH Pandya'",
        )
        self.engine._cache_store(
            "And bowling?",
            self._result("Hardik bowling answer", "HH Pandya"),
            history=history,
        )

        before = self.engine.get_cache_stats()
        invalidation = self.engine.invalidate_cache(clear_entries=True)
        after = self.engine.get_cache_stats()
        cached = self.engine._cache_lookup("And bowling?", history=history)

        self.assertEqual(before["data_version"], 1)
        self.assertEqual(before["total_entries"], 1)
        self.assertTrue(invalidation["ok"])
        self.assertEqual(invalidation["data_version"], 2)
        self.assertEqual(after["data_version"], 2)
        self.assertEqual(after["total_entries"], 0)
        self.assertIsNone(cached)


class AdminPipelineRegressionTests(unittest.TestCase):
    def test_admin_pipeline_reports_partial_failure_and_invalidates_cache(self):
        fake_engine = mock.Mock()
        fake_engine.invalidate_cache.return_value = {
            "ok": True,
            "data_version": 4,
            "cache_cleared": True,
        }

        def download_step():
            print("downloaded latest archive")

        def load_step():
            print("loading new data")
            raise RuntimeError("load failed")

        with mock.patch.object(app_main, "engine", fake_engine):
            payload = app_main._run_admin_pipeline([
                ("download", download_step, {}),
                ("load", load_step, {}),
            ], invalidate_cache=True)

        self.assertEqual(payload["status"], "partial")
        self.assertTrue(payload["steps"][0]["ok"])
        self.assertFalse(payload["steps"][1]["ok"])
        self.assertEqual(payload["error"], "load failed")
        self.assertTrue(payload["cache_invalidated"])
        self.assertEqual(payload["data_version"], 4)
        self.assertIn("[ERROR] load", payload["log"])
        self.assertIn("ERROR: load failed", payload["log"])
        fake_engine.invalidate_cache.assert_called_once_with(clear_entries=True)


if __name__ == "__main__":
    unittest.main()