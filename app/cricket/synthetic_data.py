"""Deterministic synthetic cricket data generator.

Produces a realistic dataset of players, teams and matches using
seeded random generation so results are reproducible.  No external
calls are made; all data is fabricated from curated fixtures.
"""

from __future__ import annotations

import hashlib
import random
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from .models import (
    BattingStats,
    BowlingStats,
    Match,
    MatchFormat,
    MatchScore,
    Player,
    PlayerRole,
    Team,
)

# ---------------------------------------------------------------------------
# Curated fixture data (names, venues, etc.)
# ---------------------------------------------------------------------------

_COUNTRIES: List[str] = [
    "India", "Australia", "England", "Pakistan", "South Africa",
    "New Zealand", "West Indies", "Sri Lanka", "Bangladesh", "Afghanistan",
    "Zimbabwe", "Ireland",
]

_BATTING_STYLES: List[str] = ["Right-hand bat", "Left-hand bat"]

_BOWLING_STYLES: List[str] = [
    "Right-arm fast", "Right-arm fast-medium", "Right-arm medium",
    "Right-arm off-spin", "Right-arm leg-spin",
    "Left-arm fast", "Left-arm fast-medium", "Left-arm orthodox spin",
    "Left-arm wrist spin", "None",
]

_FIRST_NAMES: List[str] = [
    "Virat", "Rohit", "Steve", "Pat", "Joe", "Ben", "Babar", "Shaheen",
    "Kane", "Trent", "Faf", "Kagiso", "Dimuth", "Shakib", "Rashid",
    "David", "Mitchell", "Cameron", "James", "Jonny", "Jasprit", "Shubman",
    "Travis", "Marnus", "Tim", "Devon", "Will", "Chris", "Sikandar",
    "Temba", "Quinton", "Anrich", "Kyle", "Rassie", "Aiden",
    "Kusal", "Pathum", "Dhananjaya", "Chamika", "Lahiru",
    "Litton", "Mushfiqur", "Mehidy", "Taskin", "Mustafizur",
    "Noor", "Azmatullah", "Ibrahim", "Mujeeb", "Fazalhaq",
    "Craig", "Sean", "Ryan", "Blessing", "Sikandar",
    "Paul", "Lorcan", "Barry", "Andrew", "Mark",
]

_LAST_NAMES: List[str] = [
    "Kohli", "Sharma", "Smith", "Cummins", "Root", "Stokes", "Azam", "Shah",
    "Williamson", "Boult", "du Plessis", "Rabada", "Karunaratne", "Al Hasan",
    "Khan", "Warner", "Starc", "Green", "Anderson", "Bairstow", "Bumrah",
    "Gill", "Head", "Labuschagne", "Southee", "Conway", "Young", "Gayle",
    "Raza", "Bavuma", "de Kock", "Nortje", "Verreynne", "van der Dussen",
    "Markram", "Mendis", "Nissanka", "de Silva", "Karunaratne", "Sandakan",
    "Das", "Rahim", "Hasan", "Ahmed", "Rahman",
    "Ahmad", "Omarzai", "Zadran", "Mujeeb", "Farooqi",
    "Ervine", "Williams", "Burl", "Mavuta", "Masakadza",
    "Stirling", "Tucker", "McCarthy", "Balbirnie", "Tector",
]

