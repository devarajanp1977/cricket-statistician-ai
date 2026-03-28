"""Tests for the cricket statistician AI agent module."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.cricket.models import (
    BattingStats,
    BowlingStats,
    CricketQueryRequest,
    LeaderboardResponse,
    MatchFormat,
    Player,
    PlayerRole,
    QueryIntent,
    Team,
)
from app.cricket.synthetic_data import generate, CricketDataStore
from app.cricket.agent import (
    CricketStatisticianAgent,
    _detect_intent,
    _extract_format,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Synthetic data generation
# ---------------------------------------------------------------------------

class TestSyntheticDataGeneration:
    """Tests for the deterministic cricket data generator."""

    def test_generate_returns_data_store(self):
        store = generate(seed=1)
        assert isinstance(store, CricketDataStore)

    def test_players_count(self):
        store = generate(seed=2, num_players=30)
        # num_players distributed across 12 countries; actual count may be
        # slightly higher or lower depending on remainder allocation
        assert 28 <= len(store.players) <= 32

    def test_teams_count(self):
        store = generate(seed=3)
        # One team per country (12 countries defined)
        assert len(store.teams) == 12

    def test_matches_count(self):
        store = generate(seed=4, num_matches=20)
        assert len(store.matches) == 20

    def test_determinism(self):
        s1 = generate(seed=99, num_players=20, num_matches=10)
        s2 = generate(seed=99, num_players=20, num_matches=10)
        ids1 = sorted(s1.players.keys())
        ids2 = sorted(s2.players.keys())
        assert ids1 == ids2
        # Same stats for same seed
        p1 = list(s1.players.values())[0]
        p2 = s2.players[p1.id]
        assert p1.batting_stats == p2.batting_stats

    def test_different_seeds_produce_different_data(self):
        s1 = generate(seed=10)
        s2 = generate(seed=11)
        names1 = {p.name for p in s1.players.values()}
        names2 = {p.name for p in s2.players.values()}
        # At least some names should differ
        assert names1 != names2 or sorted(s1.players.keys()) != sorted(s2.players.keys())

    def test_player_has_stats_for_all_formats(self):
        store = generate(seed=5)
        formats = {f.value for f in MatchFormat}
        for player in list(store.players.values())[:5]:
            assert set(player.batting_stats.keys()) == formats
            assert set(player.bowling_stats.keys()) == formats

    def test_batting_stats_non_negative(self):
        store = generate(seed=6, num_players=20)
        for p in store.players.values():
            for fmt, bat in p.batting_stats.items():
                assert bat.runs >= 0
                assert bat.innings >= 0
                assert bat.average >= 0
                assert bat.strike_rate >= 0

    def test_bowling_stats_non_negative(self):
        store = generate(seed=7, num_players=20)
        for p in store.players.values():
            for fmt, bowl in p.bowling_stats.items():
                assert bowl.wickets >= 0
                assert bowl.economy >= 0

    def test_matches_have_two_teams(self):
        store = generate(seed=8, num_matches=10)
        for m in store.matches:
            assert len(m.teams) == 2
            assert m.teams[0] != m.teams[1]

    def test_matches_sorted_by_date_desc(self):
        store = generate(seed=9, num_matches=20)
        dates = [m.date for m in store.matches]
        assert dates == sorted(dates, reverse=True)

    def test_match_scores_have_correct_teams(self):
        store = generate(seed=10, num_matches=5)
        for m in store.matches:
            score_teams = {s.team for s in m.scores}
            assert score_teams == set(m.teams)


class TestDataStoreLookups:
    """Tests for CricketDataStore query methods."""

    @pytest.fixture
    def store(self):
        return generate(seed=42, num_players=24, num_matches=20)

    def test_get_player_existing(self, store):
        pid = list(store.players.keys())[0]
        p = store.get_player(pid)
        assert p is not None
        assert p.id == pid

    def test_get_player_missing(self, store):
        assert store.get_player("nonexistent_id") is None

    def test_get_team_existing(self, store):
        tid = list(store.teams.keys())[0]
        t = store.get_team(tid)
        assert t is not None
        assert t.id == tid

    def test_get_team_missing(self, store):
        assert store.get_team("unknown_team") is None

    def test_list_players_all(self, store):
        players = store.list_players()
        assert len(players) == len(store.players)

    def test_list_players_by_country(self, store):
        players = store.list_players(country="India")
        assert all(p.country == "India" for p in players)

    def test_list_players_unknown_country(self, store):
        players = store.list_players(country="Atlantis")
        assert players == []

    def test_list_matches_all(self, store):
        matches = store.list_matches()
        assert len(matches) <= 50  # default limit

    def test_list_matches_by_format(self, store):
        for fmt in MatchFormat:
            matches = store.list_matches(fmt=fmt)
            assert all(m.format == fmt for m in matches)

    def test_batting_leaderboard_returns_sorted(self, store):
        lb = store.batting_leaderboard(MatchFormat.ODI, metric="runs", top_n=5)
        vals = [e["value"] for e in lb]
        assert vals == sorted(vals, reverse=True)

    def test_batting_leaderboard_top_n(self, store):
        lb = store.batting_leaderboard(MatchFormat.TEST, metric="average", top_n=3)
        assert len(lb) <= 3

    def test_bowling_leaderboard_wickets_sorted(self, store):
        lb = store.bowling_leaderboard(MatchFormat.T20I, metric="wickets", top_n=5)
        vals = [e["value"] for e in lb]
        assert vals == sorted(vals, reverse=True)


# ---------------------------------------------------------------------------
# Agent intent detection and helpers
# ---------------------------------------------------------------------------

class TestIntentDetection:
    def test_player_stats_intent(self):
        assert _detect_intent("Show batting average for this player") == QueryIntent.PLAYER_STATS

    def test_team_stats_intent(self):
        assert _detect_intent("Show team rankings") == QueryIntent.TEAM_STATS

    def test_match_results_intent(self):
        assert _detect_intent("Show recent match results") == QueryIntent.MATCH_RESULTS

    def test_comparison_intent(self):
        assert _detect_intent("Compare two batsmen vs each other") == QueryIntent.PLAYER_COMPARISON

    def test_leaderboard_intent(self):
        assert _detect_intent("Top 10 batsmen in ODI") == QueryIntent.LEADERBOARD

    def test_general_intent(self):
        assert _detect_intent("hello") == QueryIntent.GENERAL

    def test_extract_format_test(self):
        assert _extract_format("best batsman in test cricket") == MatchFormat.TEST

    def test_extract_format_odi(self):
        assert _extract_format("ODI records") == MatchFormat.ODI

    def test_extract_format_t20i(self):
        assert _extract_format("t20i top scorers") == MatchFormat.T20I

    def test_extract_format_ipl(self):
        assert _extract_format("IPL season stats") == MatchFormat.IPL

    def test_extract_format_none(self):
        assert _extract_format("some generic query") is None


class TestCricketAgent:
    """Tests for CricketStatisticianAgent.query()."""

    @pytest.fixture
    def agent(self):
        store = generate(seed=42, num_players=24, num_matches=20)
        return CricketStatisticianAgent(store=store, provider=None)

    def test_query_returns_response(self, agent):
        req = CricketQueryRequest(query="Who are the top batsmen in ODI?")
        resp = agent.query(req)
        assert resp.answer
        assert 0.0 <= resp.confidence <= 1.0

    def test_leaderboard_query(self, agent):
        req = CricketQueryRequest(query="Top bowlers by wickets in Test cricket")
        resp = agent.query(req)
        assert resp.intent == QueryIntent.LEADERBOARD
        assert resp.stats_table is not None

    def test_player_stats_query(self, agent):
        req = CricketQueryRequest(query="Show batting statistics for a player")
        resp = agent.query(req)
        assert resp.intent == QueryIntent.PLAYER_STATS

    def test_match_results_query(self, agent):
        req = CricketQueryRequest(query="Show recent match results")
        resp = agent.query(req)
        assert resp.intent == QueryIntent.MATCH_RESULTS

    def test_comparison_query(self, agent):
        req = CricketQueryRequest(query="Compare players versus each other")
        resp = agent.query(req)
        assert resp.intent == QueryIntent.PLAYER_COMPARISON
        assert len(resp.players) == 2

    def test_team_stats_query(self, agent):
        req = CricketQueryRequest(query="Show team rankings for India")
        resp = agent.query(req)
        assert resp.intent == QueryIntent.TEAM_STATS

    def test_format_filter_respected(self, agent):
        req = CricketQueryRequest(
            query="Top batsmen",
            format_filter=MatchFormat.TEST,
            top_n=5,
        )
        resp = agent.query(req)
        assert resp.answer

    def test_general_query(self, agent):
        req = CricketQueryRequest(query="hello")
        resp = agent.query(req)
        assert resp.intent == QueryIntent.GENERAL
        assert "players" in resp.answer.lower() or "teams" in resp.answer.lower()

    def test_no_ai_insight_without_provider(self, agent):
        req = CricketQueryRequest(query="Top ODI batsmen")
        resp = agent.query(req)
        assert resp.ai_insight is None
        assert resp.data_source == "synthetic"

    def test_leaderboard_method(self, agent):
        lb = agent.leaderboard(fmt=MatchFormat.ODI, category="batting", metric="runs", top_n=5)
        assert isinstance(lb, LeaderboardResponse)
        assert len(lb.entries) <= 5
        assert lb.format == MatchFormat.ODI
        assert lb.category == "batting"

    def test_leaderboard_bowling(self, agent):
        lb = agent.leaderboard(fmt=MatchFormat.TEST, category="bowling", metric="wickets", top_n=3)
        assert lb.category == "bowling"
        assert lb.metric == "wickets"


# ---------------------------------------------------------------------------
# API endpoints (integration via TestClient)
# ---------------------------------------------------------------------------

class TestCricketAPIEndpoints:
    def test_status_endpoint(self):
        r = client.get("/api/v1/cricket/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["players"] > 0
        assert data["teams"] > 0
        assert data["matches"] > 0

    def test_list_players_endpoint(self):
        r = client.get("/api/v1/cricket/players")
        assert r.status_code == 200
        data = r.json()
        assert "players" in data
        assert "total" in data
        assert len(data["players"]) > 0

    def test_list_players_pagination(self):
        r1 = client.get("/api/v1/cricket/players?limit=5&offset=0")
        r2 = client.get("/api/v1/cricket/players?limit=5&offset=5")
        assert r1.status_code == 200
        assert r2.status_code == 200
        ids1 = {p["id"] for p in r1.json()["players"]}
        ids2 = {p["id"] for p in r2.json()["players"]}
        assert ids1.isdisjoint(ids2)

    def test_list_players_country_filter(self):
        r = client.get("/api/v1/cricket/players?country=India")
        assert r.status_code == 200
        data = r.json()
        assert all(p["country"] == "India" for p in data["players"])

    def test_list_players_role_filter(self):
        r = client.get("/api/v1/cricket/players?role=batsman")
        assert r.status_code == 200
        data = r.json()
        assert all(p["role"] == "batsman" for p in data["players"])

    def test_get_player_existing(self):
        # Get any player id from list
        players_r = client.get("/api/v1/cricket/players?limit=1")
        pid = players_r.json()["players"][0]["id"]
        r = client.get(f"/api/v1/cricket/players/{pid}")
        assert r.status_code == 200
        assert r.json()["id"] == pid

    def test_get_player_not_found(self):
        r = client.get("/api/v1/cricket/players/nonexistent_player_xyz")
        assert r.status_code == 404

    def test_list_teams_endpoint(self):
        r = client.get("/api/v1/cricket/teams")
        assert r.status_code == 200
        data = r.json()
        assert "teams" in data
        assert len(data["teams"]) > 0

    def test_get_team_existing(self):
        teams_r = client.get("/api/v1/cricket/teams")
        tid = teams_r.json()["teams"][0]["id"]
        r = client.get(f"/api/v1/cricket/teams/{tid}")
        assert r.status_code == 200
        assert r.json()["id"] == tid

    def test_get_team_not_found(self):
        r = client.get("/api/v1/cricket/teams/team_atlantis")
        assert r.status_code == 404

    def test_list_matches_endpoint(self):
        r = client.get("/api/v1/cricket/matches")
        assert r.status_code == 200
        data = r.json()
        assert "matches" in data
        assert len(data["matches"]) > 0

    def test_list_matches_format_filter(self):
        for fmt in ["test", "odi", "t20i", "ipl"]:
            r = client.get(f"/api/v1/cricket/matches?format={fmt}")
            assert r.status_code == 200
            data = r.json()
            assert all(m["format"] == fmt for m in data["matches"])

    def test_get_match_existing(self):
        matches_r = client.get("/api/v1/cricket/matches?limit=1")
        mid = matches_r.json()["matches"][0]["match_id"]
        r = client.get(f"/api/v1/cricket/matches/{mid}")
        assert r.status_code == 200
        assert r.json()["match_id"] == mid

    def test_get_match_not_found(self):
        r = client.get("/api/v1/cricket/matches/match_9999_xyz")
        assert r.status_code == 404

    def test_query_endpoint_basic(self):
        r = client.post(
            "/api/v1/cricket/query",
            json={"query": "Show top ODI batsmen"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "answer" in data
        assert "intent" in data
        assert "confidence" in data

    def test_query_endpoint_with_format_filter(self):
        r = client.post(
            "/api/v1/cricket/query",
            json={"query": "Top batsmen", "format_filter": "test", "top_n": 5},
        )
        assert r.status_code == 200

    def test_query_endpoint_too_short(self):
        r = client.post("/api/v1/cricket/query", json={"query": "hi"})
        assert r.status_code == 422

    def test_leaderboard_batting_endpoint(self):
        r = client.get("/api/v1/cricket/leaderboard/odi?category=batting&metric=runs&top_n=5")
        assert r.status_code == 200
        data = r.json()
        assert data["format"] == "odi"
        assert data["category"] == "batting"
        assert len(data["entries"]) <= 5

    def test_leaderboard_bowling_endpoint(self):
        r = client.get("/api/v1/cricket/leaderboard/test?category=bowling&metric=wickets&top_n=10")
        assert r.status_code == 200
        data = r.json()
        assert data["category"] == "bowling"

    def test_leaderboard_invalid_metric(self):
        r = client.get("/api/v1/cricket/leaderboard/odi?category=batting&metric=invalid_metric")
        assert r.status_code == 400

    def test_cricket_page_serves_html(self):
        r = client.get("/cricket")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Cricket" in r.text
