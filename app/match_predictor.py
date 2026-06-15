"""Pre-match outcome predictor — IPL Elo ratings + recent form.

Walks historical IPL matches in chronological order to compute team Elo
ratings, persists them in ``cache.duckdb``, and blends Elo with last-10
recent-form win rate to produce a win probability for a hypothetical X vs Y
match. Fires deterministically from a strict intent matcher: a question must
contain a predictive keyword AND exactly two IPL teams (by full name or
abbreviation). Otherwise ``try_predict`` returns None and the caller falls
back to the LLM path.

Limitations (documented in the user-facing answer too): no venue, toss,
lineup, or weather adjustment. Pure historical Elo + recent form.
"""

from __future__ import annotations

import re

import duckdb


# ── IPL team taxonomy ───────────────────────────────────────────────────────

# Canonical IPL team name → list of all historical name variants (handles
# franchise renames so all of a team's history feeds one Elo rating).
_IPL_TEAM_VARIANTS: dict[str, list[str]] = {
    "Royal Challengers Bengaluru": ["Royal Challengers Bengaluru", "Royal Challengers Bangalore"],
    "Delhi Capitals": ["Delhi Capitals", "Delhi Daredevils"],
    "Punjab Kings": ["Punjab Kings", "Kings XI Punjab"],
    "Sunrisers Hyderabad": ["Sunrisers Hyderabad", "Deccan Chargers"],
    "Mumbai Indians": ["Mumbai Indians"],
    "Chennai Super Kings": ["Chennai Super Kings"],
    "Kolkata Knight Riders": ["Kolkata Knight Riders"],
    "Rajasthan Royals": ["Rajasthan Royals"],
    "Gujarat Titans": ["Gujarat Titans"],
    "Lucknow Super Giants": ["Lucknow Super Giants"],
    "Rising Pune Supergiant": ["Rising Pune Supergiant", "Rising Pune Supergiants"],
    "Gujarat Lions": ["Gujarat Lions"],
    "Kochi Tuskers Kerala": ["Kochi Tuskers Kerala"],
    "Pune Warriors": ["Pune Warriors"],
}

_VARIANT_TO_CANONICAL: dict[str, str] = {
    v: canonical for canonical, variants in _IPL_TEAM_VARIANTS.items() for v in variants
}

_ABBREV_TO_CANONICAL: dict[str, str] = {
    "csk": "Chennai Super Kings",
    "mi": "Mumbai Indians",
    "rcb": "Royal Challengers Bengaluru",
    "kkr": "Kolkata Knight Riders",
    "dc": "Delhi Capitals",
    "pbks": "Punjab Kings",
    "srh": "Sunrisers Hyderabad",
    "rr": "Rajasthan Royals",
    "gt": "Gujarat Titans",
    "lsg": "Lucknow Super Giants",
}


# ── Elo computation ─────────────────────────────────────────────────────────

INITIAL_RATING = 1500.0
K_FACTOR = 20.0


def _expected(r_a: float, r_b: float) -> float:
    """Standard Elo expected-score formula."""
    return 1.0 / (1.0 + 10.0 ** ((r_b - r_a) / 400.0))


def _canonicalise(team: str | None) -> str | None:
    if not team:
        return None
    return _VARIANT_TO_CANONICAL.get(team, team)


def _compute_ipl_ratings(db_path: str) -> dict[str, dict]:
    """Walk historical IPL matches chronologically and return Elo state per team."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        rows = con.execute("""
            SELECT match_id, date_start, team1, team2, outcome_winner, outcome_result
            FROM matches
            WHERE event_name = 'Indian Premier League'
            ORDER BY date_start, match_id
        """).fetchall()
    finally:
        con.close()

    state: dict[str, dict] = {}

    def ensure(team: str) -> dict:
        if team not in state:
            state[team] = {"rating": INITIAL_RATING, "matches": 0, "wins": 0, "losses": 0, "draws": 0}
        return state[team]

    for (_match_id, _date, team1, team2, winner, result) in rows:
        a = _canonicalise(team1)
        b = _canonicalise(team2)
        if not a or not b or a == b:
            continue
        result_norm = (result or "").lower()
        if result_norm == "no result":
            continue  # rating untouched
        s_a, s_b = ensure(a), ensure(b)
        r_a, r_b = s_a["rating"], s_b["rating"]
        e_a = _expected(r_a, r_b)
        if result_norm == "tie":
            sa_score = 0.5
            s_a["draws"] += 1
            s_b["draws"] += 1
        else:
            winner_canon = _canonicalise(winner)
            if winner_canon == a:
                sa_score = 1.0
                s_a["wins"] += 1
                s_b["losses"] += 1
            elif winner_canon == b:
                sa_score = 0.0
                s_a["losses"] += 1
                s_b["wins"] += 1
            else:
                continue  # winner not one of the two teams; skip update
        s_a["rating"] = r_a + K_FACTOR * (sa_score - e_a)
        s_b["rating"] = r_b + K_FACTOR * ((1 - sa_score) - (1 - e_a))
        s_a["matches"] += 1
        s_b["matches"] += 1

    return state


# ── Persistence (cache.duckdb) ──────────────────────────────────────────────

def _store_ratings(cache_path: str, format_name: str, state: dict, data_version: int) -> None:
    """Persist computed Elo state to ``team_ratings`` in cache.duckdb."""
    con = duckdb.connect(cache_path)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS team_ratings (
                format TEXT,
                team TEXT,
                rating DOUBLE,
                matches INTEGER,
                wins INTEGER,
                losses INTEGER,
                draws INTEGER,
                PRIMARY KEY (format, team)
            )
        """)
        con.execute("DELETE FROM team_ratings WHERE format = ?", [format_name])
        for team, s in state.items():
            con.execute(
                "INSERT INTO team_ratings (format, team, rating, matches, wins, losses, draws) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [format_name, team, s["rating"], s["matches"], s["wins"], s["losses"], s["draws"]],
            )
        con.execute(
            "INSERT INTO cache_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            [f"team_ratings_{format_name}_version", str(data_version)],
        )
    finally:
        con.close()