_VENUES: List[Tuple[str, str, str]] = [
    # (venue, city, country)
    ("Eden Gardens", "Kolkata", "India"),
    ("Wankhede Stadium", "Mumbai", "India"),
    ("MA Chidambaram Stadium", "Chennai", "India"),
    ("Narendra Modi Stadium", "Ahmedabad", "India"),
    ("Melbourne Cricket Ground", "Melbourne", "Australia"),
    ("Sydney Cricket Ground", "Sydney", "Australia"),
    ("Lords Cricket Ground", "London", "England"),
    ("The Oval", "London", "England"),
    ("Edgbaston", "Birmingham", "England"),
    ("National Stadium Karachi", "Karachi", "Pakistan"),
    ("Gaddafi Stadium", "Lahore", "Pakistan"),
    ("Newlands Cricket Ground", "Cape Town", "South Africa"),
    ("Wanderers Stadium", "Johannesburg", "South Africa"),
    ("Basin Reserve", "Wellington", "New Zealand"),
    ("Eden Park", "Auckland", "New Zealand"),
    ("Kensington Oval", "Bridgetown", "West Indies"),
    ("Providence Stadium", "Georgetown", "West Indies"),
    ("Sinhalese Sports Club", "Colombo", "Sri Lanka"),
    ("Sher-e-Bangla Stadium", "Dhaka", "Bangladesh"),
    ("Sharjah Cricket Stadium", "Sharjah", "UAE"),
]

_COACHING_NAMES: List[str] = [
    "Ravi Shastri", "Justin Langer", "Chris Silverwood", "Saqlain Mushtaq",
    "Mark Boucher", "Gary Stead", "Phil Simmons", "Mickey Arthur",
    "Chris Broad", "Phil Jaques", "Dav Whatmore", "Lance Klusener",
]

_FORMATS = list(MatchFormat)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _seeded_rng(seed: int, tag: str = "") -> random.Random:
    """Return a seeded Random instance unique to (seed, tag)."""
    h = int(hashlib.md5(f"{seed}:{tag}".encode()).hexdigest(), 16) % (2**32)
    return random.Random(h)


def _pick(rng: random.Random, items: list):
    return rng.choice(items)


def _rand_name(rng: random.Random) -> str:
    first = _pick(rng, _FIRST_NAMES)
    last = _pick(rng, _LAST_NAMES)
    return f"{first} {last}"


# ---------------------------------------------------------------------------
# Batting / Bowling stat generators
# ---------------------------------------------------------------------------

