"""FastAPI router for the cricket statistician AI agent.

All routes are prefixed with /api/v1/cricket.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from .agent import CricketStatisticianAgent
from .models import (
    CricketQueryRequest,
    CricketQueryResponse,
    LeaderboardResponse,
    Match,
    MatchFormat,
    Player,
    Team,
)
from .synthetic_data import CricketDataStore, generate as generate_store

router = APIRouter(prefix="/api/v1/cricket", tags=["cricket"])

# ---------------------------------------------------------------------------
# Lazy-initialised singleton data store and agent
# ---------------------------------------------------------------------------

_DEFAULT_SEED = int(os.getenv("CRICKET_DATA_SEED", "42"))
_DEFAULT_NUM_PLAYERS = int(os.getenv("CRICKET_NUM_PLAYERS", "60"))
_DEFAULT_NUM_MATCHES = int(os.getenv("CRICKET_NUM_MATCHES", "80"))

_store: Optional[CricketDataStore] = None
_agent: Optional[CricketStatisticianAgent] = None


def _get_agent() -> CricketStatisticianAgent:
    global _store, _agent
    if _agent is None:
        _store = generate_store(
            seed=_DEFAULT_SEED,
            num_players=_DEFAULT_NUM_PLAYERS,
            num_matches=_DEFAULT_NUM_MATCHES,
        )
        # Try to reuse the application-level AI provider if available
        try:
            from ..ai_core import AIDataGenerator
            gen = AIDataGenerator()
            provider = gen.provider if gen.provider.name != "synthetic" else None
        except Exception:
            provider = None
        _agent = CricketStatisticianAgent(store=_store, provider=provider)
    return _agent


def _get_store() -> CricketDataStore:
    _get_agent()  # ensures _store is initialised
    assert _store is not None
    return _store


# ---------------------------------------------------------------------------
# Helper ΓÇô serialise domain objects for JSON responses
# ---------------------------------------------------------------------------

def _player_to_dict(p: Player) -> Dict[str, Any]:
    return p.model_dump()


def _team_to_dict(t: Team) -> Dict[str, Any]:
    return t.model_dump()


def _match_to_dict(m: Match) -> Dict[str, Any]:
    return m.model_dump()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/players", summary="List all players")
async def list_players(
    country: Optional[str] = Query(None, description="Filter by country name"),
    role: Optional[str] = Query(None, description="Filter by role: batsman/bowler/all_rounder/wicket_keeper"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """Return a paginated list of players, optionally filtered by country or role."""
    store = _get_store()
    players = store.list_players(country=country)
    if role:
        players = [p for p in players if p.role.value == role]
    total = len(players)
    page = players[offset: offset + limit]
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "players": [_player_to_dict(p) for p in page],
    }


@router.get("/players/{player_id}", response_model=Player, summary="Get a single player")
async def get_player(player_id: str) -> Player:
    """Return full profile and statistics for a specific player."""
    store = _get_store()
    player = store.get_player(player_id)
    if not player:
        raise HTTPException(status_code=404, detail=f"Player '{player_id}' not found")
    return player


@router.get("/teams", summary="List all teams")
async def list_teams() -> Dict[str, Any]:
    """Return all teams with their rankings and rosters."""
    store = _get_store()
    teams = store.list_teams()
    return {
        "total": len(teams),
        "teams": [_team_to_dict(t) for t in teams],
    }


@router.get("/teams/{team_id}", response_model=Team, summary="Get a single team")
async def get_team(team_id: str) -> Team:
    """Return profile and rankings for a specific team."""
    store = _get_store()
    team = store.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail=f"Team '{team_id}' not found")
    return team


@router.get("/matches", summary="List matches")
async def list_matches(
    format: Optional[MatchFormat] = Query(None, description="Filter by match format"),
    team: Optional[str] = Query(None, description="Filter by team name"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """Return a paginated list of matches, optionally filtered by format or team."""
    store = _get_store()
    matches = store.list_matches(fmt=format, team=team, limit=limit + offset)
    total = len(matches)
    page = matches[offset: offset + limit]
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "matches": [_match_to_dict(m) for m in page],
    }


@router.get("/matches/{match_id}", response_model=Match, summary="Get a single match")
async def get_match(match_id: str) -> Match:
    """Return full scorecard for a specific match."""
    store = _get_store()
    found = next((m for m in store.matches if m.match_id == match_id), None)
    if not found:
        raise HTTPException(status_code=404, detail=f"Match '{match_id}' not found")
    return found


@router.post("/query", response_model=CricketQueryResponse, summary="Ask the statistician")
async def cricket_query(req: CricketQueryRequest) -> CricketQueryResponse:
    """Submit a natural-language cricket statistics query.

    The agent will detect the intent, retrieve relevant data from the
    synthetic knowledge base, and optionally augment the answer with an
    AI-generated narrative insight.
    """
    agent = _get_agent()
    return agent.query(req)


@router.get("/leaderboard/{format}", response_model=LeaderboardResponse, summary="Get leaderboard")
async def get_leaderboard(
    format: MatchFormat,
    category: str = Query("batting", description="'batting' or 'bowling'"),
    metric: str = Query("runs", description="Metric to rank by: runs/average/wickets/economy/strike_rate/centuries"),
    top_n: int = Query(10, ge=1, le=50),
) -> LeaderboardResponse:
    """Return a ranked leaderboard for the given match format, category and metric."""
    valid_batting = {"runs", "average", "strike_rate", "centuries", "half_centuries", "sixes", "fours"}
    valid_bowling = {"wickets", "economy", "average", "strike_rate", "five_wicket_hauls"}
    valid = valid_batting if category == "batting" else valid_bowling
    if metric not in valid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid metric '{metric}' for {category}. Valid: {sorted(valid)}",
        )
    agent = _get_agent()
    return agent.leaderboard(fmt=format, category=category, metric=metric, top_n=top_n)


@router.get("/status", summary="Cricket module status")
async def cricket_status() -> Dict[str, Any]:
    """Return status and summary of the cricket data store."""
    store = _get_store()
    return {
        "status": "ok",
        "players": len(store.players),
        "teams": len(store.teams),
        "matches": len(store.matches),
        "formats": [f.value for f in MatchFormat],
        "seed": _DEFAULT_SEED,
    }
