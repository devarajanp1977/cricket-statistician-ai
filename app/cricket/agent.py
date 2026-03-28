"""Cricket Statistician AI Agent.

Handles natural-language queries about cricket statistics by:
1. Detecting the user's intent from their query text.
2. Retrieving relevant data from the synthetic data store.
3. Generating a structured, human-readable answer.
4. Optionally augmenting the answer with AI-provider narrative insight.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .models import (
    CricketQueryRequest,
    CricketQueryResponse,
    LeaderboardEntry,
    LeaderboardResponse,
    MatchFormat,
    PlayerRole,
    QueryIntent,
)
from .synthetic_data import CricketDataStore

if TYPE_CHECKING:
    from ..providers.provider_base import AIProvider


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

_INTENT_PATTERNS: List[tuple[str, QueryIntent]] = [
    (r"\b(compare|vs\.?|versus|better than)\b", QueryIntent.PLAYER_COMPARISON),
    (r"\b(leaderboard|top|best|rank(?:ing)?|most runs|most wickets|highest)\b", QueryIntent.LEADERBOARD),
    (r"\b(team|squad|captain|coach|ranking)\b", QueryIntent.TEAM_STATS),
    (r"\b(match(?:es)?|result|score|won|beat|defeat|venue|played)\b", QueryIntent.MATCH_RESULTS),
    (r"\b(player|batt(?:ing|ed)|bowl(?:ing|ed)|century|centuries|wicket|average|strike rate|career)\b", QueryIntent.PLAYER_STATS),
]


def _detect_intent(query: str) -> QueryIntent:
    q = query.lower()
    for pattern, intent in _INTENT_PATTERNS:
        if re.search(pattern, q):
            return intent
    return QueryIntent.GENERAL


# ---------------------------------------------------------------------------
# Query-to-filter helpers
# ---------------------------------------------------------------------------

_FORMAT_KEYWORDS: Dict[str, MatchFormat] = {
    "test": MatchFormat.TEST,
    "tests": MatchFormat.TEST,
    "odi": MatchFormat.ODI,
    "odis": MatchFormat.ODI,
    "one day": MatchFormat.ODI,
    "t20i": MatchFormat.T20I,
    "t20is": MatchFormat.T20I,
    "t20 international": MatchFormat.T20I,
    "ipl": MatchFormat.IPL,
}


def _extract_format(query: str) -> Optional[MatchFormat]:
    q = query.lower()
    for kw, fmt in _FORMAT_KEYWORDS.items():
        if kw in q:
            return fmt
    return None


def _extract_names(query: str, players: Dict[str, Any]) -> List[str]:
    """Return list of player IDs whose names appear in the query."""
    q = query.lower()
    found = []
    for pid, player in players.items():
        # Match on last name or full name
        parts = player.name.lower().split()
        if any(part in q for part in parts if len(part) > 3):
            found.append(pid)
    return found


def _extract_team_names(query: str, teams: Dict[str, Any]) -> List[str]:
    q = query.lower()
    found = []
    for tid, team in teams.items():
        if team.name.lower() in q or team.country.lower() in q:
            found.append(tid)
    return found


# ---------------------------------------------------------------------------
# Answer builders
# ---------------------------------------------------------------------------

def _player_stats_answer(
    req: CricketQueryRequest,
    store: CricketDataStore,
    fmt: Optional[MatchFormat],
) -> CricketQueryResponse:
    player_ids = req.player_ids or _extract_names(req.query, store.players)
    if not player_ids:
        # Return all players (paginated)
        player_ids = list(store.players.keys())[: req.top_n]

    active_fmt = fmt or MatchFormat.ODI
    players_out = []
    for pid in player_ids[: req.top_n]:
        p = store.get_player(pid)
        if not p:
            continue
        bat = p.batting_stats.get(active_fmt.value)
        bowl = p.bowling_stats.get(active_fmt.value)
        players_out.append({
            "id": p.id,
            "name": p.name,
            "country": p.country,
            "role": p.role.value,
            "batting_style": p.batting_style,
            "bowling_style": p.bowling_style,
            "batting": bat.model_dump() if bat else None,
            "bowling": bowl.model_dump() if bowl else None,
        })

    if not players_out:
        return CricketQueryResponse(
            query=req.query,
            intent=QueryIntent.PLAYER_STATS,
            answer="No matching players found for your query.",
            confidence=0.4,
        )

    names = ", ".join(p["name"] for p in players_out)
    answer = (
        f"Found {len(players_out)} player(s): {names}. "
        f"Showing {active_fmt.value.upper()} career statistics."
    )
    return CricketQueryResponse(
        query=req.query,
        intent=QueryIntent.PLAYER_STATS,
        answer=answer,
        players=players_out,
        confidence=0.85,
    )


def _team_stats_answer(
    req: CricketQueryRequest,
    store: CricketDataStore,
    fmt: Optional[MatchFormat],
) -> CricketQueryResponse:
    team_ids = req.team_ids or _extract_team_names(req.query, store.teams)
    if not team_ids:
        team_ids = list(store.teams.keys())[: req.top_n]

    active_fmt = fmt or MatchFormat.ODI
    teams_out = []
    for tid in team_ids[: req.top_n]:
        t = store.get_team(tid)
        if not t:
            continue
        players_in_team = store.list_players(country=t.country)
        teams_out.append({
            "id": t.id,
            "name": t.name,
            "country": t.country,
            "captain": t.captain,
            "coach": t.coach,
            "ranking": t.ranking,
            "squad_size": len(players_in_team),
            "format_ranking": t.ranking.get(active_fmt.value),
        })

    if not teams_out:
        return CricketQueryResponse(
            query=req.query,
            intent=QueryIntent.TEAM_STATS,
            answer="No matching teams found.",
            confidence=0.4,
        )

    names = ", ".join(t["name"] for t in teams_out)
    answer = f"Found {len(teams_out)} team(s): {names}."
    return CricketQueryResponse(
        query=req.query,
        intent=QueryIntent.TEAM_STATS,
        answer=answer,
        teams=teams_out,
        confidence=0.85,
    )


def _match_results_answer(
    req: CricketQueryRequest,
    store: CricketDataStore,
    fmt: Optional[MatchFormat],
) -> CricketQueryResponse:
    team_ids = req.team_ids or _extract_team_names(req.query, store.teams)
    team_name = store.teams[team_ids[0]].name if team_ids else None

    matches = store.list_matches(fmt=fmt, team=team_name, limit=req.top_n)
    if not matches:
        return CricketQueryResponse(
            query=req.query,
            intent=QueryIntent.MATCH_RESULTS,
            answer="No matches found matching your query.",
            confidence=0.4,
        )

    matches_out = [m.model_dump() for m in matches]
    recent = matches[0]
    answer = (
        f"Found {len(matches)} match(es). "
        f"Most recent: {recent.teams[0]} vs {recent.teams[1]} on {recent.date} "
        f"at {recent.venue}. {recent.winner} won {recent.margin}."
    )
    return CricketQueryResponse(
        query=req.query,
        intent=QueryIntent.MATCH_RESULTS,
        answer=answer,
        matches=matches_out,
        confidence=0.85,
    )


def _comparison_answer(
    req: CricketQueryRequest,
    store: CricketDataStore,
    fmt: Optional[MatchFormat],
) -> CricketQueryResponse:
    player_ids = req.player_ids or _extract_names(req.query, store.players)

    if len(player_ids) < 2:
        all_ids = list(store.players.keys())
        # Pad with the first two players if not enough found
        for pid in all_ids:
            if pid not in player_ids:
                player_ids.append(pid)
            if len(player_ids) >= 2:
                break

    active_fmt = fmt or MatchFormat.ODI
    players_out = []
    for pid in player_ids[:2]:
        p = store.get_player(pid)
        if not p:
            continue
        bat = p.batting_stats.get(active_fmt.value)
        bowl = p.bowling_stats.get(active_fmt.value)
        players_out.append({
            "id": p.id,
            "name": p.name,
            "country": p.country,
            "role": p.role.value,
            "batting": bat.model_dump() if bat else None,
            "bowling": bowl.model_dump() if bowl else None,
        })

    if len(players_out) < 2:
        return CricketQueryResponse(
            query=req.query,
            intent=QueryIntent.PLAYER_COMPARISON,
            answer="Could not find two players to compare.",
            confidence=0.3,
        )

    p1, p2 = players_out
    bat1 = p1["batting"] or {}
    bat2 = p2["batting"] or {}
    avg1 = bat1.get("average", 0)
    avg2 = bat2.get("average", 0)
    better_bat = p1["name"] if avg1 >= avg2 else p2["name"]

    bowl1 = p1["bowling"] or {}
    bowl2 = p2["bowling"] or {}
    wkts1 = bowl1.get("wickets", 0)
    wkts2 = bowl2.get("wickets", 0)
    better_bowl = p1["name"] if wkts1 >= wkts2 else p2["name"]

    answer = (
        f"Comparing {p1['name']} ({p1['country']}) vs {p2['name']} ({p2['country']}) "
        f"in {active_fmt.value.upper()}. "
        f"Better batting average: {better_bat} ({max(avg1, avg2):.2f}). "
        f"More wickets: {better_bowl} ({max(wkts1, wkts2)} wkts)."
    )

    stats_table = {
        "format": active_fmt.value,
        "players": [
            {"name": p1["name"], "batting_avg": avg1, "wickets": wkts1},
            {"name": p2["name"], "batting_avg": avg2, "wickets": wkts2},
        ],
    }

    return CricketQueryResponse(
        query=req.query,
        intent=QueryIntent.PLAYER_COMPARISON,
        answer=answer,
        players=players_out,
        stats_table=stats_table,
        confidence=0.80,
    )


def _leaderboard_answer(
    req: CricketQueryRequest,
    store: CricketDataStore,
    fmt: Optional[MatchFormat],
) -> CricketQueryResponse:
    q = req.query.lower()
    active_fmt = fmt or MatchFormat.ODI
    is_bowling = any(kw in q for kw in ("bowl", "wicket", "economy"))
    metric = "wickets" if is_bowling else "runs"
    if "average" in q:
        metric = "average"
    if "strike rate" in q:
        metric = "strike_rate"
    if "economy" in q:
        metric = "economy"
    if "centuries" in q or "hundred" in q:
        metric = "centuries"

    if is_bowling:
        raw = store.bowling_leaderboard(active_fmt, metric=metric, top_n=req.top_n)
        category = "bowling"
    else:
        raw = store.batting_leaderboard(active_fmt, metric=metric, top_n=req.top_n)
        category = "batting"

    entries = []
    for rank, entry in enumerate(raw, start=1):
        p = entry["player"]
        val = entry["value"]
        entries.append({
            "rank": rank,
            "player_id": p.id,
            "player_name": p.name,
            "country": p.country,
            "value": val,
            "label": f"{val:.2f} {metric}",
        })

    if not entries:
        return CricketQueryResponse(
            query=req.query,
            intent=QueryIntent.LEADERBOARD,
            answer="No leaderboard data available.",
            confidence=0.4,
        )

    top_name = entries[0]["player_name"]
    top_val = entries[0]["value"]
    answer = (
        f"Top {category} leaderboard ({active_fmt.value.upper()}, by {metric}): "
        f"#1 {top_name} with {top_val:.2f}. "
        f"Showing top {len(entries)} players."
    )

    stats_table = {
        "format": active_fmt.value,
        "category": category,
        "metric": metric,
        "entries": entries,
    }

    return CricketQueryResponse(
        query=req.query,
        intent=QueryIntent.LEADERBOARD,
        answer=answer,
        players=[],
        stats_table=stats_table,
        confidence=0.90,
    )


def _general_answer(req: CricketQueryRequest, store: CricketDataStore) -> CricketQueryResponse:
    total_players = len(store.players)
    total_teams = len(store.teams)
    total_matches = len(store.matches)
    answer = (
        f"The cricket statistics database contains {total_players} players, "
        f"{total_teams} teams, and {total_matches} matches. "
        "You can ask about player stats, team rankings, match results, or comparisons."
    )
    return CricketQueryResponse(
        query=req.query,
        intent=QueryIntent.GENERAL,
        answer=answer,
        confidence=0.70,
    )


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------

class CricketStatisticianAgent:
    """AI-powered cricket statistician agent.

    Uses a synthetic data store as its knowledge base and optionally augments
    answers with narrative insight from an AI provider (Vertex Gemini, local
    LLM, etc.).  Follows the same provider abstraction used by the rest of
    the platform, gracefully degrading to purely synthetic answers when no
    AI provider is available.
    """

    def __init__(
        self,
        store: CricketDataStore,
        provider: Optional["AIProvider"] = None,
    ) -> None:
        self._store = store
        self._provider = provider

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(self, req: CricketQueryRequest) -> CricketQueryResponse:
        """Answer a cricket statistics query.

        Intent detection ΓåÆ data retrieval ΓåÆ optional AI augmentation.
        """
        intent = _detect_intent(req.query)
        fmt = req.format_filter or _extract_format(req.query)

        response = self._dispatch(req, intent, fmt)

        # Optionally augment with AI-provider narrative
        if self._provider:
            insight = self._ai_insight(req.query, response)
            if insight:
                response.ai_insight = insight
                response.data_source = "ai_augmented"

        return response

    def leaderboard(
        self,
        fmt: MatchFormat,
        category: str = "batting",
        metric: str = "runs",
        top_n: int = 10,
    ) -> LeaderboardResponse:
        """Return a structured leaderboard for the given format and metric."""
        if category == "bowling":
            raw = self._store.bowling_leaderboard(fmt, metric=metric, top_n=top_n)
        else:
            raw = self._store.batting_leaderboard(fmt, metric=metric, top_n=top_n)

        entries = []
        for rank, entry in enumerate(raw, start=1):
            p = entry["player"]
            val = entry["value"]
            entries.append(LeaderboardEntry(
                rank=rank,
                player_id=p.id,
                player_name=p.name,
                country=p.country,
                value=val,
                label=f"{val:.2f} {metric}",
            ))

        return LeaderboardResponse(
            format=fmt,
            category=category,
            metric=metric,
            entries=entries,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _dispatch(
        self,
        req: CricketQueryRequest,
        intent: QueryIntent,
        fmt: Optional[MatchFormat],
    ) -> CricketQueryResponse:
        if intent == QueryIntent.PLAYER_STATS:
            return _player_stats_answer(req, self._store, fmt)
        if intent == QueryIntent.TEAM_STATS:
            return _team_stats_answer(req, self._store, fmt)
        if intent == QueryIntent.MATCH_RESULTS:
            return _match_results_answer(req, self._store, fmt)
        if intent == QueryIntent.PLAYER_COMPARISON:
            return _comparison_answer(req, self._store, fmt)
        if intent == QueryIntent.LEADERBOARD:
            return _leaderboard_answer(req, self._store, fmt)
        return _general_answer(req, self._store)

    def _ai_insight(
        self, query: str, response: CricketQueryResponse
    ) -> Optional[str]:
        """Ask the AI provider for a short narrative insight."""
        if self._provider is None:
            return None
        try:
            summary_lines = [f"Query: {query}", f"Answer: {response.answer}"]
            if response.stats_table:
                summary_lines.append(f"Key stats: {response.stats_table}")
            prompt = (
                "You are a cricket statistician. "
                "Provide a concise 2-sentence expert insight based on:\n"
                + "\n".join(summary_lines)
            )
            return self._provider.freeform(prompt, max_tokens=120)
        except Exception:  # pragma: no cover - provider errors are non-fatal
            return None