def _load_ratings(cache_path: str, format_name: str) -> dict[str, dict]:
    """Load ratings for one format from cache.duckdb."""
    con = duckdb.connect(cache_path, read_only=True)
    try:
        rows = con.execute(
            "SELECT team, rating, matches, wins, losses, draws "
            "FROM team_ratings WHERE format = ?",
            [format_name],
        ).fetchall()
    finally:
        con.close()
    return {
        team: {"rating": rating, "matches": m, "wins": w, "losses": l_, "draws": d}
        for (team, rating, m, w, l_, d) in rows
    }


def _get_or_build_ratings(db_path: str, cache_path: str, format_name: str, data_version: int) -> dict:
    """Return ratings, rebuilding from history if the cached version is stale."""
    stored_version = -1
    try:
        con = duckdb.connect(cache_path, read_only=True)
        try:
            row = con.execute(
                "SELECT value FROM cache_meta WHERE key = ?",
                [f"team_ratings_{format_name}_version"],
            ).fetchone()
            if row and row[0] is not None:
                stored_version = int(row[0])
        finally:
            con.close()
    except Exception:
        pass

    if stored_version != data_version:
        if format_name == "IPL":
            state = _compute_ipl_ratings(db_path)
        else:
            state = {}
        _store_ratings(cache_path, format_name, state, data_version)
        return state
    return _load_ratings(cache_path, format_name)


# ── Recent form ─────────────────────────────────────────────────────────────

def _recent_form(db_path: str, team_canonical: str, n: int = 10) -> dict:
    """Return W/L/D over the team's last n IPL matches, plus a W/L/D sequence string."""
    variants = _IPL_TEAM_VARIANTS.get(team_canonical, [team_canonical])
    placeholders = ",".join(["?"] * len(variants))
    con = duckdb.connect(db_path, read_only=True)
    try:
        rows = con.execute(
            f"""
            SELECT date_start, match_id, team1, team2, outcome_winner, outcome_result
            FROM matches
            WHERE event_name = 'Indian Premier League'
              AND (team1 IN ({placeholders}) OR team2 IN ({placeholders}))
            ORDER BY date_start DESC, match_id DESC
            LIMIT ?
            """,
            list(variants) + list(variants) + [n],
        ).fetchall()
    finally:
        con.close()

    wins = losses = draws = 0
    sequence: list[str] = []
    for (_date, _mid, _t1, _t2, winner, result) in reversed(rows):
        result_norm = (result or "").lower()
        if result_norm == "no result":
            sequence.append("-")
            continue
        if result_norm == "tie":
            draws += 1
            sequence.append("D")
            continue
        if _canonicalise(winner) == team_canonical:
            wins += 1
            sequence.append("W")
        else:
            losses += 1
            sequence.append("L")
    return {"wins": wins, "losses": losses, "draws": draws, "sequence": "".join(sequence)}


# ── Intent matching ─────────────────────────────────────────────────────────

# Predictive keywords — at least one must appear for the predictor to fire,
# so plain historical questions ("how often has RR beaten MI?") fall through.
_PREDICTIVE_RE = re.compile(
    r"\b("
    r"predict|prediction|forecast|"
    r"who (?:will|would|should|might) win|"
    r"who wins\b|will win\b|would win\b|"
    r"chances? of (?:winning|beating)|"
    r"probabilit(?:y|ies) of (?:winning|a win)|"
    r"likely to (?:win|beat)|"
    r"next match"
    r")\b",
    re.IGNORECASE,
)


