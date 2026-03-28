"""Pydantic data models for the cricket statistician AI agent."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class MatchFormat(str, Enum):
    TEST = "test"
    ODI = "odi"
    T20I = "t20i"
    IPL = "ipl"


class PlayerRole(str, Enum):
    BATSMAN = "batsman"
    BOWLER = "bowler"
    ALL_ROUNDER = "all_rounder"
    WICKET_KEEPER = "wicket_keeper"


class QueryIntent(str, Enum):
    PLAYER_STATS = "player_stats"
    TEAM_STATS = "team_stats"
    MATCH_RESULTS = "match_results"
    PLAYER_COMPARISON = "player_comparison"
    LEADERBOARD = "leaderboard"
    GENERAL = "general"


# ---------------------------------------------------------------------------
# Statistics sub-models
# ---------------------------------------------------------------------------

class BattingStats(BaseModel):
    innings: int = Field(..., description="Number of innings batted")
    runs: int = Field(..., description="Total runs scored")
    not_outs: int = Field(..., description="Number of not-out dismissals")
    highest_score: int = Field(..., description="Highest individual score")
    average: float = Field(..., description="Batting average")
    strike_rate: float = Field(..., description="Batting strike rate (runs per 100 balls)")
    centuries: int = Field(..., description="Number of centuries (100+ runs)")
    half_centuries: int = Field(..., description="Number of half-centuries (50-99 runs)")
    fours: int = Field(..., description="Total boundary fours hit")
    sixes: int = Field(..., description="Total sixes hit")


class BowlingStats(BaseModel):
    innings: int = Field(..., description="Number of innings bowled")
    wickets: int = Field(..., description="Total wickets taken")
    overs: float = Field(..., description="Total overs bowled")
    runs_conceded: int = Field(..., description="Total runs conceded")
    average: float = Field(..., description="Bowling average (runs per wicket)")
    economy: float = Field(..., description="Economy rate (runs per over)")
    strike_rate: float = Field(..., description="Bowling strike rate (balls per wicket)")
    five_wicket_hauls: int = Field(..., description="Number of 5-wicket hauls")
    best_bowling: str = Field(..., description="Best bowling figures e.g. '5/20'")


# ---------------------------------------------------------------------------
# Core domain models
# ---------------------------------------------------------------------------

class Player(BaseModel):
    id: str
    name: str
    country: str
    role: PlayerRole
    batting_style: str = Field(..., description="e.g. 'Right-hand bat'")
    bowling_style: str = Field(..., description="e.g. 'Right-arm fast-medium'")
    batting_stats: Dict[str, BattingStats] = Field(
        default_factory=dict, description="Batting stats keyed by match format"
    )
    bowling_stats: Dict[str, BowlingStats] = Field(
        default_factory=dict, description="Bowling stats keyed by match format"
    )


class Team(BaseModel):
    id: str
    name: str
    country: str
    captain: str
    coach: str
    ranking: Dict[str, int] = Field(
        default_factory=dict, description="ICC rankings keyed by match format"
    )


class MatchScore(BaseModel):
    team: str
    runs: int
    wickets: int
    overs: float
    score_str: str = Field(..., description="Human-readable score e.g. '250/7 (50.0 ov)'")


class Match(BaseModel):
    match_id: str
    format: MatchFormat
    date: str = Field(..., description="Match date in YYYY-MM-DD format")
    venue: str
    city: str
    country: str
    teams: List[str] = Field(..., min_length=2, max_length=2)
    scores: List[MatchScore]
    winner: str
    margin: str = Field(..., description="e.g. 'by 6 wickets' or 'by 150 runs'")
    player_of_match: str
    highlights: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Request / Response models for the agent API
# ---------------------------------------------------------------------------

class CricketQueryRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=500, description="Natural-language question")
    format_filter: Optional[MatchFormat] = Field(
        None, description="Restrict results to a specific match format"
    )
    player_ids: Optional[List[str]] = Field(
        None, description="Restrict to specific player IDs"
    )
    team_ids: Optional[List[str]] = Field(
        None, description="Restrict to specific team IDs"
    )
    top_n: int = Field(10, ge=1, le=50, description="Max number of records to return")


class CricketQueryResponse(BaseModel):
    query: str
    intent: QueryIntent
    answer: str = Field(..., description="Human-readable answer to the query")
    players: List[Dict[str, Any]] = Field(default_factory=list)
    teams: List[Dict[str, Any]] = Field(default_factory=list)
    matches: List[Dict[str, Any]] = Field(default_factory=list)
    stats_table: Optional[Dict[str, Any]] = Field(
        None, description="Structured stats for table/chart rendering"
    )
    ai_insight: Optional[str] = Field(
        None, description="AI-generated narrative insight (when provider available)"
    )
    confidence: float = Field(..., ge=0.0, le=1.0)
    data_source: str = Field(default="synthetic", description="'synthetic' or 'ai_augmented'")


class LeaderboardEntry(BaseModel):
    rank: int
    player_id: str
    player_name: str
    country: str
    value: float
    label: str = Field(..., description="e.g. '56.78 avg' or '245 wkts'")


class LeaderboardResponse(BaseModel):
    format: MatchFormat
    category: str = Field(..., description="'batting' or 'bowling'")
    metric: str = Field(..., description="e.g. 'average', 'runs', 'wickets'")
    entries: List[LeaderboardEntry]
