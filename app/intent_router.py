"""Deterministic intent router for common cricket leaderboard questions.

Matches a tightly-anchored set of "most X in Y" questions to ready-made,
schema-correct DuckDB SQL, so the most common queries skip the LLM entirely
(zero API calls) and can never produce hallucinated SQL.

Matching is deliberately strict: the WHOLE question must fit the leaderboard
shape (``re.fullmatch``). Anything with an extra qualifier — a year, a team, a
player, a phase such as "powerplay", an ordinal such as "second" — fails to
match and ``route`` returns None, so the caller falls back to the LLM path.
The router can therefore only ever save cost, never change an answer the LLM
would otherwise have produced correctly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RoutedQuery:
    """A leaderboard question resolved to ready-to-run DuckDB SQL."""

    sql: str
    metric_label: str
    scope_label: str


# Scope phrase -> (SQL WHERE clause, human-readable label).
_SCOPES: dict[str, tuple[str, str]] = {
    "ipl": ("m.event_name = 'Indian Premier League'", "the IPL"),
    "indian premier league": ("m.event_name = 'Indian Premier League'", "the IPL"),
    "odi": ("m.match_type = 'ODI'", "ODIs"),
    "odis": ("m.match_type = 'ODI'", "ODIs"),
    "t20i": ("m.match_type = 'T20' AND m.team_type = 'international'", "T20 internationals"),
    "t20is": ("m.match_type = 'T20' AND m.team_type = 'international'", "T20 internationals"),
    "t20 international": ("m.match_type = 'T20' AND m.team_type = 'international'", "T20 internationals"),
    "t20 internationals": ("m.match_type = 'T20' AND m.team_type = 'international'", "T20 internationals"),
    "international t20": ("m.match_type = 'T20' AND m.team_type = 'international'", "T20 internationals"),
    "international t20s": ("m.match_type = 'T20' AND m.team_type = 'international'", "T20 internationals"),
}

# Captured metric token (spaces/hyphens stripped) -> internal metric id.
_METRICS: dict[str, str] = {
    "run": "runs", "runs": "runs", "runscorer": "runs", "runscorers": "runs",
    "wicket": "wickets", "wickets": "wickets",
    "wickettaker": "wickets", "wickettakers": "wickets",
    "six": "sixes", "sixes": "sixes", "6s": "sixes",
    "four": "fours", "fours": "fours", "4s": "fours",
}

_LEADERBOARD_RE = re.compile(
    r"(?:list|show|name|give me|tell me)?\s*"
    r"(?:who|which|what)?\s*"
    r"(?:player|batter|batsman|bowler)?\s*"
    r"(?:has|have|had|is|are|were)?\s*"
    r"(?:the\s+)?"
    r"(?:scored|made|hit|taken|got)?\s*"
    r"(?:the\s+)?"
    r"(?:most|highest|top|leading|best)\s+"
    r"(?:number\s+of\s+)?"
    r"\d*\s*"
    r"(?P<metric>run[\s-]?scorers?|runs?|wicket[\s-]?takers?|wickets?|"
    r"sixes|six|6s|fours|four|4s)\s*"
    r"(?:scorers?|takers?|hitters?)?\s*"
    r"(?:in|of|across)\s+"
    r"(?:the\s+)?"
    r"(?P<scope>indian premier league|ipl|odis?|t20 internationals?|"
    r"international t20s?|t20is?)\s*"
    r"(?:cricket|matches|history|ever)?\s*"
    r"(?:of\s+all[\s-]?time)?",
    re.IGNORECASE,
)


def _normalize(question: str) -> str:
    """Lower-case, drop punctuation, and collapse whitespace for matching."""
    text = (question or "").lower()
    text = re.sub(r"[?!.,;:\"'`]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _build_sql(metric: str, where_clause: str) -> str | None:
    """Return schema-correct leaderboard SQL for a metric, or None."""
    if metric == "wickets":
        return (
            "SELECT d.bowler, COUNT(*) AS wickets\n"
            "FROM wickets w\n"
            "JOIN deliveries d ON w.match_id = d.match_id AND w.innings_num = d.innings_num\n"
            "  AND w.over_num = d.over_num AND w.ball_num = d.ball_num\n"
            "JOIN matches m ON d.match_id = m.match_id\n"
            f"WHERE {where_clause}\n"
            "  AND w.kind NOT IN ('run out', 'retired hurt', 'retired out', 'obstructing the field')\n"
            "GROUP BY d.bowler\n"
            "ORDER BY wickets DESC\n"
            "LIMIT 10"
        )
    metric_expr = {
        "runs": "SUM(d.runs_batter)",
        "sixes": "SUM(CASE WHEN d.runs_batter = 6 AND d.non_boundary IS NOT TRUE THEN 1 ELSE 0 END)",
        "fours": "SUM(CASE WHEN d.runs_batter = 4 AND d.non_boundary IS NOT TRUE THEN 1 ELSE 0 END)",
    }.get(metric)
    if metric_expr is None:
        return None
    return (
        f"SELECT d.batter, {metric_expr} AS {metric}\n"
        "FROM deliveries d\n"
        "JOIN matches m ON d.match_id = m.match_id\n"
        f"WHERE {where_clause}\n"
        "GROUP BY d.batter\n"
        f"ORDER BY {metric} DESC\n"
        "LIMIT 10"
    )


def route(question: str) -> RoutedQuery | None:
    """Resolve a leaderboard question to ready SQL, or None if it does not match."""
    match = _LEADERBOARD_RE.fullmatch(_normalize(question))
    if not match:
        return None
    metric = _METRICS.get(re.sub(r"[\s-]", "", match.group("metric")))
    scope = _SCOPES.get(re.sub(r"\s+", " ", match.group("scope")).strip())
    if metric is None or scope is None:
        return None
    where_clause, scope_label = scope
    sql = _build_sql(metric, where_clause)
    if sql is None:
        return None
    return RoutedQuery(sql=sql, metric_label=metric, scope_label=scope_label)


def _fmt(value: object) -> str:
    """Format a numeric metric value with thousands separators."""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def narrate(routed: RoutedQuery, rows: list[list]) -> str:
    """Produce a short markdown answer for a routed leaderboard result."""
    name, value = rows[0][0], rows[0][1]
    answer = (
        f"**{name}** tops the list with **{_fmt(value)} {routed.metric_label}** "
        f"in {routed.scope_label}."
    )
    if len(rows) >= 3:
        second = f"{rows[1][0]} ({_fmt(rows[1][1])})"
        third = f"{rows[2][0]} ({_fmt(rows[2][1])})"
        answer += f" Next come {second} and {third}."
    return answer