def _batting_stats_for_format(
    rng: random.Random, role: PlayerRole, fmt: MatchFormat
) -> BattingStats:
    """Generate plausible batting stats for a player/format combo."""
    is_bat_dominant = role in (PlayerRole.BATSMAN, PlayerRole.WICKET_KEEPER)
    is_bowler = role == PlayerRole.BOWLER

    # innings range by format
    inns_range = {
        MatchFormat.TEST: (20, 120),
        MatchFormat.ODI: (20, 200),
        MatchFormat.T20I: (10, 100),
        MatchFormat.IPL: (10, 150),
    }
    lo, hi = inns_range[fmt]
    innings = rng.randint(lo, hi)

    not_outs = rng.randint(0, innings // 5)
    dismissals = innings - not_outs

    # runs per innings shaped by role
    if is_bat_dominant:
        avg = rng.uniform(32.0, 58.0)
    elif is_bowler:
        avg = rng.uniform(8.0, 22.0)
    else:
        avg = rng.uniform(22.0, 38.0)

    runs = int(avg * dismissals) if dismissals > 0 else rng.randint(0, 50)

    # strike rate by format
    sr_range = {
        MatchFormat.TEST: (45.0, 65.0),
        MatchFormat.ODI: (72.0, 95.0),
        MatchFormat.T20I: (115.0, 155.0),
        MatchFormat.IPL: (120.0, 165.0),
    }
    sr_lo, sr_hi = sr_range[fmt]
    strike_rate = round(rng.uniform(sr_lo, sr_hi), 2)

    # hundreds / fifties: meaningful only for batsmen in longer formats
    max_hundreds = max(0, runs // 100)
    centuries = rng.randint(0, min(max_hundreds, 50 if is_bat_dominant else 5))
    half_centuries = rng.randint(centuries, centuries + rng.randint(0, 30))

    balls_faced = int(runs / (strike_rate / 100)) if strike_rate > 0 else runs * 2
    fours = int(balls_faced * rng.uniform(0.10, 0.20))
    sixes = int(balls_faced * rng.uniform(0.01, 0.06))

    highest = min(runs, rng.randint(40, 200 if is_bat_dominant else 60))

    return BattingStats(
        innings=innings,
        runs=runs,
        not_outs=not_outs,
        highest_score=highest,
        average=round(avg, 2),
        strike_rate=strike_rate,
        centuries=centuries,
        half_centuries=half_centuries,
        fours=fours,
        sixes=sixes,
    )


def _bowling_stats_for_format(
    rng: random.Random, role: PlayerRole, fmt: MatchFormat
) -> BowlingStats:
    """Generate plausible bowling stats for a player/format combo."""
    is_bowl_dominant = role in (PlayerRole.BOWLER, PlayerRole.ALL_ROUNDER)
    is_pure_bat = role in (PlayerRole.BATSMAN, PlayerRole.WICKET_KEEPER)

    inns_range = {
        MatchFormat.TEST: (15, 100),
        MatchFormat.ODI: (15, 160),
        MatchFormat.T20I: (10, 80),
        MatchFormat.IPL: (10, 120),
    }
    lo, hi = inns_range[fmt]
    innings = rng.randint(lo // 2 if is_pure_bat else lo, hi)

    wickets = rng.randint(
        0 if is_pure_bat else 10,
        10 if is_pure_bat else 350,
    )

    overs_per_innings = {
        MatchFormat.TEST: rng.uniform(12.0, 25.0),
        MatchFormat.ODI: rng.uniform(7.0, 10.0),
        MatchFormat.T20I: rng.uniform(3.0, 4.0),
        MatchFormat.IPL: rng.uniform(3.0, 4.0),
    }
    overs = round(innings * overs_per_innings[fmt], 1)

    economy = round(rng.uniform(5.5, 8.5) if fmt in (MatchFormat.T20I, MatchFormat.IPL) else rng.uniform(3.2, 5.0), 2)
    runs_conceded = int(overs * economy)
    bowling_avg = round(runs_conceded / wickets, 2) if wickets > 0 else 0.0
    bowling_sr = round((overs * 6) / wickets, 2) if wickets > 0 else 0.0

    fifers = rng.randint(0, wickets // 25) if wickets > 25 else 0
    best_w = min(wickets, rng.randint(1 if wickets > 0 else 0, 7))
    best_r = rng.randint(best_w * 6, best_w * 18) if best_w > 0 else 0

    return BowlingStats(
        innings=innings,
        wickets=wickets,
        overs=overs,
        runs_conceded=runs_conceded,
        average=bowling_avg,
        economy=economy,
        strike_rate=bowling_sr,
        five_wicket_hauls=fifers,
        best_bowling=f"{best_w}/{best_r}" if best_w > 0 else "0/0",
    )


# ---------------------------------------------------------------------------
# Public generator
# ---------------------------------------------------------------------------

class CricketDataStore:
    """In-memory store of synthetic cricket data for one seed value."""

    def __init__(self, players: List[Player], teams: List[Team], matches: List[Match]) -> None:
        self.players: Dict[str, Player] = {p.id: p for p in players}
        self.teams: Dict[str, Team] = {t.id: t for t in teams}
        self.matches: List[Match] = matches

    # ------------------------------------------------------------------
    # Convenience lookups
    # ------------------------------------------------------------------

    def get_player(self, player_id: str) -> Optional[Player]:
        return self.players.get(player_id)

    def get_team(self, team_id: str) -> Optional[Team]:
        return self.teams.get(team_id)

    def list_players(self, country: Optional[str] = None) -> List[Player]:
        if country:
            return [p for p in self.players.values() if p.country.lower() == country.lower()]
        return list(self.players.values())

    def list_teams(self) -> List[Team]:
        return list(self.teams.values())

    def list_matches(
        self,
        fmt: Optional[MatchFormat] = None,
        team: Optional[str] = None,
        limit: int = 50,
    ) -> List[Match]:
        result = self.matches
        if fmt:
            result = [m for m in result if m.format == fmt]
        if team:
            result = [m for m in result if team.lower() in [t.lower() for t in m.teams]]
        return result[:limit]

    def batting_leaderboard(self, fmt: MatchFormat, metric: str = "average", top_n: int = 10) -> List[Dict]:
        entries = []
        for p in self.players.values():
            stats = p.batting_stats.get(fmt.value)
            if stats and stats.innings >= 5:
                val = getattr(stats, metric, None)
                if val is not None:
                    entries.append({"player": p, "value": float(val), "stats": stats})
        entries.sort(key=lambda x: x["value"], reverse=True)
        return entries[:top_n]

    def bowling_leaderboard(self, fmt: MatchFormat, metric: str = "wickets", top_n: int = 10) -> List[Dict]:
        entries = []
        for p in self.players.values():
            stats = p.bowling_stats.get(fmt.value)
            if stats and stats.wickets > 0:
                val = getattr(stats, metric, None)
                if val is not None and (metric not in ("average", "economy", "strike_rate") or val > 0):
                    entries.append({"player": p, "value": float(val), "stats": stats})
        reverse = metric in ("wickets", "five_wicket_hauls")
        entries.sort(key=lambda x: x["value"], reverse=reverse)
        return entries[:top_n]


def generate(seed: int = 42, num_players: int = 60, num_matches: int = 80) -> CricketDataStore:
    """Generate a reproducible synthetic cricket dataset.

    Parameters
    ----------
    seed:
        Random seed for deterministic output.
    num_players:
        Number of players to generate (default 60).
    num_matches:
        Number of matches to generate (default 80).

    Returns
    -------
    CricketDataStore
        In-memory store with players, teams and matches.
    """
    rng_root = _seeded_rng(seed, "root")

    # ------------------------------------------------------------------
    # Teams
    # ------------------------------------------------------------------
    teams: List[Team] = []
    for idx, country in enumerate(_COUNTRIES):
        t_rng = _seeded_rng(seed, f"team:{country}")
        coach = _pick(t_rng, _COACHING_NAMES)
        captain_first = _pick(t_rng, _FIRST_NAMES)
        captain_last = _pick(t_rng, _LAST_NAMES)
        captain = f"{captain_first} {captain_last}"
        ranking = {
            fmt.value: t_rng.randint(1, len(_COUNTRIES))
            for fmt in _FORMATS
        }
        teams.append(Team(
            id=f"team_{country.lower().replace(' ', '_')}",
            name=country,
            country=country,
            captain=captain,
            coach=coach,
            ranking=ranking,
        ))

    # ------------------------------------------------------------------
    # Players (distributed across countries)
    # ------------------------------------------------------------------
    players: List[Player] = []
    players_per_country = max(1, num_players // len(_COUNTRIES))
    remainder = num_players - players_per_country * len(_COUNTRIES)

    for c_idx, country in enumerate(_COUNTRIES):
        count = players_per_country + (1 if c_idx < remainder else 0)
        for p_idx in range(count):
            p_rng = _seeded_rng(seed, f"player:{country}:{p_idx}")
            name = _rand_name(p_rng)
            role: PlayerRole = _pick(p_rng, list(PlayerRole))
            bat_style = _pick(p_rng, _BATTING_STYLES)

            # Bowlers get a non-None bowling style
            non_bowl = _BOWLING_STYLES[:-1]  # exclude "None"
            bowl_styles = non_bowl if role in (PlayerRole.BOWLER, PlayerRole.ALL_ROUNDER) else _BOWLING_STYLES
            bowl_style = _pick(p_rng, bowl_styles)

            pid = f"player_{country.lower().replace(' ', '_')}_{p_idx}"

            bat_stats: Dict[str, BattingStats] = {}
            bowl_stats: Dict[str, BowlingStats] = {}
            for fmt in _FORMATS:
                bs_rng = _seeded_rng(seed, f"bat:{pid}:{fmt.value}")
                bat_stats[fmt.value] = _batting_stats_for_format(bs_rng, role, fmt)
                bwl_rng = _seeded_rng(seed, f"bowl:{pid}:{fmt.value}")
                bowl_stats[fmt.value] = _bowling_stats_for_format(bwl_rng, role, fmt)

            players.append(Player(
                id=pid,
                name=name,
                country=country,
                role=role,
                batting_style=bat_style,
                bowling_style=bowl_style,
                batting_stats=bat_stats,
                bowling_stats=bowl_stats,
            ))

    # ------------------------------------------------------------------
    # Matches
    # ------------------------------------------------------------------
    team_names = [t.name for t in teams]
    matches: List[Match] = []
    used_dates: set = set()

    for m_idx in range(num_matches):
        m_rng = _seeded_rng(seed, f"match:{m_idx}")
        fmt: MatchFormat = _pick(m_rng, _FORMATS)
        venue_tuple = _pick(m_rng, _VENUES)
        venue, city, venue_country = venue_tuple

        # Pick two different teams
        team_a = _pick(m_rng, team_names)
        team_b = _pick(m_rng, [t for t in team_names if t != team_a])

        # Random date in the last 3 years (using datetime for valid dates)
        _epoch = date(2022, 1, 1)
        day_offset = m_rng.randint(0, 1095)
        date_str = (_epoch + timedelta(days=day_offset)).isoformat()
        # Ensure unique dates within the dataset
        while date_str in used_dates:
            day_offset = (day_offset + 1) % 1096
            date_str = (_epoch + timedelta(days=day_offset)).isoformat()
        used_dates.add(date_str)

        # Scores calibrated by format
        def _score(r: random.Random, is_first: bool, f: MatchFormat) -> MatchScore:
            if f == MatchFormat.TEST:
                runs = r.randint(150, 520)
                wkts = r.randint(0, 10) if runs < 450 else r.randint(7, 10)
                overs = round(runs / r.uniform(2.8, 4.0), 1)
            elif f == MatchFormat.ODI:
                runs = r.randint(140, 340)
                wkts = r.randint(0, 10) if runs < 290 else r.randint(8, 10)
                overs = min(50.0, round(runs / r.uniform(5.0, 7.5), 1))
            else:  # T20I / IPL
                runs = r.randint(100, 230)
                wkts = r.randint(0, 10) if runs < 200 else r.randint(7, 10)
                overs = min(20.0, round(runs / r.uniform(8.0, 12.0), 1))
            team_name = team_a if is_first else team_b
            return MatchScore(
                team=team_name,
                runs=runs,
                wickets=wkts,
                overs=overs,
                score_str=f"{runs}/{wkts} ({overs} ov)",
            )

        score_a = _score(m_rng, True, fmt)
        score_b = _score(m_rng, False, fmt)

        if score_a.runs > score_b.runs:
            # team_a batted first and team_b couldn't overhaul the total
            winner = team_a
            margin = f"by {score_a.runs - score_b.runs} runs"
        elif score_b.runs > score_a.runs:
            # team_b chased down the target; margin is wickets in hand
            winner = team_b
            wickets_remaining = 10 - score_b.wickets
            if wickets_remaining > 0:
                margin = f"by {wickets_remaining} wicket{'s' if wickets_remaining != 1 else ''}"
            else:
                margin = f"by {score_b.runs - score_a.runs} runs"
        else:
            winner = "Tie"
            margin = "Tied"

        country_players = [p for p in players if p.country in (team_a, team_b)]
        pom_player = _pick(m_rng, country_players) if country_players else _pick(m_rng, players)
        pom_name = pom_player.name

        highlights = [
            f"{pom_name} named Player of the Match",
            f"{team_a} scored {score_a.score_str}",
            f"{team_b} scored {score_b.score_str}",
            f"{winner} won {margin}",
        ]

        matches.append(Match(
            match_id=f"match_{m_idx:04d}",
            format=fmt,
            date=date_str,
            venue=venue,
            city=city,
            country=venue_country,
            teams=[team_a, team_b],
            scores=[score_a, score_b],
            winner=winner,
            margin=margin,
            player_of_match=pom_name,
            highlights=highlights,
        ))

    matches.sort(key=lambda m: m.date, reverse=True)
    return CricketDataStore(players=players, teams=teams, matches=matches)