def _find_ipl_teams(question: str) -> list[str]:
    """Return canonical IPL team names mentioned in the question, in order found."""
    found: list[tuple[int, str]] = []
    seen: set[str] = set()
    q_lower = question.lower()

    # Full names (longest variants first to prefer specific matches).
    all_variants = sorted(
        ((canonical, v) for canonical, vs in _IPL_TEAM_VARIANTS.items() for v in vs),
        key=lambda cv: -len(cv[1]),
    )
    for canonical, variant in all_variants:
        if canonical in seen:
            continue
        m = re.search(r"\b" + re.escape(variant) + r"\b", question, re.IGNORECASE)
        if m:
            found.append((m.start(), canonical))
            seen.add(canonical)

    # Abbreviations.
    for abbrev, canonical in _ABBREV_TO_CANONICAL.items():
        if canonical in seen:
            continue
        m = re.search(r"\b" + abbrev + r"\b", q_lower)
        if m:
            found.append((m.start(), canonical))
            seen.add(canonical)

    found.sort(key=lambda x: x[0])
    return [c for (_p, c) in found]


# ── Public API ──────────────────────────────────────────────────────────────

def try_predict(question: str, db_path: str, cache_path: str, data_version: int) -> dict | None:
    """Match a 'predict X vs Y' style question and return a full response dict, or None.

    Conservative: requires a predictive keyword AND exactly two IPL teams.
    Anything else (historical record questions, non-IPL teams, ambiguous
    phrasings) returns None so the caller falls back to the LLM path.
    """
    if not _PREDICTIVE_RE.search(question or ""):
        return None
    teams = _find_ipl_teams(question)
    if len(teams) != 2:
        return None
    team_a, team_b = teams

    try:
        ratings = _get_or_build_ratings(db_path, cache_path, "IPL", data_version)
    except Exception:
        return None

    state_a = ratings.get(team_a)
    state_b = ratings.get(team_b)
    if state_a is None or state_b is None:
        return None  # one of the teams has no IPL Elo (no history)

    r_a, r_b = state_a["rating"], state_b["rating"]
    elo_pa = _expected(r_a, r_b)

    try:
        form_a = _recent_form(db_path, team_a)
        form_b = _recent_form(db_path, team_b)
    except Exception:
        form_a = form_b = {"wins": 0, "losses": 0, "draws": 0, "sequence": ""}

    total_a = form_a["wins"] + form_a["losses"] + form_a["draws"]
    total_b = form_b["wins"] + form_b["losses"] + form_b["draws"]
    rate_a = form_a["wins"] / total_a if total_a else 0.5
    rate_b = form_b["wins"] / total_b if total_b else 0.5
    denom = rate_a + rate_b
    form_pa = rate_a / denom if denom else 0.5

    # Blend: 70% Elo, 30% recent form.
    pa = 0.7 * elo_pa + 0.3 * form_pa
    pb = 1.0 - pa

    answer = (
        f"**{team_a} {int(round(pa * 100))}% — {team_b} {int(round(pb * 100))}%**  \n\n"
        f"**Elo (IPL):** {team_a} {int(round(r_a))} ({state_a['matches']} matches) "
        f"vs {team_b} {int(round(r_b))} ({state_b['matches']} matches).  \n"
        f"**Last 10 IPL matches:** {team_a} {form_a['wins']}W-{form_a['losses']}L"
        f"{'-' + str(form_a['draws']) + 'D' if form_a['draws'] else ''} "
        f"({form_a['sequence'] or '—'}), "
        f"{team_b} {form_b['wins']}W-{form_b['losses']}L"
        f"{'-' + str(form_b['draws']) + 'D' if form_b['draws'] else ''} "
        f"({form_b['sequence'] or '—'}).  \n\n"
        f"*Historical Elo (K=20, initial 1500) blended 70/30 with recent form. "
        f"Does not account for venue, toss, lineup, or weather.*"
    )

    return {
        "question": question,
        "sql": None,
        "columns": ["team", "win_prob", "elo", "last_10"],
        "rows": [
            [team_a, round(pa, 3), round(r_a, 1),
             f"{form_a['wins']}W-{form_a['losses']}L"],
            [team_b, round(pb, 3), round(r_b, 1),
             f"{form_b['wins']}W-{form_b['losses']}L"],
        ],
        "answer": answer,
        "error": None,
        "chart_config": None,
        "context_summary": f"Prediction: {team_a} vs {team_b} (IPL Elo + recent form, no LLM call)",
        "new_fact": None,
        "display_hint": {"format": "stats", "stat_type": "match"},
        "sections": None,
        "model_used": "match-predictor",
        "cached": False,
        "candidates": None,
        "original_question": None,
        "profile": None,
    }
