"""Cricket Statistician AI — query engine.

Translates natural-language cricket questions into DuckDB SQL,
executes them, and formats results via GPT-4.1 through GitHub Models API.
"""

import os
import ast
import json
import html as _html
import hashlib
import urllib.request
import duckdb
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "db", "cricket.duckdb")
CACHE_PATH = os.path.join(BASE_DIR, "data", "db", "cache.duckdb")
KB_PATH = os.path.join(BASE_DIR, "data", "knowledge_base.json")

DEFAULT_MODEL = os.getenv("GITHUB_MODEL", "gpt-4.1")
FALLBACK_MODELS = [
    os.getenv("GITHUB_MODEL_2", "gpt-4o"),
    os.getenv("GITHUB_MODEL_3", "gpt-4o-mini"),
    os.getenv("GITHUB_MODEL_4", "gpt-5-mini"),
]
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")


import time
import re as _re


# Track rate-limited models: {model_name: timestamp_when_limit_expires}
_rate_limit_until: dict[str, float] = {}
# Track remaining calls per model from API response headers
_rate_limit_remaining: dict[str, dict] = {}
# Track total LLM calls made since server start
_llm_call_count: int = 0


def _load_knowledge_facts() -> str:
    """Load active facts from knowledge base as a formatted string for prompts."""
    try:
        if os.path.exists(KB_PATH):
            with open(KB_PATH, "r", encoding="utf-8") as f:
                kb = json.load(f)
            active = [f for f in kb.get("facts", []) if f.get("active", True)]
            if active:
                lines = [f"- {f['text']}" for f in active]
                return "\n".join(lines)
    except Exception:
        pass
    return ""

# Database schema description for the LLM
DB_SCHEMA = """
You have access to a DuckDB database with TWO data sources stitched together:

═══════════════════════════════════════════════════════════════════════════════
SOURCE 1: KAGGLE TEST DATA (1877 – present, scorecard-level, TEST MATCHES ONLY)
Use for: Test centuries, averages, career batting/bowling records, all-time Test rankings.
This is the SOURCE OF TRUTH for all Test match scorecard statistics.
═══════════════════════════════════════════════════════════════════════════════

TABLE: kaggle_matches (2,636 Test matches, 1877–2026)
  "TEST Match No" (BIGINT), "Match ID" (BIGINT), "Match Name" (VARCHAR),
  "Series ID" (BIGINT), "Series Name" (VARCHAR),
  "Match Start Date" (DATE), "Match End Date" (DATE), "Match Format" (VARCHAR),
  "Team1 ID" (BIGINT), "Team1 Name" (VARCHAR), "Team1 Captain" (BIGINT),
  "Innings1 Team1 Runs Scored" (BIGINT/DOUBLE), "Innings1 Team1 Wickets Fell" (BIGINT/DOUBLE),
  "Innings1 Team1 Extras Rec" (BIGINT/DOUBLE),
  "Innings2 Team1 Runs Scored" (DOUBLE), "Innings2 Team1 Wickets Fell" (DOUBLE),
  "Innings2 Team1 Extras Rec" (DOUBLE),
  "Team2 ID" (BIGINT), "Team2 Name" (VARCHAR), "Team2 Captain" (BIGINT),
  "Innings1 Team2 Runs Scored" (DOUBLE), ...(same pattern for Team2)...,
  "Match Venue (Stadium)" (VARCHAR), "Match Venue (City)" (VARCHAR),
  "Match Venue (Country)" (VARCHAR),
  "Toss Winner" (VARCHAR), "Toss Winner Choice" (VARCHAR),
  "Match Winner" (VARCHAR), "Match Result Text" (VARCHAR),
  "MOM Player" (DOUBLE)
  NOTE: Column names have spaces and capitals — always quote them with double-quotes!
  NOTE: Team names are full names: "India", "Australia", "England", etc.

TABLE: kaggle_batting (105,342 rows — every innings by every batsman in Test history)
  "Match ID" (BIGINT), innings (BIGINT), team (VARCHAR),
  batsman (BIGINT — this is a player_id, join to player_map or kaggle_players),
  runs (DOUBLE), balls (DOUBLE), fours (DOUBLE), sixes (DOUBLE),
  strikeRate (DOUBLE), isOut (BOOLEAN), wicketType (VARCHAR),
  fielders (VARCHAR), bowler (DOUBLE — player_id)

TABLE: kaggle_bowling (51,199 rows — every bowling spell in Test history)
  "Match ID" (BIGINT), innings (BIGINT), team (VARCHAR), opposition (VARCHAR),
  "bowler id" (BIGINT — player_id), overs (DOUBLE), balls (BIGINT),
  maidens (BIGINT), conceded (BIGINT), wickets (BIGINT),
  economy (DOUBLE), dots (DOUBLE), fours (DOUBLE), sixes (DOUBLE),
  wides (BIGINT), noballs (BIGINT)

TABLE: kaggle_fow (82,069 rows — fall of wickets)
  "Match ID" (BIGINT), innings (BIGINT), team (VARCHAR),
  player (DOUBLE — player_id), wicket (DOUBLE), over (DOUBLE), runs (DOUBLE)

TABLE: kaggle_partnerships (81,223 rows — partnership data)
  "Match ID" (BIGINT), innings (BIGINT), "for wicket" (BIGINT),
  team (VARCHAR), opposition (VARCHAR),
  player1 (BIGINT), player2 (BIGINT),
  "player1 runs" (BIGINT), "player2 runs" (BIGINT),
  "player1 balls" (BIGINT), "player2 balls" (BIGINT),
  "partnership runs" (BIGINT), "partnership balls" (BIGINT)

TABLE: kaggle_players (6,701 players)
  player_id (BIGINT PK), player_object_id (BIGINT),
  player_name (VARCHAR), dob (DATE), gender (VARCHAR),
  batting_style (VARCHAR), bowling_style (VARCHAR)

TABLE: player_map (18,235 rows — unified player mapping across both sources)
  cricsheet_id (VARCHAR), cricsheet_name (VARCHAR — e.g. "SR Tendulkar"),
  player_name (VARCHAR — SAME as cricsheet_name, Cricsheet-format initials e.g. "SR Tendulkar", NOT full name!),
  kaggle_player_id (BIGINT), cricinfo_id (BIGINT)
  WARNING: player_map.player_name is Cricsheet-format (initials), NOT full names.
  For FULL player names (e.g. "Sachin Tendulkar"), ALWAYS use kaggle_players.player_name.
  Use player_map ONLY for ID mapping between Cricsheet and Kaggle data.
  For Kaggle queries, JOIN directly: kaggle_batting.batsman → kaggle_players.player_id
  Example: LEFT JOIN kaggle_players kp ON b.batsman = kp.player_id

═══════════════════════════════════════════════════════════════════════════════
SOURCE 2: CRICSHEET BALL-BY-BALL DATA (2001/2003 – present, ALL FORMATS)
Use for: Ball-by-ball analysis, ODI/T20/IPL stats, phase-wise analysis,
partnership ball-tracking, over-by-over scoring, death overs, powerplay stats.
═══════════════════════════════════════════════════════════════════════════════

TABLE: matches (21,380 matches — Tests, ODIs, T20s, IPL, etc.)
  match_id (VARCHAR PK), match_type (VARCHAR: Test/ODI/T20/IT20/ODM/MDM),
  match_type_number (INT), gender (VARCHAR: male/female), season (VARCHAR),
  date_start (DATE), date_end (DATE), venue (VARCHAR), city (VARCHAR),
  event_name (VARCHAR), event_match_number (INT), event_group (VARCHAR),
  event_stage (VARCHAR), team1 (VARCHAR), team2 (VARCHAR),
  toss_winner (VARCHAR), toss_decision (VARCHAR: bat/field),
  outcome_winner (VARCHAR), outcome_result (VARCHAR: draw/tie/no result),
  outcome_by_runs (INT), outcome_by_wickets (INT), outcome_by_innings (INT),
  outcome_method (VARCHAR), player_of_match (VARCHAR),
  team_type (VARCHAR: international/club), overs (INT), balls_per_over (INT)

TABLE: innings
  match_id (VARCHAR), innings_num (INT), batting_team (VARCHAR),
  declared (BOOL), forfeited (BOOL), super_over (BOOL),
  target_runs (INT), target_overs (INT)
  PK: (match_id, innings_num)

TABLE: deliveries (10.9M rows — every ball bowled)
  match_id (VARCHAR), innings_num (INT), over_num (INT), ball_num (INT),
  batter (VARCHAR), bowler (VARCHAR), non_striker (VARCHAR),
  runs_batter (INT), runs_extras (INT), runs_total (INT),
  non_boundary (BOOL), extras_wides (INT), extras_noballs (INT),
  extras_byes (INT), extras_legbyes (INT), extras_penalty (INT)
  PK: (match_id, innings_num, over_num, ball_num)

TABLE: wickets
  id (INT), match_id (VARCHAR), innings_num (INT), over_num (INT),
  ball_num (INT), player_out (VARCHAR), kind (VARCHAR: bowled/caught/
  caught and bowled/lbw/stumped/run out/retired hurt/hit wicket/
  obstructing the field),
  fielder1 (VARCHAR — player display name e.g. 'MS Dhoni', NOT an ID;
    do NOT join to cricsheet_id),
  fielder2 (VARCHAR — second fielder for run outs, same format as fielder1)

TABLE: players (Cricsheet player register)
  cricsheet_id (VARCHAR PK), name (VARCHAR — matches fielder1/fielder2 format),
  unique_name (VARCHAR),
  key_cricinfo (VARCHAR), key_cricketarchive (VARCHAR),
  key_bcci (VARCHAR), key_pulse (VARCHAR)

TABLE: player_profiles (enriched player data from ESPN Cricinfo — 17K+ players)
  cricsheet_id (VARCHAR PK), cricinfo_id (VARCHAR),
  full_name (VARCHAR — e.g. "Virat Kohli"), first_name (VARCHAR), last_name (VARCHAR),
  display_name (VARCHAR), batting_style (VARCHAR — e.g. "Right hand Bat"),
  bowling_style (VARCHAR — e.g. "Right-arm medium"), playing_role (VARCHAR — e.g. "Top order Batter"),
  country (VARCHAR — e.g. "India"), dob (DATE), debut_year (INTEGER),
  is_active (BOOLEAN), gender (VARCHAR), birth_place (VARCHAR),
  jersey_number (VARCHAR), major_teams (VARCHAR — comma-separated),
  headshot_url (VARCHAR)

═══════════════════════════════════════════════════════════════════════════════
CRITICAL RULES FOR CHOOSING DATA SOURCE:
═══════════════════════════════════════════════════════════════════════════════

1. For TEST MATCH scorecard stats (centuries, averages, career runs/wickets, records):
   → ALWAYS use kaggle_batting / kaggle_bowling + kaggle_players for player names
   → These cover the COMPLETE history from 1877 to present

2. For BALL-BY-BALL analysis (strike rates by phase, over-by-over, dot ball %, etc.):
   → Use deliveries table (Cricsheet). Caveat: only from ~2003 onwards.

3. For ODI / T20I / IPL / BBL / franchise cricket:
   → Use Cricsheet tables (matches + deliveries). Kaggle has Tests ONLY.

4. For Test MATCH-LEVEL data (results, venues, toss stats):
   → Prefer kaggle_matches (complete history) over matches table.

5. Player names: Kaggle uses player IDs → ALWAYS JOIN to kaggle_players for full names.
   NEVER use player_map.player_name for display — it has Cricsheet-style initials ("SR Tendulkar"), not full names.
   kaggle_players.player_name has full names ("Sachin Tendulkar", "Virat Kohli").
   Cricsheet tables use name strings directly (e.g. "V Kohli", "SR Tendulkar").

IMPORTANT NOTES:
- Team names are full names: "India", "Australia", "England", etc.
- Cricsheet player names use initials: "V Kohli", "SPD Smith", "JE Root"
- kaggle_players.player_name has FULL names: "Virat Kohli", "Steven Smith", "Joe Root"
- player_map.player_name has CRICSHEET initials (same as cricsheet_name), NOT full names
- For Kaggle queries, ALWAYS use kaggle_players.player_name for display, NEVER player_map.player_name
- Cricsheet match_type values: Test, ODI, T20, IT20, ODM, MDM
- For IPL/BBL/etc. use event_name, e.g. event_name = 'Indian Premier League'
- TEAM ABBREVIATIONS: Users often use short names. ALWAYS expand to full team names in SQL:
  IPL: SRH = 'Sunrisers Hyderabad', CSK = 'Chennai Super Kings', MI = 'Mumbai Indians',
  RCB = 'Royal Challengers Bengaluru', KKR = 'Kolkata Knight Riders', DC = 'Delhi Capitals',
  PBKS = 'Punjab Kings', RR = 'Rajasthan Royals', GT = 'Gujarat Titans', LSG = 'Lucknow Super Giants'
  International: IND = 'India', AUS = 'Australia', ENG = 'England', SA = 'South Africa',
  NZ = 'New Zealand', PAK = 'Pakistan', SL = 'Sri Lanka', WI = 'West Indies', BAN = 'Bangladesh',
  ZIM = 'Zimbabwe', AFG = 'Afghanistan', IRE = 'Ireland', SCO = 'Scotland', NEP = 'Nepal'
- IPL TEAM RENAMES — CRITICAL: Several IPL teams changed names over the years. The database has
  BOTH old and new names. When querying ANY historical/head-to-head/all-time IPL team data, you
  MUST match ALL name variants using IN (...) — not just the current name:
  RCB: IN ('Royal Challengers Bengaluru', 'Royal Challengers Bangalore')
  DC:  IN ('Delhi Capitals', 'Delhi Daredevils')
  PBKS: IN ('Punjab Kings', 'Kings XI Punjab')
  SRH also absorbed: 'Deccan Chargers' (predecessor franchise, 2008-2012)
  Also existed temporarily: 'Rising Pune Supergiant', 'Rising Pune Supergiants', 'Gujarat Lions',
    'Kochi Tuskers Kerala', 'Pune Warriors'
  For outcome_winner checks, also use IN (...) with all variants.
  Example — CSK vs RCB head-to-head:
    WHERE (m.team1 = 'Chennai Super Kings' AND m.team2 IN ('Royal Challengers Bengaluru', 'Royal Challengers Bangalore'))
       OR (m.team1 IN ('Royal Challengers Bengaluru', 'Royal Challengers Bangalore') AND m.team2 = 'Chennai Super Kings')
  And for counting wins: CASE WHEN m.outcome_winner IN ('Royal Challengers Bengaluru', 'Royal Challengers Bangalore') THEN 1 END
- TEAM QUERIES: When users mention a team (by name or abbreviation), filter using batting_team in
  the innings table, or team1/team2 in matches — NEVER filter on batter or bowler columns for teams.
  CRITICAL: The deliveries table has NO team column. You MUST JOIN to innings to get batting_team.
  Example: "SRH's avg score batting first" →
    FROM deliveries d
    JOIN innings i ON d.match_id = i.match_id AND d.innings_num = i.innings_num
    JOIN matches m ON d.match_id = m.match_id
    WHERE i.batting_team = 'Sunrisers Hyderabad' AND m.event_name = 'Indian Premier League'
- Cricsheet over_num is 0-indexed (first over = 0)
- Powerplay overs in T20: over_num 0-5, death overs: over_num 15-19
- Use ILIKE for case-insensitive name matching
- When user says a FULL NAME like "Steve Smith", match the full name: ILIKE '%Steven Smith%'
  NEVER use just '%Smith%' — it matches ALL Smiths. Common aliases:
  Steve Smith = Steven Smith, Viv Richards = Vivian Richards
- Always use player names with ILIKE '%full_name%' for flexibility
"""

# Compact prompt for SQL retry — schema only, no verbose rules / narrative instructions.
# Keeps total token count low enough for gpt-4o-mini (8 000-token API limit).
SQL_RETRY_PROMPT = f"""You are a DuckDB SQL expert. Fix the failed SQL query using the schema below.
Return ONLY the corrected SQL, no explanation, no markdown fences.

{DB_SCHEMA}
"""

SQL_PROMPT_HEADER = """You are a cricket statistics SQL expert. Given a natural-language cricket question, either:
1. Generate a DuckDB SQL query to answer it (return ONLY SQL, no prose, no markdown), OR
2. Call one of the available WASP tools for predictions and probability questions.

Use wasp_predict_score when asked to predict/forecast a first-innings final score.
Use wasp_win_probability when asked about chances of winning, chase probability, or can a team win.
For all other questions (historical stats, records, comparisons), generate SQL."""

SQL_SCHEMA_KAGGLE_COMPACT = """
KAGGLE TEST SOURCE: authoritative for Test career stats and Test scorecard records.

TABLE kaggle_matches:
    "Match ID", "Match Name", "Match Start Date", "Team1 Name", "Team2 Name",
    "Match Venue (Country)", "Match Winner", "Match Result Text"

TABLE kaggle_batting:
    "Match ID", innings, team, batsman, runs, balls, fours, sixes,
    strikeRate, isOut, wicketType, fielders, bowler
    -- wicketType: caught/bowled/lbw/stumped/run out/caught and bowled/hit wicket
    -- fielders: VARCHAR containing a player_id in format like ['59611']. Extract with: TRY_CAST(REGEXP_EXTRACT(fielders, '([0-9]+)') AS INT). Use TRY_CAST (not CAST) to safely skip malformed values. Join to kaggle_players.player_id.
    -- For fielding stats (e.g. most catches in Tests): GROUP BY the extracted player_id, filter wicketType='caught'.

TABLE kaggle_bowling:
    "Match ID", innings, team, opposition, "bowler id", overs, balls,
    maidens, conceded, wickets, economy

TABLE kaggle_players:
    player_id, player_name, dob, gender, batting_style, bowling_style

TABLE player_map:
    cricsheet_name, kaggle_player_id, cricinfo_id
"""

SQL_SCHEMA_CRICSHEET_COMPACT = """
CRICSHEET SOURCE: authoritative for ODI, T20I, IPL, franchise cricket, and ball-by-ball analysis.

TABLE matches:
    match_id, match_type, date_start, venue, city, event_name,
    team1, team2, outcome_winner, outcome_result, team_type, overs, balls_per_over

TABLE innings:
    match_id, innings_num, batting_team, target_runs, target_overs, super_over

TABLE deliveries:
    match_id, innings_num, over_num, ball_num, batter, bowler, non_striker,
    runs_batter, runs_extras, runs_total, non_boundary,
    extras_wides, extras_noballs, extras_byes, extras_legbyes

TABLE wickets:
    match_id, innings_num, over_num, ball_num, player_out, kind, fielder1, fielder2
    -- fielder1 and fielder2 contain player DISPLAY NAMES (e.g. 'MS Dhoni'), NOT IDs.
    -- Do NOT join fielder1/fielder2 to players.cricsheet_id (always 0 rows).
    -- For fielding stats, use fielder1 directly as the player name.
    -- kind='stumped' → fielder1 is always the wicketkeeper (cricket rule).
    -- kind='caught' includes keeper catches and fielder catches (no distinction in data).

TABLE players:
    cricsheet_id, name, unique_name, key_cricinfo

TABLE player_profiles:
    cricsheet_id, display_name, batting_style, bowling_style, playing_role,
    country, dob, debut_year, is_active, major_teams

TABLE player_map:
    cricsheet_name, kaggle_player_id, cricinfo_id
"""

SQL_RULES_COMMON_COMPACT = """
Common rules:
1. Limit to 50 rows unless the user asks otherwise.
2. Always qualify shared columns with table aliases.
3. Never return raw player ids; join to display names.
4. If a [PLAYER FILTER] block is present, copy that filter exactly and do not replace it with ILIKE matching.
5. Use HAVING or an outer query for aggregate filters; never put SUM, COUNT, AVG, MIN, or MAX directly in WHERE.
6. Keep data source consistency with follow-up context unless the user explicitly changes scope.
7. DuckDB relative dates use INTERVAL arithmetic, for example CURRENT_DATE - INTERVAL 1 YEAR or date_col >= CURRENT_DATE - INTERVAL 30 DAY. Never use MySQL-style DATE_ADD('year', -1, CURRENT_DATE) or DATE_SUB(CURRENT_DATE, INTERVAL 1 YEAR).
8. If the user explicitly says all formats or across all formats, do not restrict to international matches or a single competition unless the question asks for that scope. For recent all-format player form, prefer Cricsheet because it covers recent Tests plus limited-overs cricket in one source.
"""

SQL_RULES_KAGGLE_COMPACT = """
Kaggle Test rules:
1. Use kaggle_batting or kaggle_bowling plus kaggle_players for Test career stats, Test records, Test averages, and Test rankings.
2. Use kaggle_matches for Test match-level details like result, venue, and winner.
3. Kaggle player references are numeric ids. Join via kaggle_players.player_id for full player names.
4. Quote Kaggle columns that contain spaces or capitals with double quotes.
5. When a user supplies a full player name, match via kaggle_players.player_name, not player_map.player_name.
6. For fielding stats in Tests: kaggle_batting.fielders is a VARCHAR like '['59611']'. Extract the numeric id with TRY_CAST(REGEXP_EXTRACT(fielders, '([0-9]+)') AS INT) — use TRY_CAST not CAST to skip malformed rows. Join to kaggle_players.player_id. Filter wicketType for the dismissal type (e.g. 'caught', 'stumped').
"""

SQL_RULES_CRICSHEET_COMPACT = """
Cricsheet rules:
1. Use matches, innings, deliveries, and wickets for ODI, T20I, IPL, franchise, and ball-by-ball questions.
2. Team filters belong on innings.batting_team or matches.team1 and matches.team2. Deliveries has no team column.
3. Cricsheet batting formula: first aggregate per innings by batter, match_id, innings_num; balls faced exclude wides; dismissals come from wickets excluding retired hurt and retired out; batting average is runs divided by dismissals; strike rate is runs times 100 divided by balls faced; centuries and fifties come from the per-innings CTE.
4. Cricsheet bowling formula: legal balls exclude wides and no-balls; wickets come from wickets joined to deliveries on match_id, innings_num, over_num, and ball_num; exclude run out, retired hurt, obstructing the field, and retired out.
5. The wickets table does not have a bowler column. Join to deliveries and use d.bowler. For bowling wickets, join wickets to deliveries on (match_id, innings_num, over_num, ball_num) ONLY — NEVER add d.bowler = w.player_out (that compares the bowler to the person dismissed, giving 0 matches). The bowler is already filtered via WHERE d.bowler = 'Name'.
6. Franchise leagues such as IPL, BBL, PSL, CPL, and WPL should be filtered with matches.event_name, not matches.match_type. For IPL use event_name = 'Indian Premier League'.
7. Head-to-head or between-team queries must filter the exact pairing: (m.team1 = team_a AND m.team2 = team_b) OR (m.team1 = team_b AND m.team2 = team_a), including rename variants where needed.
8. Cricsheet over_num is 0-indexed.
9. Fielding stats: fielder1 and fielder2 store player DISPLAY NAMES (e.g. 'MS Dhoni'), NOT cricsheet_id. Use fielder1 directly as the player name — do NOT join to players.cricsheet_id (produces 0 rows). Filter wickets.kind for the dismissal type: 'caught' for catches, 'stumped' for stumpings, 'run out' for run outs. For stumpings, fielder1 is always the wicketkeeper. To approximate wicketkeeper catches, join fielder1 to player_profiles via players.name and filter playing_role ILIKE '%Wicketkeeper%'.
10. Chase and win-probability queries: when the user asks about chances of winning, can X chase, or probability of winning a target, you MUST use a target RANGE: i.target_runs BETWEEN (target-15) AND (target+15). NEVER use exact i.target_runs = N because the same target almost never recurs and you will get 0 rows. Filter by the specific chasing team with i.batting_team. Use this CTE pattern:
    WITH second_innings AS (
        SELECT m.match_id, i.batting_team, i.target_runs, SUM(d.runs_total) as chase_total
        FROM matches m JOIN innings i ON m.match_id = i.match_id AND i.innings_num = 2 AND i.super_over = 0
        JOIN deliveries d ON m.match_id = d.match_id AND d.innings_num = 2
        WHERE i.target_runs BETWEEN <target-15> AND <target+15> AND i.batting_team = '<chasing_team>'
        GROUP BY m.match_id, i.batting_team, i.target_runs)
    SELECT COUNT(*) as total_chases, SUM(CASE WHEN chase_total >= target_runs THEN 1 ELSE 0 END) as successful,
           ROUND(100.0 * SUM(CASE WHEN chase_total >= target_runs THEN 1 ELSE 0 END) / COUNT(*), 1) as win_pct FROM second_innings
"""

SQL_RULES_TEAM_NAMES_COMPACT = """
Team naming rules:
1. Expand common abbreviations to full names, for example CSK, RCB, SRH, MI, KKR, DC, PBKS, IND, AUS, ENG.
2. Historical IPL queries must include renamed variants with IN (...):
     RCB = Royal Challengers Bengaluru or Royal Challengers Bangalore
     DC = Delhi Capitals or Delhi Daredevils
     PBKS = Punjab Kings or Kings XI Punjab
     SRH historical predecessor may require Deccan Chargers when the question is truly all-time franchise history.
"""

SQL_RULES_OUTPUT_COMPACT = """
Output-shaping rules:
1. Scorecard lookup queries must include match_id or "Match ID" in the SELECT.
2. Career or record queries for a single player must return exactly ONE row of aggregated totals. Always GROUP BY the player name across all their innings. Never return per-innings rows for a career summary.
3. For all-rounders, include both batting columns (runs, batting_avg, strike_rate, fifties, centuries, highest_score) AND bowling columns (wickets, bowling_avg, economy, bowling_sr, runs_conceded) in a single row.
4. Use clear aliases such as batting_avg, strike_rate, highest_score, bowling_avg, economy, bowling_sr, runs_conceded.
"""

SQL_COMPACT_SECTIONS = {
        "full": (
                SQL_SCHEMA_KAGGLE_COMPACT.strip() + "\n\n" + SQL_SCHEMA_CRICSHEET_COMPACT.strip(),
                "\n\n".join([
                        SQL_RULES_COMMON_COMPACT.strip(),
                        SQL_RULES_KAGGLE_COMPACT.strip(),
                        SQL_RULES_CRICSHEET_COMPACT.strip(),
                        SQL_RULES_TEAM_NAMES_COMPACT.strip(),
                        SQL_RULES_OUTPUT_COMPACT.strip(),
                ]),
        ),
        "kaggle": (
                SQL_SCHEMA_KAGGLE_COMPACT.strip(),
                "\n\n".join([
                        SQL_RULES_COMMON_COMPACT.strip(),
                        SQL_RULES_KAGGLE_COMPACT.strip(),
                        SQL_RULES_OUTPUT_COMPACT.strip(),
                ]),
        ),
        "cricsheet": (
                SQL_SCHEMA_CRICSHEET_COMPACT.strip(),
                "\n\n".join([
                        SQL_RULES_COMMON_COMPACT.strip(),
                        SQL_RULES_CRICSHEET_COMPACT.strip(),
                        SQL_RULES_TEAM_NAMES_COMPACT.strip(),
                        SQL_RULES_OUTPUT_COMPACT.strip(),
                ]),
        ),
}


def _load_compact_knowledge_facts(max_chars: int = 900, max_items: int = 10) -> str:
        """Load a size-limited subset of active facts for SQL prompts."""
        try:
                if not os.path.exists(KB_PATH):
                        return ""
                with open(KB_PATH, "r", encoding="utf-8") as f:
                        kb = json.load(f)
                selected: list[str] = []
                current_len = 0
                for fact in kb.get("facts", []):
                        if not fact.get("active", True):
                                continue
                        line = f"- {fact['text']}"
                        projected = current_len + len(line) + (1 if selected else 0)
                        if projected > max_chars:
                                break
                        selected.append(line)
                        current_len = projected
                        if len(selected) >= max_items:
                                break
                return "\n".join(selected)
        except Exception:
                return ""


def _build_compact_sql_prompt(intent: str, include_facts: bool = True) -> str:
        """Build a compact SQL system prompt for the detected query intent."""
        schema_text, rules_text = SQL_COMPACT_SECTIONS.get(intent, SQL_COMPACT_SECTIONS["full"])
        parts = [SQL_PROMPT_HEADER, schema_text, rules_text]
        if include_facts:
                facts = _load_compact_knowledge_facts()
                if facts:
                        parts.append("Cricket domain knowledge:\n" + facts)
        return "\n\n".join(part for part in parts if part)

SQL_SYSTEM_PROMPT = f"""You are a cricket statistics SQL expert. Given a natural-language question about cricket, generate a DuckDB SQL query to answer it.

{DB_SCHEMA}

Rules:
1. Return ONLY the SQL query, no explanation, no markdown code fences.
2. Always limit results to 50 rows max unless the user asks for all.
3. Use appropriate aggregations (AVG, SUM, COUNT, etc.).
3a. DUCKDB DATE ARITHMETIC: use expressions like CURRENT_DATE - INTERVAL 1 YEAR, CURRENT_DATE - INTERVAL 30 DAY, or date_col + INTERVAL 1 MONTH. Never use MySQL-style DATE_ADD('year', -1, CURRENT_DATE), DATE_SUB('year', 1, CURRENT_DATE), or DATE_SUB(CURRENT_DATE, INTERVAL 1 YEAR).
3b. ALL FORMATS MEANS ALL FORMATS: if the user explicitly says all formats or across all formats, do not silently restrict the query to international cricket or a single competition. For recent player form across all formats, prefer Cricsheet matches/deliveries unless the user explicitly asks for all-time Test history.
4. CRICSHEET BATTING CALCULATIONS (deliveries table) — MANDATORY FORMULAS:
   These are EXACT formulas. Do NOT deviate. The deliveries table is ball-by-ball data.
   a) balls_faced: COUNT(*) FILTER (WHERE extras_wides = 0) — wides are NOT balls faced by the batter.
   b) dismissals: Must come from the WICKETS TABLE, NOT from counting deliveries!
      Use: (SELECT COUNT(*) FROM wickets w WHERE w.player_out = d.batter AND w.match_id = d.match_id ...)
      Or via a CTE joining wickets table. Exclude kind IN ('retired hurt', 'retired out').
   c) batting_average: total_runs / NULLIF(dismissals, 0) — runs divided by DISMISSALS from wickets table.
      NEVER divide runs by balls or by innings count. This produces wrong averages (~1-2 instead of ~35-50).
   d) strike_rate: (total_runs * 100.0) / NULLIF(balls_faced, 0) — expected range: 70-150 for T20, 40-60 for Tests.
      If your SR result is below 10, YOUR FORMULA IS WRONG.
   e) innings: COUNT(DISTINCT (match_id, innings_num)) for that batter.
   f) not_outs: innings - dismissals.
   g) centuries: Count innings where SUM(runs_batter) >= 100, grouped by (match_id, innings_num).
   h) fifties: Count innings where SUM(runs_batter) >= 50 AND < 100, grouped by (match_id, innings_num).
   i) highest_score: MAX of per-innings run totals grouped by (match_id, innings_num).

   EXAMPLE CTE PATTERN for Cricsheet batting stats:
   WITH innings_stats AS (
       SELECT d.batter, d.match_id, d.innings_num,
              SUM(d.runs_batter) as runs,
              COUNT(*) FILTER (WHERE d.extras_wides = 0) as balls
       FROM deliveries d
       JOIN matches m ON d.match_id = m.match_id
       WHERE [filters]
       GROUP BY d.batter, d.match_id, d.innings_num
   ),
   dismissals AS (
       SELECT w.player_out as batter, COUNT(*) as outs
       FROM wickets w
       JOIN matches m ON w.match_id = m.match_id
       WHERE [same filters on m] AND w.kind NOT IN ('retired hurt', 'retired out')
       GROUP BY w.player_out
   )
   SELECT i.batter,
       COUNT(DISTINCT (i.match_id, i.innings_num)) as innings,
       SUM(i.runs) as runs, SUM(i.balls) as balls_faced,
       COALESCE(d.outs, 0) as dismissals,
       COUNT(DISTINCT (i.match_id, i.innings_num)) - COALESCE(d.outs, 0) as not_outs,
       ROUND(SUM(i.runs) * 1.0 / NULLIF(d.outs, 0), 2) as batting_avg,
       ROUND(SUM(i.runs) * 100.0 / NULLIF(SUM(i.balls), 0), 2) as strike_rate,
       MAX(i.runs) as highest_score,
       COUNT(*) FILTER (WHERE i.runs >= 100) as centuries,
       COUNT(*) FILTER (WHERE i.runs >= 50 AND i.runs < 100) as fifties
   FROM innings_stats i
   LEFT JOIN dismissals d ON i.batter = d.batter
   GROUP BY i.batter, d.outs

   CRITICAL: centuries and fifties MUST use the innings_stats CTE column (i.runs which is already
   a per-innings SUM), NOT raw deliveries. The FILTER clause uses the pre-aggregated innings total.
   NEVER write COUNT(*) FILTER (WHERE SUM(...) >= 100) — nested aggregates are ILLEGAL in SQL.
   NEVER compute centuries/fifties/highest_score from the raw deliveries table in a single GROUP BY.
   You MUST first aggregate per-innings (GROUP BY batter, match_id, innings_num to get innings runs),
   then query THAT CTE for centuries (runs >= 100), fifties (runs >= 50 AND < 100), highest (MAX(runs)).
   WRONG: SELECT batter, COUNT(*) FILTER (WHERE runs_batter >= 100) FROM deliveries GROUP BY batter
   (This counts BALLS with runs_batter >= 100, which is always 0. A single ball never scores 100.)
   CORRECT: Use the innings_stats CTE above where runs = SUM(runs_batter) per innings.
   Similarly for fours and sixes:
   fours = SUM(CASE WHEN d.runs_batter = 4 AND (d.non_boundary IS NULL OR d.non_boundary = false) THEN 1 ELSE 0 END)
   sixes = SUM(CASE WHEN d.runs_batter = 6 AND (d.non_boundary IS NULL OR d.non_boundary = false) THEN 1 ELSE 0 END)

5. CRICSHEET BOWLING CALCULATIONS (deliveries table) — MANDATORY FORMULAS:
   a) legal_balls: COUNT(*) FILTER (WHERE extras_wides = 0 AND extras_noballs = 0).
   b) overs: legal_balls / 6 (display as FLOOR(legal_balls/6) || '.' || (legal_balls % 6)).
   c) runs_conceded: SUM(runs_total) — includes all runs off that bowler's deliveries.
   d) wickets: From WICKETS TABLE joined to deliveries on (match_id, innings_num, over_num, ball_num).
      Only count wickets WHERE kind NOT IN ('run out', 'retired hurt', 'obstructing the field', 'retired out').
      CRITICAL: The wickets table does NOT have a 'bowler' column. To find who bowled the wicket,
      you MUST join wickets → deliveries on (match_id, innings_num, over_num, ball_num) and use d.bowler.
   e) bowling_average: runs_conceded / NULLIF(wickets, 0).
   f) economy: (runs_conceded * 6.0) / NULLIF(legal_balls, 0).
   g) bowling_strike_rate: legal_balls * 1.0 / NULLIF(wickets, 0).

   EXAMPLE CTE PATTERN for Cricsheet bowling stats (e.g. top wicket takers):
   WITH bowling_agg AS (
       SELECT d.bowler, d.match_id, d.innings_num,
              COUNT(*) FILTER (WHERE d.extras_wides = 0 AND d.extras_noballs = 0) AS legal_balls,
              SUM(d.runs_total) AS runs_conceded
       FROM deliveries d
       JOIN matches m ON d.match_id = m.match_id
       WHERE [filters on m]
       GROUP BY d.bowler, d.match_id, d.innings_num
   ),
   bowling_wickets AS (
       SELECT d.bowler, d.match_id, d.innings_num, COUNT(*) AS wkts
       FROM wickets w
       JOIN deliveries d ON w.match_id = d.match_id AND w.innings_num = d.innings_num
           AND w.over_num = d.over_num AND w.ball_num = d.ball_num
       JOIN matches m ON w.match_id = m.match_id
       WHERE [same filters on m]
         AND w.kind NOT IN ('run out', 'retired hurt', 'obstructing the field', 'retired out')
       GROUP BY d.bowler, d.match_id, d.innings_num
   )
   SELECT ba.bowler,
       COUNT(DISTINCT ba.match_id) AS matches,
       COUNT(DISTINCT (ba.match_id, ba.innings_num)) AS innings_bowled,
       SUM(ba.legal_balls) AS total_legal_balls,
       SUM(ba.runs_conceded) AS total_runs,
       COALESCE(SUM(bw.wkts), 0) AS total_wickets,
       ROUND(SUM(ba.runs_conceded) * 1.0 / NULLIF(COALESCE(SUM(bw.wkts), 0), 0), 2) AS bowling_avg,
       ROUND(SUM(ba.runs_conceded) * 6.0 / NULLIF(SUM(ba.legal_balls), 0), 2) AS economy,
       ROUND(SUM(ba.legal_balls) * 1.0 / NULLIF(COALESCE(SUM(bw.wkts), 0), 0), 2) AS bowling_sr
   FROM bowling_agg ba
   LEFT JOIN bowling_wickets bw ON ba.bowler = bw.bowler
       AND ba.match_id = bw.match_id AND ba.innings_num = bw.innings_num
   GROUP BY ba.bowler
   ORDER BY total_wickets DESC

   WRONG: SELECT w.bowler FROM wickets w — wickets table has NO bowler column!
   WRONG: GROUP BY w.bowler — same error.
   CORRECT: JOIN wickets to deliveries, then use d.bowler for the bowler's name.

6. CRICSHEET FIELDING CALCULATIONS (wickets table) — MANDATORY FORMULAS:
   CRITICAL: fielder1 and fielder2 contain player DISPLAY NAMES (e.g. 'MS Dhoni'),
   NOT cricsheet_id values. NEVER join fielder1/fielder2 to players.cricsheet_id — it always returns 0 rows.
   Use fielder1 directly as the player name in SELECT and GROUP BY.

   a) catches: COUNT(*) WHERE kind = 'caught' — count by fielder1.
   b) stumpings: COUNT(*) WHERE kind = 'stumped' — fielder1 is always the wicketkeeper.
   c) run_outs: COUNT(*) WHERE kind = 'run out' — may involve fielder1 and/or fielder2.
   d) total_dismissals: catches + stumpings + run_outs for a given fielder.
   e) wicketkeeper identification: Stumpings are 100% keeper. For keeper catches, join
      fielder1 to players.name, then to player_profiles.playing_role ILIKE '%Wicketkeeper%'.
      Note this is a career role — approximate for per-match identification.

   EXAMPLE CTE PATTERN for fielding stats (e.g. most catches in IPL):
   WITH fielding AS (
       SELECT w.fielder1 AS fielder, COUNT(*) AS catches
       FROM wickets w
       JOIN matches m ON w.match_id = m.match_id
       WHERE m.event_name = 'Indian Premier League'
         AND w.kind = 'caught'
         AND w.fielder1 IS NOT NULL
       GROUP BY w.fielder1
   )
   SELECT fielder, catches
   FROM fielding
   ORDER BY catches DESC
   LIMIT 10

   EXAMPLE CTE PATTERN for total keeper dismissals:
   WITH keeper_actions AS (
       SELECT w.fielder1 AS keeper,
              COUNT(*) FILTER (WHERE w.kind = 'stumped') AS stumpings,
              COUNT(*) FILTER (WHERE w.kind = 'caught') AS catches
       FROM wickets w
       JOIN matches m ON w.match_id = m.match_id
       JOIN players p ON w.fielder1 = p.name
       JOIN player_profiles pp ON p.cricsheet_id = pp.cricsheet_id
       WHERE pp.playing_role ILIKE '%Wicketkeeper%'
         AND w.fielder1 IS NOT NULL
         AND w.kind IN ('stumped', 'caught')
         AND [format/event filters on m]
       GROUP BY w.fielder1
   )
   SELECT keeper, stumpings, catches, (stumpings + catches) AS total_dismissals
   FROM keeper_actions
   ORDER BY total_dismissals DESC

   WRONG: JOIN wickets w ON w.fielder1 = p.cricsheet_id — fielder1 is a NAME, not an ID!
   CORRECT: Use w.fielder1 directly, or JOIN w.fielder1 = p.name for player profile lookups.

7. Join tables as needed. The deliveries table is the main fact table for Cricsheet.
8. If the question is ambiguous, make reasonable assumptions and prefer international matches.
9. Use CTEs for complex queries to keep them readable.
10. FOLLOW-UP CONSISTENCY: When conversation history is provided, pay close attention to which
    tables and player name format were used in previous queries. If the context mentions
    "Cricsheet" or "deliveries", use the deliveries/matches/innings/wickets tables with
    Cricsheet player names (e.g. "V Kohli"). If the context mentions "Kaggle" or "scorecards",
    use kaggle_* tables with player_map for names. NEVER switch data sources mid-conversation
    unless the user explicitly asks for different data. If a previous SQL is provided in the
    context, use the same tables and joins as a starting point.
11. SMART STAT AUTO-INCLUDE — MANDATORY COLUMNS BY QUERY TYPE:
    When a fan asks for stats, they expect the FULL picture. Always include:
    a) "Top N run scorers / best batsmen / orange cap":
       → player, matches, innings, not_outs, runs, highest_score, batting_avg, strike_rate,
         centuries, fifties, fours, sixes
    b) "Top N wicket takers / best bowlers / purple cap":
       → player, matches, innings_bowled, overs, runs_conceded, wickets, bowling_avg,
         economy, bowling_sr, best_bowling_innings (BBI), five_wicket_hauls
    c) "Player career / overall stats / how has X performed":
       → Return BOTH batting AND bowling stats as SEPARATE QUERIES or combined with clear labels.
         BATTING: matches, innings, not_outs, runs, highest, avg, sr, 100s, 50s, 4s, 6s
         BOWLING: matches, innings_bowled, overs, runs, wickets, avg, economy, sr, bbi, 5w
         For an all-rounder, both sections matter equally.
    d) "Compare X vs Y":
       → Include ALL batting stats for both. If either is a bowler, include bowling stats too.
    Do NOT return just "player + runs" or "player + wickets" — that is useless without context.
11. PLAYER TYPE AWARENESS:
    Detect whether the query is about a BATSMAN, BOWLER, or ALL-ROUNDER based on context:
    - If the question mentions runs, centuries, batting, average, strike rate → focus on BATTING stats
    - If the question mentions wickets, bowling, economy, overs → focus on BOWLING stats
    - If the question is general ("career stats", "how has X performed") → include BOTH batting AND bowling
    - For "top N" queries, the metric (runs/wickets) determines the focus
    For career summaries of known all-rounders or when unsure, ALWAYS include both batting and bowling.
12. PLAYER NAMES: NEVER return raw player IDs, player_id, or numeric identifiers in results.
    For Kaggle data, ALWAYS JOIN to kaggle_players (using player_id) to get full names.
    NEVER use player_map.player_name for display — it has Cricsheet initials, not full names.
    For Cricsheet data, names are already strings. Every player reference in output columns
    must be a full name string, never a number or Cricsheet initials.
13. SCORECARD QUERIES: When the user asks for a match scorecard, match details, or innings
    breakdown, your SQL MUST include the "Match ID" (for Kaggle) or match_id (for Cricsheet)
    column in the SELECT. The system will use this ID to build the full scorecard automatically.
    Keep the SQL simple — just identify the match. Example:
    SELECT "Match ID", "Match Name" FROM kaggle_matches WHERE ...
14. PLAYER NAME MATCHING: When a user mentions a specific player by full name (e.g. "Steve Smith",
    "Virat Kohli"), use ILIKE with the FULL name, not just the surname.
    - Kaggle: player_name ILIKE '%Steven Smith%' or player_name ILIKE '%Steve Smith%'
    - Cricsheet: batter ILIKE '%SPD Smith%' for known Cricsheet-format names.
    - When matching a well-known player, try common name variants:
      "Steve Smith" → player_name ILIKE '%Steven Smith%' OR player_name ILIKE '%Steve Smith%'
      "Viv Richards" → player_name ILIKE '%Viv Richards%' OR player_name ILIKE '%Vivian Richards%'
    - NEVER match just a surname like '%Smith%' — this returns ALL players with that surname.
    - EXCEPTION: If the user gives ONLY a single surname (e.g. "Suryavanshi", "Bumrah") and
      NO [PLAYER FILTER] block is provided, use ILIKE '%Surname%' on the Cricsheet batter/bowler
      columns. This is safer than exact match because Cricsheet names use initials (e.g. 'V Suryavanshi',
      'JJ Bumrah'). For Kaggle, use a subquery: WHERE batsman IN (SELECT player_id FROM kaggle_players WHERE player_name ILIKE '%Surname%').
    - For Kaggle data, ALWAYS filter via a subquery on kaggle_players:
      WHERE batsman IN (SELECT player_id FROM kaggle_players WHERE player_name ILIKE '%Steven Smith%')
15. FOLLOW-UP QUERIES: When conversation history mentions a player (e.g. "he", "that player",
    "his career"), look at the CONTEXT_SUMMARY and previous SQL to identify WHO the user means.
    Extract the FULL player name from context (e.g. "Sachin Tendulkar" not "SR Tendulkar")
    and use it with ILIKE on kaggle_players.player_name. Use the SAME data source (Kaggle/Cricsheet)
    as the previous query. If context contains a Cricsheet-format name like "SR Tendulkar",
    resolve it to the full name using: SELECT player_name FROM kaggle_players WHERE player_id IN
    (SELECT kaggle_player_id FROM player_map WHERE cricsheet_name ILIKE '%SR Tendulkar%').
16. COLUMN NAMING: Use clear, descriptive column aliases that indicate batting vs bowling:
    - Batting: batting_avg, strike_rate, centuries, fifties, highest_score, not_outs, fours, sixes
    - Bowling: bowling_avg, economy, bowling_sr, wickets, overs_bowled, runs_conceded, five_wickets
    This helps the frontend correctly segregate batting and bowling stats.
17. PLAYER ATTRIBUTES (batting_style, bowling_style, playing_role, country, debut_year, dob):
    The BEST source for player attributes is the player_profiles table — it has 17K+ players
    enriched from ESPN Cricinfo with batting_style, bowling_style, playing_role, country, and more.
    For Cricsheet queries (deliveries-based, IPL, ODI, T20):
      JOIN players p ON d.batter = p.name
      JOIN player_profiles pp ON p.cricsheet_id = pp.cricsheet_id
    Then filter: pp.batting_style ILIKE '%left%' or pp.bowling_style ILIKE '%medium%'
    Or pp.playing_role ILIKE '%allrounder%' or pp.country = 'India' or pp.debut_year >= 2015
    Values: batting_style = 'Right hand Bat' | 'Left hand Bat'
            bowling_style = 'Right-arm medium' | 'Right-arm offbreak' | 'Legbreak' | 'Slow left-arm orthodox' | etc.
            playing_role = 'Top order Batter' | 'Allrounder' | 'Bowler' | 'Wicketkeeper Batter' | 'Batting Allrounder' | 'Bowling Allrounder' | etc.
    For Kaggle queries, you can ALSO use kaggle_players (has batting_style/bowling_style), but
    player_profiles has richer data (playing_role, country, debut_year, major_teams).
    Cross-reference: kaggle_players via player_map → cricsheet_id → player_profiles
    NEVER reference batting_style or bowling_style on deliveries, matches, wickets, or player_map tables.
18. PREDICTION / FORECASTING QUERIES: This rule applies ONLY when the user explicitly uses words
    like "predict", "forecast", "project", or "what will" about a FUTURE outcome.
    It does NOT apply to simple average/mean questions like "what's the avg score", "average batting first",
    "how many runs on average" — those are straightforward aggregation queries, just use AVG().
    When this rule DOES apply, use DuckDB's statistical functions for a richer analysis:
    SELECT ROUND(AVG(final_score), 2) as predicted_score,
           ROUND(STDDEV_SAMP(final_score), 2) as std_dev,
           ROUND(AVG(final_score) - 1.96 * STDDEV_SAMP(final_score) / SQRT(COUNT(*)), 2) as low_95_ci,
           ROUND(AVG(final_score) + 1.96 * STDDEV_SAMP(final_score) / SQRT(COUNT(*)), 2) as high_95_ci,
           ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY final_score), 2) as q1,
           ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY final_score), 2) as median,
           ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY final_score), 2) as q3,
           MIN(final_score) as min_score, MAX(final_score) as max_score,
           COUNT(*) as sample_size
19. CONVERSATIONAL / EXPLANATORY QUESTIONS: If the user asks "why", "explain", "how come", or a
    non-data question referencing previous results, you MUST still return valid SQL. If no SQL is
    needed, return the SQL from the previous context (or a simple SELECT 1) — NEVER return prose text.
    The system handles narrative explanations separately.
20. RESOLVED PLAYER FILTERS: When the question includes a [PLAYER FILTER] block, you MUST
    copy the provided WHERE clause EXACTLY into your SQL. These contain pre-resolved unique IDs.
    - kaggle_player_id: Use for kaggle_batting.batsman, kaggle_bowling."bowler id", etc.
    - cricsheet_name: Use for deliveries.batter, deliveries.bowler, wickets.player_out, etc.
        - Any context-only lines such as team, role, or nationality are disambiguation hints only.
            Do NOT turn them into SQL filters unless the user explicitly asks for that team or scope.
    Example [PLAYER FILTER] block:
      Kaggle filter: batsman = 10406
      Cricsheet filter: batter = 'GC Smith'
    Your SQL MUST use whichever filter matches the tables you choose. NEVER use ILIKE name
    matching when a [PLAYER FILTER] block is present — use the exact filter provided.
    This is MANDATORY. Ignoring the filter will produce wrong results.
21. COLUMN QUALIFICATION: When JOINing multiple tables, ALWAYS qualify every column reference
    with a table alias (e.g. d.match_id, i.batting_team, m.event_name). NEVER write bare
    column names like "SELECT match_id" or "WHERE innings_num = 1" when multiple tables share
    that column. DuckDB will raise "Ambiguous reference" if you forget. Common shared columns:
    match_id (deliveries, innings, matches, wickets), innings_num (deliveries, innings, wickets).
22. AGGREGATES IN WHERE vs HAVING: NEVER put aggregate functions (SUM, COUNT, AVG, MIN, MAX)
    directly in a WHERE clause — this is illegal SQL. Use one of these patterns instead:
    a) HAVING clause: GROUP BY ... HAVING SUM(runs) >= 140
    b) CTE / subquery: Compute the aggregate in a CTE, then filter in the outer WHERE.
    Example — WRONG:  WHERE SUM(d.runs_total) >= 51
    Example — CORRECT: Use a CTE: WITH team_runs AS (SELECT match_id, SUM(runs_total) as total ...) SELECT ... FROM team_runs WHERE total >= 51
23. CHASE / WIN-PROBABILITY QUERIES — MANDATORY: When the user asks about "chances of winning",
    "can X chase", "probability of winning", or any second-innings win/loss scenario with a known target:
    ⚠ CRITICAL: You MUST use a target RANGE, NEVER an exact match. Write:
       i.target_runs BETWEEN (target - 15) AND (target + 15)
    Do NOT write i.target_runs = <number>. Exact target matches return 0 rows because
    the same target almost never recurs. A ±15 range gives a statistically meaningful sample.
    Also: filter for the SPECIFIC CHASING TEAM using i.batting_team (the team batting second).
    Use this EXACT CTE pattern:
    WITH second_innings AS (
        SELECT m.match_id, i.batting_team, i.target_runs,
               SUM(d.runs_total) as chase_total
        FROM matches m
        JOIN innings i ON m.match_id = i.match_id AND i.innings_num = 2 AND i.super_over = 0
        JOIN deliveries d ON m.match_id = d.match_id AND d.innings_num = 2
        WHERE i.target_runs BETWEEN <target-15> AND <target+15>
          AND i.batting_team = '<chasing_team>'
        GROUP BY m.match_id, i.batting_team, i.target_runs
    )
    SELECT COUNT(*) as total_chases,
           SUM(CASE WHEN chase_total >= target_runs THEN 1 ELSE 0 END) as successful,
           ROUND(100.0 * SUM(CASE WHEN chase_total >= target_runs THEN 1 ELSE 0 END) / COUNT(*), 1) as win_pct
    FROM second_innings
"""

# NOTE: SQL_SYSTEM_PROMPT is a static base. Knowledge facts are injected at runtime
# in _generate_sql() to pick up live changes without restart.""

NARRATIVE_SYSTEM_PROMPT = """You are a knowledgeable cricket statistician and analyst. Given a user's question and the data results from our database, provide a clear, insightful response.

DATA SOURCES:
- Test match scorecard data (kaggle_* tables): COMPLETE from 1877 to present — covers all-time records accurately.
- Ball-by-ball data (Cricsheet deliveries table): From ~2003 onwards, ALL formats (Tests, ODIs, T20s, IPL, etc.).
- For Test scorecard stats, the data is comprehensive and accurate for all-time records.
- For ball-by-ball analysis or ODI/T20/franchise stats, note the data is from ~2003 onwards only.

RESPONSE FORMAT — CRITICAL:
1. Write PLAIN PROSE ONLY. NO markdown formatting of any kind.
   - Do NOT use tables (no | pipes), no bullet points (no - or *), no headers (no # or ##).
   - Do NOT use **bold** or *italic* markdown syntax.
   - Do NOT try to format data as text tables. The data will be displayed separately by the UI.
2. Write 2-4 sentences of natural, flowing analysis. Think sports commentator, not data dump.
3. Focus on INSIGHT: context, records, comparisons, trends, what makes this interesting.
4. Format numbers naturally in prose: "averaged 42.38 at a strike rate of 57" not "avg: 42.38, SR: 57.04".
5. If data is empty or insufficient, say so honestly and suggest alternatives.
6. CHART CONFIG: If the data suits a chart AND has MULTIPLE rows, output on a NEW LINE:
   CHART_CONFIG:{"type":"bar","title":"Chart Title","x_field":"column_name","y_field":"column_name"}
   Supported types: bar, line, pie, doughnut, grouped_bar, scatter, horizontalBar.
   - x_field and y_field must exactly match SQL result column names.
   - For grouped_bar, use "y_fields": ["col1","col2",...] instead of y_field.
   - Pie/doughnut for <=8 items, bar for ranked lists, line for time series.
   - NEVER output CHART_CONFIG when results have only 1 row. It makes no sense to chart a single data point.
   - NEVER output CHART_CONFIG for errors or empty results.
7. CONTEXT SUMMARY: Always output on a NEW LINE:
   CONTEXT_SUMMARY:concise ~15 word summary including which data source was used
   IMPORTANT: Always mention the data source used (Cricsheet deliveries OR Kaggle scorecards) so
   follow-up queries can use the same tables and player name format.
   Example: CONTEXT_SUMMARY:Kohli 4th-innings Test batting via Cricsheet deliveries, 37 innings, avg 42.38
8. NEW FACTS: If you discover a cricket anomaly or important contextual fact, output on a NEW LINE:
   NEW_FACT:concise factual statement (max 200 chars)
   Only for genuine domain knowledge, NOT routine stats.
9. DISPLAY HINT: Based on the nature of the query and data, ALWAYS output on a NEW LINE:
   DISPLAY_HINT:{"format":"<type>","stat_type":"<batting|bowling|allround|team|match>"}
   Supported formats:
   - "scorecard": When user asks for a match scorecard, full innings details, or match summary.
   - "stats": Default for player stats, aggregated performance data, single-row results.
   - "table": Ranked lists, leaderboards, multi-row tabular data.
   - "comparison": Head-to-head or side-by-side player/team comparisons.
   stat_type is MANDATORY and tells the frontend what kind of data this is:
   - "batting": Query is about batting stats (runs, avg, SR, centuries, etc.)
   - "bowling": Query is about bowling stats (wickets, economy, avg, etc.)
   - "allround": Query includes BOTH batting and bowling stats (career summary, all-rounder)
   - "team": Team-level stats (wins, losses, margins)
   - "match": Match-level data (results, venues, toss)
   Always output exactly one DISPLAY_HINT line. Default to "stats" for single-row, "table" for multi-row.
10. DATA ACCURACY GUARDRAIL: Only state statistical facts that are present in the query results.
    If you supplement with knowledge from your training data (e.g. career context, records held,
    historical significance), clearly prefix it with "Note:" so the user knows it comes from
    general knowledge rather than the database. Never invent statistics not in the results.
"""

# NOTE: NARRATIVE_SYSTEM_PROMPT is a static base. Knowledge facts are injected at runtime.


class CricketQueryEngine:
    """Translates natural-language queries to SQL via GPT-4.1, executes, and narrates."""

    def __init__(self, model: str = DEFAULT_MODEL, db_path: str = DB_PATH):
        self.model = model
        self.model_chain = [model] + FALLBACK_MODELS
        self.db_path = db_path
        self.cache_path = CACHE_PATH
        self.client = OpenAI(
            base_url="https://models.inference.ai.azure.com",
            api_key=GITHUB_TOKEN,
        )
        self._last_model_used: str | None = None
        self._espn_team_name_cache: dict[str, str | None] = {}
        self._espn_test_summary_cache: dict[str, dict[str, dict] | None] = {}
        self._init_cache()
        self._wasp_tables: dict[str, dict[tuple[int, int], float]] = {}
        self._wasp_meta: dict[str, dict] = {}
        self._build_wasp_tables()

    # ── WASP pre-computation ────────────────────────────────────────────

    def _build_wasp_tables(self) -> None:
        """Pre-compute V(b,w) lookup tables from historical ball-by-ball data.

        V(b,w) = average additional runs scored from ball index b with w wickets
        lost to the end of the first innings.  Based on WASP methodology
        (Brooker & Hogan, University of Canterbury).

        Tables built for: T20 (all), IPL, ODI.
        """
        import logging
        log = logging.getLogger(__name__)

        configs = [
            ("T20", "m.match_type = 'T20'", 120),
            ("IPL", "m.event_name LIKE '%Indian Premier League%'", 120),
            ("ODI", "m.match_type = 'ODI'", 300),
        ]

        try:
            con = duckdb.connect(self.db_path, read_only=True)
        except Exception as exc:
            log.warning("WASP: cannot open DB for pre-computation: %s", exc)
            return

        for label, where_filter, max_balls in configs:
            try:
                rows = con.execute(f"""
                    WITH wicket_balls AS (
                        SELECT match_id, innings_num, over_num, ball_num
                        FROM wickets
                    ),
                    ball_state AS (
                        SELECT
                            d.match_id, d.innings_num, d.over_num, d.ball_num,
                            d.runs_total,
                            CASE WHEN w.match_id IS NOT NULL THEN 1 ELSE 0 END AS is_wkt,
                            (d.over_num * 6 + d.ball_num - 1) AS ball_idx
                        FROM deliveries d
                        JOIN matches m ON d.match_id = m.match_id
                        LEFT JOIN wicket_balls w
                            ON d.match_id = w.match_id
                            AND d.innings_num = w.innings_num
                            AND d.over_num = w.over_num
                            AND d.ball_num = w.ball_num
                        WHERE {where_filter}
                          AND d.innings_num = 1
                    ),
                    ball_with_wickets AS (
                        SELECT *,
                            COALESCE(SUM(is_wkt) OVER (
                                PARTITION BY match_id, innings_num
                                ORDER BY over_num, ball_num
                                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                            ), 0) AS wickets_before
                        FROM ball_state
                    ),
                    innings_totals AS (
                        SELECT match_id, innings_num, SUM(runs_total) AS total_runs
                        FROM ball_state
                        GROUP BY match_id, innings_num
                    ),
                    combined AS (
                        SELECT
                            b.ball_idx,
                            b.wickets_before,
                            t.total_runs - (SUM(b.runs_total) OVER (
                                PARTITION BY b.match_id, b.innings_num
                                ORDER BY b.over_num, b.ball_num
                            )) AS runs_remaining,
                            b.runs_total,
                            b.is_wkt
                        FROM ball_with_wickets b
                        JOIN innings_totals t
                            ON b.match_id = t.match_id AND b.innings_num = t.innings_num
                    )
                    SELECT
                        ball_idx,
                        wickets_before,
                        ROUND(AVG(runs_remaining + runs_total), 2) AS v_bw,
                        COUNT(*) AS n
                    FROM combined
                    WHERE ball_idx >= 0 AND ball_idx < {max_balls} AND wickets_before < 10
                    GROUP BY ball_idx, wickets_before
                    ORDER BY ball_idx, wickets_before
                """).fetchall()

                table: dict[tuple[int, int], float] = {}
                n_table: dict[tuple[int, int], int] = {}

                for ball_idx, w, v_bw, n in rows:
                    table[(ball_idx, w)] = float(v_bw)
                    n_table[(ball_idx, w)] = int(n)

                self._wasp_tables[label] = table
                self._wasp_meta[label] = {
                    "max_balls": max_balls,
                    "n": n_table,
                    "total_cells": len(table),
                }
                log.info("WASP[%s]: loaded %d cells from historical data", label, len(table))

            except Exception as exc:
                log.warning("WASP[%s]: failed to build table: %s", label, exc)

        con.close()

    def _wasp_predict(self, ball_idx: int, wickets: int, label: str) -> tuple[float | None, int]:
        """Look up V(b,w) from the pre-computed WASP table.

        Returns (expected_additional_runs, sample_size) or (None, 0).
        """
        table = self._wasp_tables.get(label)
        n_table = self._wasp_meta.get(label, {}).get("n", {})
        if not table:
            return None, 0

        v = table.get((ball_idx, wickets))
        n = n_table.get((ball_idx, wickets), 0)
        if v is not None:
            return v, n

        # Interpolate from neighboring cells for sparse regions
        candidates = []
        for db in range(-2, 3):
            for dw in range(-1, 2):
                neighbor = table.get((ball_idx + db, wickets + dw))
                if neighbor is not None:
                    weight = 1.0 / (1.0 + abs(db) + abs(dw) * 3)
                    nn = n_table.get((ball_idx + db, wickets + dw), 1)
                    candidates.append((neighbor, weight, nn))
        if candidates:
            total_w = sum(w for _, w, _ in candidates)
            interp_v = sum(v * w for v, w, _ in candidates) / total_w
            interp_n = sum(nn for _, _, nn in candidates)
            return interp_v, interp_n
        return None, 0

    def _call_llm(self, messages: list[dict], temperature: float = 0.1) -> str:
        """Call LLM walking the model chain, skipping rate-limited models."""
        global _rate_limit_until
        now = time.time()
        last_error = None

        for model in self.model_chain:
            # Skip models we already know are rate-limited
            if model in _rate_limit_until and now < _rate_limit_until[model]:
                continue

            try:
                raw_response = self.client.chat.completions.with_raw_response.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                )
                response = raw_response.parse()
                self._last_model_used = model
                # Increment global LLM call counter
                global _llm_call_count
                _llm_call_count += 1
                # Capture rate limit headers from raw HTTP response
                try:
                    hdrs = raw_response.headers
                    remaining = hdrs.get("x-ratelimit-remaining-requests")
                    limit = hdrs.get("x-ratelimit-limit-requests")
                    if remaining is not None:
                        _rate_limit_remaining[model] = {
                            "remaining": int(remaining),
                            "limit": int(limit) if limit else -1,
                            "reset": hdrs.get("x-ratelimit-reset-requests", ""),
                        }
                except (ValueError, TypeError, AttributeError):
                    pass
                return response.choices[0].message.content.strip()
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RateLimitReached" in err_str or "rate" in err_str.lower():
                    # Parse wait time from error if available (e.g. "Please wait 75494 seconds")
                    wait_match = _re.search(r'wait\s+(\d+)\s+seconds', err_str)
                    wait_secs = int(wait_match.group(1)) if wait_match else 86400
                    _rate_limit_until[model] = now + wait_secs
                    print(f"Rate limited on {model} (wait {wait_secs}s), trying next model...")
                    last_error = e
                    continue
                raise

        raise last_error or Exception("All models rate limited")

    # ── Function-calling tools for the LLM ──────────────────────────────

    _WASP_TOOLS = [
        {
            "type": "function",
            "function": {
                "name": "wasp_predict_score",
                "description": (
                    "Predict the final first-innings score using the WASP model "
                    "(Winning and Score Predictor). Call this when the user asks to predict, "
                    "forecast, or estimate a batting team's final score given their current "
                    "match state (score, wickets, overs). Works for T20, IPL, ODI."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "current_score": {"type": "integer", "description": "Runs scored so far"},
                        "wickets": {"type": "integer", "description": "Wickets lost (0-9)"},
                        "overs": {"type": "number", "description": "Overs completed (e.g. 14.3 means 14 overs and 3 balls)"},
                        "format": {
                            "type": "string",
                            "enum": ["ipl", "t20", "odi"],
                            "description": "Match format. Use 'ipl' for IPL/franchise T20, 't20' for international T20, 'odi' for 50-over matches.",
                        },
                        "team": {"type": "string", "description": "Batting team name (optional, for context)"},
                    },
                    "required": ["current_score", "wickets", "overs", "format"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "wasp_win_probability",
                "description": (
                    "Calculate the probability that the chasing team will win, given the "
                    "current second-innings match state. Uses the WASP model to estimate "
                    "expected additional runs and compares to the target. Call this when the "
                    "user asks about chances of winning, can a team chase a target, win "
                    "probability, or likelihood of a successful chase."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "integer", "description": "Target score to chase (runs needed to win)"},
                        "current_score": {"type": "integer", "description": "Chasing team's current score"},
                        "wickets": {"type": "integer", "description": "Wickets lost by chasing team (0-9)"},
                        "overs": {"type": "number", "description": "Overs completed in the chase (e.g. 12.0)"},
                        "format": {
                            "type": "string",
                            "enum": ["ipl", "t20", "odi"],
                            "description": "Match format",
                        },
                        "team": {"type": "string", "description": "Chasing team name (optional)"},
                    },
                    "required": ["target", "current_score", "wickets", "overs", "format"],
                },
            },
        },
    ]

    def _call_llm_with_tools(self, messages: list[dict], temperature: float = 0.1) -> object:
        """Call LLM with function-calling tools. Returns the full message object
        (which may contain a tool_calls list or just content)."""
        global _rate_limit_until, _llm_call_count
        now = time.time()
        last_error = None

        for model in self.model_chain:
            if model in _rate_limit_until and now < _rate_limit_until[model]:
                continue
            try:
                raw_response = self.client.chat.completions.with_raw_response.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    tools=self._WASP_TOOLS,
                    tool_choice="auto",
                )
                response = raw_response.parse()
                self._last_model_used = model
                _llm_call_count += 1
                try:
                    hdrs = raw_response.headers
                    remaining = hdrs.get("x-ratelimit-remaining-requests")
                    limit = hdrs.get("x-ratelimit-limit-requests")
                    if remaining is not None:
                        _rate_limit_remaining[model] = {
                            "remaining": int(remaining),
                            "limit": int(limit) if limit else -1,
                            "reset": hdrs.get("x-ratelimit-reset-requests", ""),
                        }
                except (ValueError, TypeError, AttributeError):
                    pass
                return response.choices[0].message
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RateLimitReached" in err_str or "rate" in err_str.lower():
                    wait_match = _re.search(r'wait\s+(\d+)\s+seconds', err_str)
                    wait_secs = int(wait_match.group(1)) if wait_match else 86400
                    _rate_limit_until[model] = now + wait_secs
                    last_error = e
                    continue
                raise
        raise last_error or Exception("All models rate limited")

    def _execute_tool_call(self, name: str, args: dict) -> dict:
        """Execute a WASP tool call and return structured result data."""
        if name == "wasp_predict_score":
            return self._tool_predict_score(**args)
        elif name == "wasp_win_probability":
            return self._tool_win_probability(**args)
        return {"error": f"Unknown tool: {name}"}

    def _tool_predict_score(self, current_score: int, wickets: int, overs: float,
                            format: str, team: str = None) -> dict:
        """WASP first-innings score prediction tool."""
        fmt = format.lower()
        wasp_label = {"ipl": "IPL", "odi": "ODI"}.get(fmt, "T20")
        total_overs = 50 if fmt == "odi" else 20

        whole = int(overs)
        part = round((overs - whole) * 10)
        ball_idx = whole * 6 + part

        v_now, n_now = self._wasp_predict(ball_idx, wickets, wasp_label)
        if v_now is None and wasp_label != "T20" and fmt != "odi":
            wasp_label = "T20"
            v_now, n_now = self._wasp_predict(ball_idx, wickets, wasp_label)
        if v_now is None:
            return {"error": "Insufficient data for this match state"}

        predicted = round(current_score + v_now)
        v_opt, _ = self._wasp_predict(ball_idx, max(0, wickets - 1), wasp_label)
        v_pes, _ = self._wasp_predict(ball_idx, min(9, wickets + 1), wasp_label)
        high = round(current_score + v_opt) if v_opt else predicted
        low = round(current_score + v_pes) if v_pes else predicted

        rr = current_score / overs if overs > 0 else 0
        naive = round(rr * total_overs)

        v_start, _ = self._wasp_predict(0, 0, wasp_label)
        resource_pct = round((1 - v_now / v_start) * 100, 1) if v_start and v_start > 0 else None

        return {
            "tool": "wasp_predict_score",
            "model": f"wasp-{wasp_label.lower()}",
            "current_score": current_score, "wickets": wickets,
            "overs": overs, "ball_index": ball_idx,
            "format": wasp_label, "team": team,
            "predicted_total": predicted,
            "likely_range": [low, high],
            "naive_extrapolation": naive,
            "wasp_additional_runs": round(v_now, 1),
            "resource_used_pct": resource_pct,
            "data_points": n_now,
            "total_overs": total_overs,
        }

    def _tool_win_probability(self, target: int, current_score: int, wickets: int,
                              overs: float, format: str, team: str = None) -> dict:
        """WASP second-innings win probability tool."""
        fmt = format.lower()
        wasp_label = {"ipl": "IPL", "odi": "ODI"}.get(fmt, "T20")
        total_overs = 50 if fmt == "odi" else 20

        whole = int(overs)
        part = round((overs - whole) * 10)
        ball_idx = whole * 6 + part

        runs_needed = target - current_score

        v_now, n_now = self._wasp_predict(ball_idx, wickets, wasp_label)
        if v_now is None and wasp_label != "T20" and fmt != "odi":
            wasp_label = "T20"
            v_now, n_now = self._wasp_predict(ball_idx, wickets, wasp_label)
        if v_now is None:
            return {"error": "Insufficient data for this match state"}

        expected_additional = v_now
        predicted_total = current_score + expected_additional

        # Win probability via comparison of expected runs vs required
        # Using the spread across wicket scenarios as a proxy for standard deviation
        v_opt, _ = self._wasp_predict(ball_idx, max(0, wickets - 1), wasp_label)
        v_pes, _ = self._wasp_predict(ball_idx, min(9, wickets + 1), wasp_label)
        v_opt = v_opt if v_opt is not None else expected_additional
        v_pes = v_pes if v_pes is not None else expected_additional

        # Estimate std dev from the optimistic-pessimistic spread
        # More overs remaining → more variance in outcomes
        spread = abs(v_opt - v_pes)
        overs_remaining = total_overs - overs
        # Base sigma from wicket-spread, scaled by sqrt of remaining overs
        # In T20, typical std dev of remaining runs ≈ 15-35 depending on state
        import math
        sigma = max(spread, 10.0) * max(1.0, math.sqrt(overs_remaining / 5.0))

        # Normal CDF approximation for P(score >= target)
        z = (expected_additional - runs_needed) / sigma
        # Rational approximation of standard normal CDF
        import math
        if z >= 0:
            t = 1.0 / (1.0 + 0.2316419 * z)
            poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
            win_prob = 1.0 - poly * math.exp(-z * z / 2.0) / math.sqrt(2.0 * math.pi)
        else:
            t = 1.0 / (1.0 - 0.2316419 * z)
            poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
            win_prob = poly * math.exp(-z * z / 2.0) / math.sqrt(2.0 * math.pi)

        win_pct = round(max(1.0, min(99.0, win_prob * 100)), 1)

        rr_needed = runs_needed / max(total_overs - overs, 0.1) * 6 / 6
        rr_current = current_score / overs if overs > 0 else 0

        return {
            "tool": "wasp_win_probability",
            "model": f"wasp-{wasp_label.lower()}",
            "target": target, "current_score": current_score,
            "wickets": wickets, "overs": overs } | {
            "format": wasp_label, "team": team,
            "runs_needed": runs_needed,
            "expected_additional_runs": round(expected_additional, 1),
            "predicted_chase_total": round(predicted_total),
            "win_probability_pct": win_pct,
            "required_run_rate": round(rr_needed, 2),
            "current_run_rate": round(rr_current, 2),
            "likely_additional_range": [round(v_pes, 1), round(v_opt, 1)],
            "data_points": n_now,
        }

    def _build_tool_result_response(self, question: str, tool_name: str,
                                     tool_args: dict, tool_result: dict,
                                     history: list[dict] | None = None) -> dict:
        """Let the LLM generate a narrative from the tool result."""
        if tool_result.get("error"):
            return {
                "question": question, "sql": None, "columns": [], "rows": [],
                "answer": tool_result["error"], "error": tool_result["error"],
                "chart_config": None, "context_summary": None, "new_fact": None,
                "display_hint": None, "sections": None, "model_used": None,
            }

        # Build a follow-up message for the LLM to narrate
        tool_json = json.dumps(tool_result, indent=2)
        messages = [
            {"role": "system", "content": self._get_narrative_prompt()},
            {"role": "user", "content": question},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_wasp",
                    "type": "function",
                    "function": {"name": tool_name, "arguments": json.dumps(tool_args)},
                }],
            },
            {
                "role": "tool",
                "tool_call_id": "call_wasp",
                "content": tool_json,
            },
        ]
        narrative_raw = self._call_llm(messages, temperature=0.3)

        # Parse narrative components (same as _generate_narrative)
        chart_config = None
        context_summary = None
        new_fact = None
        display_hint = None

        lines = narrative_raw.split("\n")
        narrative_lines = []
        for line in lines:
            if line.startswith("CHART_CONFIG:"):
                try:
                    chart_config = json.loads(line[len("CHART_CONFIG:"):])
                except Exception:
                    pass
            elif line.startswith("CONTEXT_SUMMARY:"):
                context_summary = line[len("CONTEXT_SUMMARY:"):]
            elif line.startswith("NEW_FACT:"):
                new_fact = line[len("NEW_FACT:"):]
            elif line.startswith("DISPLAY_HINT:"):
                try:
                    display_hint = json.loads(line[len("DISPLAY_HINT:"):])
                except Exception:
                    pass
            else:
                narrative_lines.append(line)
        narrative = "\n".join(narrative_lines).strip()
        if not display_hint:
            display_hint = {"format": "prediction", "stat_type": "team"}

        # Build display rows from tool result
        model_used = tool_result.get("model", self._last_model_used)
        if tool_name == "wasp_predict_score":
            columns = ["predicted_total", "likely_low", "likely_high",
                        "naive_extrapolation", "wasp_additional", "data_points"]
            rows = [[
                tool_result["predicted_total"],
                tool_result["likely_range"][0],
                tool_result["likely_range"][1],
                tool_result["naive_extrapolation"],
                tool_result["wasp_additional_runs"],
                tool_result["data_points"],
            ]]
        elif tool_name == "wasp_win_probability":
            columns = ["win_probability_pct", "predicted_chase_total", "runs_needed",
                        "required_run_rate", "current_run_rate", "data_points"]
            rows = [[
                tool_result["win_probability_pct"],
                tool_result["predicted_chase_total"],
                tool_result["runs_needed"],
                tool_result["required_run_rate"],
                tool_result["current_run_rate"],
                tool_result["data_points"],
            ]]
        else:
            columns = list(tool_result.keys())
            rows = [list(tool_result.values())]

        return {
            "question": question,
            "sql": f"-- {tool_name}({json.dumps(tool_args)})",
            "columns": columns,
            "rows": rows,
            "answer": narrative,
            "error": None,
            "chart_config": chart_config,
            "context_summary": context_summary or f"{tool_name} prediction",
            "new_fact": new_fact,
            "display_hint": display_hint,
            "sections": None,
            "model_used": model_used,
        }

    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(self.db_path, read_only=True)

    def _extract_espn_athlete_id(self, headshot_url: str | None) -> str | None:
        if not headshot_url:
            return None
        match = _re.search(r"/(\d+)\.(?:png|jpe?g|webp)(?:\?|$)", headshot_url)
        return match.group(1) if match else None

    def _fetch_json_url(self, url: str) -> dict | None:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.load(resp)
        except Exception:
            return None

    def _resolve_espn_team_name(self, ref_url: str) -> str | None:
        cached = self._espn_team_name_cache.get(ref_url)
        if ref_url in self._espn_team_name_cache:
            return cached
        data = self._fetch_json_url(ref_url)
        name = None
        if data:
            name = data.get("displayName") or data.get("name") or data.get("abbreviation")
        self._espn_team_name_cache[ref_url] = name
        return name

    def _format_major_teams(self, raw_major_teams) -> str | None:
        if not raw_major_teams:
            return None
        if isinstance(raw_major_teams, list):
            team_items = raw_major_teams
        elif isinstance(raw_major_teams, str):
            text = raw_major_teams.strip()
            if not text:
                return None
            if "$ref" not in text:
                return " ".join(text.split())
            try:
                team_items = ast.literal_eval(text)
            except Exception:
                return None
        else:
            return None

        if not isinstance(team_items, list):
            return None

        names: list[str] = []
        for item in team_items:
            if isinstance(item, str):
                clean = " ".join(item.split())
                if clean and clean not in names:
                    names.append(clean)
                continue
            if not isinstance(item, dict):
                continue
            if item.get("name"):
                name = str(item["name"]).strip()
            elif item.get("$ref"):
                name = self._resolve_espn_team_name(item["$ref"])
            else:
                name = None
            if name and name not in names:
                names.append(name)
        return ", ".join(names) if names else None

    def _fetch_espn_statsguru_overall_row(self, athlete_id: str, match_class: int, stat_type: str) -> list[str] | None:
        url = f"https://stats.espncricinfo.com/ci/engine/player/{athlete_id}.html?class={match_class};template=results;type={stat_type}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                text = resp.read().decode("utf-8", errors="ignore")
        except Exception:
            return None

        match = _re.search(r"overall</td>(.*?)</tr>", text, _re.IGNORECASE | _re.DOTALL)
        if not match:
            return None

        values: list[str] = []
        for cell in _re.findall(r"<td[^>]*>(.*?)</td>", match.group(1), _re.IGNORECASE | _re.DOTALL):
            plain = _re.sub(r"<[^>]+>", "", cell)
            plain = _html.unescape(plain).replace("\xa0", " ").strip()
            values.append(plain)
        return values or None

    def _fetch_espn_test_career_summary(self, athlete_id: str | None) -> dict[str, dict] | None:
        if not athlete_id:
            return None
        if athlete_id in self._espn_test_summary_cache:
            return self._espn_test_summary_cache[athlete_id]

        def to_int(value: str | None) -> int | None:
            if value in (None, "", "-"):
                return None
            cleaned = value.replace(",", "").replace("*", "").strip()
            try:
                return int(float(cleaned))
            except Exception:
                return None

        def to_float(value: str | None) -> float | None:
            if value in (None, "", "-"):
                return None
            cleaned = value.replace(",", "").replace("*", "").strip()
            try:
                return float(cleaned)
            except Exception:
                return None

        summary: dict[str, dict] = {}
        batting_row = self._fetch_espn_statsguru_overall_row(athlete_id, 1, "batting")
        if batting_row and len(batting_row) >= 10:
            summary["batting"] = {
                "matches": to_int(batting_row[1]),
                "innings": to_int(batting_row[2]),
                "not_outs": to_int(batting_row[3]),
                "runs": to_int(batting_row[4]),
                "highest_score": to_int(batting_row[5]),
                "batting_avg": to_float(batting_row[6]),
                "centuries": to_int(batting_row[7]),
                "fifties": to_int(batting_row[8]),
            }

        bowling_row = self._fetch_espn_statsguru_overall_row(athlete_id, 1, "bowling")
        if bowling_row and len(bowling_row) >= 14:
            summary["bowling"] = {
                "matches": to_int(bowling_row[1]),
                "innings": to_int(bowling_row[2]),
                "overs": bowling_row[3] or None,
                "runs_conceded": to_int(bowling_row[5]),
                "wickets": to_int(bowling_row[6]),
                "best_innings_wickets": to_int((bowling_row[7] or "").split("/")[0]),
                "bowling_avg": to_float(bowling_row[9]),
                "economy": to_float(bowling_row[10]),
                "bowling_sr": to_float(bowling_row[11]),
            }

        result = summary or None
        self._espn_test_summary_cache[athlete_id] = result
        return result

    # ── Query cache (zero LLM calls for repeated questions) ────────────────

    def _get_cache_connection(self) -> duckdb.DuckDBPyConnection:
        """Return a writable connection to the cache database."""
        return duckdb.connect(self.cache_path)

    def _init_cache(self):
        """Create the cache tables if they don't exist."""
        try:
            con = self._get_cache_connection()
            con.execute("""
                CREATE TABLE IF NOT EXISTS query_cache (
                    question_hash TEXT PRIMARY KEY,
                    question TEXT,
                    sql TEXT,
                    columns_json TEXT,
                    rows_json TEXT,
                    answer TEXT,
                    chart_config_json TEXT,
                    context_summary TEXT,
                    display_hint_json TEXT,
                    model_used TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    hit_count INTEGER DEFAULT 0
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS cache_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            con.execute("""
                INSERT INTO cache_meta (key, value)
                VALUES ('data_version', '1')
                ON CONFLICT (key) DO NOTHING
            """)
            con.close()
        except Exception:
            pass  # Cache is optional; don't fail startup

    @staticmethod
    def _history_signature(history: list[dict] | None = None) -> str:
        """Generate a stable hash of the conversation history used for cache keys."""
        normalized_history = []
        for turn in history or []:
            normalized_history.append({
                "question": (turn.get("question") or "").strip(),
                "context_summary": (turn.get("context_summary") or "").strip(),
                "sql": (turn.get("sql") or "").strip(),
            })
        payload = json.dumps(
            normalized_history,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _get_cache_data_version(self, con: duckdb.DuckDBPyConnection | None = None) -> int:
        """Return the current cache data version for stale-data invalidation."""
        owns_connection = con is None
        try:
            if owns_connection:
                con = self._get_cache_connection()
            row = con.execute(
                "SELECT value FROM cache_meta WHERE key = 'data_version'"
            ).fetchone()
            version = int(row[0]) if row and row[0] is not None else 1
            return version
        except Exception:
            return 1
        finally:
            if owns_connection and con is not None:
                con.close()

    def _cache_key(self, question: str, history: list[dict] | None = None,
                   data_version: int | None = None) -> str:
        """Generate a cache key from question text, history signature, and data version."""
        normalized_question = question.strip().lower()
        version = data_version if data_version is not None else self._get_cache_data_version()
        payload = json.dumps(
            {
                "question": normalized_question,
                "history": self._history_signature(history),
                "data_version": int(version),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_lookup(self, question: str, history: list[dict] | None = None) -> dict | None:
        """Check cache for a previous answer. Returns result dict or None."""
        try:
            con = self._get_cache_connection()
            data_version = self._get_cache_data_version(con)
            key = self._cache_key(question, history=history, data_version=data_version)
            row = con.execute("""
                SELECT question, sql, columns_json, rows_json, answer,
                       chart_config_json, context_summary, display_hint_json, model_used
                FROM query_cache
                WHERE question_hash = ?
                  AND created_at > CURRENT_TIMESTAMP - INTERVAL 7 DAY
            """, [key]).fetchone()
            if row:
                con.execute(
                    "UPDATE query_cache SET hit_count = hit_count + 1 WHERE question_hash = ?",
                    [key],
                )
                con.close()
                cached_result = {
                    "question": row[0],
                    "sql": row[1],
                    "columns": json.loads(row[2]) if row[2] else [],
                    "rows": json.loads(row[3]) if row[3] else [],
                    "answer": row[4] or "",
                    "error": None,
                    "chart_config": json.loads(row[5]) if row[5] else None,
                    "context_summary": row[6],
                    "new_fact": None,
                    "display_hint": json.loads(row[7]) if row[7] else None,
                    "sections": None,
                    "model_used": f"{row[8]} (cached)",
                    "cached": True,
                }
                cached_result["sections"] = self._maybe_build_scorecard_sections(cached_result)
                return cached_result
            con.close()
        except Exception:
            pass
        return None

    def _cache_store(self, question: str, result: dict, history: list[dict] | None = None):
        """Store a successful result in the cache."""
        if result.get("error"):
            return
        try:
            con = self._get_cache_connection()
            data_version = self._get_cache_data_version(con)
            key = self._cache_key(question, history=history, data_version=data_version)
            con.execute("""
                INSERT INTO query_cache
                    (question_hash, question, sql, columns_json, rows_json, answer,
                     chart_config_json, context_summary, display_hint_json, model_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (question_hash) DO UPDATE SET
                    sql = EXCLUDED.sql,
                    columns_json = EXCLUDED.columns_json,
                    rows_json = EXCLUDED.rows_json,
                    answer = EXCLUDED.answer,
                    chart_config_json = EXCLUDED.chart_config_json,
                    context_summary = EXCLUDED.context_summary,
                    display_hint_json = EXCLUDED.display_hint_json,
                    model_used = EXCLUDED.model_used,
                    created_at = NOW(),
                    hit_count = 0
            """, [
                key,
                question,
                result.get("sql"),
                json.dumps(result.get("columns", [])),
                json.dumps(result.get("rows", []), default=str),
                result.get("answer", ""),
                json.dumps(result.get("chart_config")) if result.get("chart_config") else None,
                result.get("context_summary"),
                json.dumps(result.get("display_hint")) if result.get("display_hint") else None,
                result.get("model_used"),
            ])
            con.close()
        except Exception:
            pass

    def get_cache_stats(self) -> dict:
        """Return cache statistics."""
        try:
            con = self._get_cache_connection()
            data_version = self._get_cache_data_version(con)
            total = con.execute("SELECT COUNT(*) FROM query_cache").fetchone()[0]
            total_hits = con.execute("SELECT COALESCE(SUM(hit_count), 0) FROM query_cache").fetchone()[0]
            recent = con.execute("""
                SELECT COUNT(*) FROM query_cache
                WHERE created_at > CURRENT_TIMESTAMP - INTERVAL 7 DAY
            """).fetchone()[0]
            con.close()
            return {
                "total_entries": total,
                "active_entries": recent,
                "total_hits": total_hits,
                "data_version": data_version,
            }
        except Exception:
            return {"total_entries": 0, "active_entries": 0, "total_hits": 0, "data_version": 1}

    def clear_cache(self):
        """Delete all cache entries."""
        try:
            con = self._get_cache_connection()
            con.execute("DELETE FROM query_cache")
            con.close()
        except Exception:
            pass

    def invalidate_cache(self, clear_entries: bool = True) -> dict:
        """Bump the cache data version and optionally clear existing entries."""
        try:
            con = self._get_cache_connection()
            current_version = self._get_cache_data_version(con)
            new_version = current_version + 1
            con.execute("""
                INSERT INTO cache_meta (key, value, updated_at)
                VALUES ('data_version', ?, NOW())
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = NOW()
            """, [str(new_version)])
            if clear_entries:
                con.execute("DELETE FROM query_cache")
            con.close()
            return {
                "ok": True,
                "data_version": new_version,
                "cache_cleared": clear_entries,
            }
        except Exception as exc:
            return {
                "ok": False,
                "data_version": None,
                "cache_cleared": False,
                "error": str(exc),
            }

    # ── Player resolution (app-layer, zero LLM calls) ─────────────────────

    # Words that should NOT be treated as player name tokens
    _STOP_WORDS = frozenset(
        "a an the and or in on at of for to is was by has how many much"
        " who what which when where do does did not no vs versus against"
        " best worst top most highest lowest all time career test tests"
        " odi odis t20 t20i t20is ipl runs wickets batting bowling average"
        " strike rate economy centuries fifties sixes fours matches innings"
        " played scored taken hat trick maiden overs balls compare comparison"
        " between performance stats statistics record records total list"
        " player team series world cup first last ever".split()
    )

    def _resolve_players(self, question: str) -> list[dict]:
        """Resolve player names from the question using the player_aliases table.

        Returns list of match groups. Each group is:
            {"query_token": str, "candidates": [...], "confidence": "high|medium|low"}
        """
        try:
            con = self._get_connection()
            # Check if table exists
            tables = [r[0] for r in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND table_name='player_aliases'"
            ).fetchall()]
            if not tables:
                con.close()
                return []
        except Exception:
            return []

        # Tokenize question into potential name fragments (2-3 word windows + single words)
        words = _re.findall(r"[A-Za-z'-]+", question)
        # Strip possessives: "Smith's" → "Smith", "James'" → "James"
        words = [_re.sub(r"'s?$", "", w) for w in words]
        # Remove any empty tokens after stripping
        words = [w for w in words if w]
        candidates_by_token: dict[str, list[dict]] = {}
        confidence_by_token: dict[str, str] = {}

        def _classify_alias_match_confidence(rows: list, window_size: int, exact_match: bool) -> str:
            if not rows:
                return "low"
            if not exact_match:
                return "medium" if window_size >= 2 else "low"
            if window_size >= 2:
                return "high"
            alias_types = {str(r[3] or "") for r in rows}
            weak_single_word_aliases = {"first_name", "auto_first_name", "surname"}
            if alias_types and alias_types.issubset(weak_single_word_aliases):
                return "low"
            return "high"

        # Try multi-word windows first (3, then 2), then single words
        matched_positions: set[int] = set()
        for window_size in (3, 2, 1):
            for i in range(len(words) - window_size + 1):
                if any(j in matched_positions for j in range(i, i + window_size)):
                    continue
                token = " ".join(words[i:i + window_size])
                token_words_lower = [w.lower() for w in words[i:i + window_size]]
                if token.lower() in self._STOP_WORDS or any(w in self._STOP_WORDS for w in token_words_lower):
                    continue
                if len(token) < 3:
                    continue

                match_confidence = None
                try:
                    rows = con.execute("""
                        SELECT DISTINCT pa.canonical_name, pa.cricsheet_name, pa.team, pa.alias_type,
                               pm.kaggle_player_id
                        FROM player_aliases pa
                        LEFT JOIN player_map pm ON pa.cricsheet_name = pm.cricsheet_name
                        WHERE lower(pa.alias) = lower(?)
                        ORDER BY
                            CASE pa.alias_type
                                WHEN 'full_name' THEN 1
                                WHEN 'nickname' THEN 2
                                WHEN 'cricsheet' THEN 3
                                WHEN 'surname' THEN 4
                                WHEN 'first_name' THEN 5
                                ELSE 6
                            END,
                            pa.canonical_name
                        LIMIT 20
                    """, [token]).fetchall()
                    if rows:
                        match_confidence = _classify_alias_match_confidence(rows, window_size, exact_match=True)
                except Exception:
                    rows = []

                # Fuzzy alias fallback: if exact alias match failed for multi-word
                # tokens, try substring matching to handle common misspellings
                # (e.g. "Sudharshan" vs "Sudharsan", "Suryavanshi" vs "Suryawanshi")
                if not rows and window_size >= 2:
                    try:
                        # Build ILIKE pattern from each word: "Sai Sudharshan" → '%Sai%Sudhar%'
                        like_parts = [w[:min(len(w), 6)] for w in token.split()]
                        like_pattern = "%" + "%".join(like_parts) + "%"
                        rows = con.execute("""
                            SELECT DISTINCT pa.canonical_name, pa.cricsheet_name, pa.team, pa.alias_type,
                                   pm.kaggle_player_id
                            FROM player_aliases pa
                            LEFT JOIN player_map pm ON pa.cricsheet_name = pm.cricsheet_name
                            WHERE pa.alias ILIKE ?
                            ORDER BY
                                CASE pa.alias_type
                                    WHEN 'full_name' THEN 1
                                    WHEN 'nickname' THEN 2
                                    WHEN 'cricsheet' THEN 3
                                    WHEN 'surname' THEN 4
                                    ELSE 5
                                END,
                                pa.canonical_name
                            LIMIT 10
                        """, [like_pattern]).fetchall()
                        if rows:
                            match_confidence = _classify_alias_match_confidence(rows, window_size, exact_match=False)
                    except Exception:
                        rows = []

                if rows:
                    # Deduplicate by canonical_name
                    seen = set()
                    unique = []
                    for r in rows:
                        if r[0] not in seen:
                            seen.add(r[0])
                            unique.append({
                                "canonical_name": r[0],
                                "cricsheet_name": r[1] or "",
                                "team": r[2] or "",
                                "alias_type": r[3],
                                "kaggle_player_id": r[4],
                            })
                    candidates_by_token[token] = unique
                    confidence_by_token[token] = match_confidence or "high"
                    for j in range(i, i + window_size):
                        matched_positions.add(j)

        # Fuzzy fallback: for unmatched single-word tokens that look like names,
        # search the players table directly with ILIKE.  This handles cases like
        # "Suryavanshi" where the player isn't in player_aliases but exists as
        # "V Suryavanshi" in the players/deliveries tables.
        for i, word in enumerate(words):
            if i in matched_positions:
                continue
            token = word
            if token.lower() in self._STOP_WORDS or len(token) < 4:
                continue
            # Must start with uppercase (looks like a name)
            if not token[0].isupper():
                continue

            # First try alias table with ILIKE — catches first names, nicknames,
            # and misspellings even if no exact alias exists
            try:
                alias_fuzzy_rows = con.execute("""
                    SELECT DISTINCT pa.canonical_name, pa.cricsheet_name, pa.team, pa.alias_type,
                           pm.kaggle_player_id
                    FROM player_aliases pa
                    LEFT JOIN player_map pm ON pa.cricsheet_name = pm.cricsheet_name
                    WHERE pa.alias ILIKE '%' || ? || '%'
                    ORDER BY
                        CASE pa.alias_type
                            WHEN 'full_name' THEN 1
                            WHEN 'nickname' THEN 2
                            WHEN 'first_name' THEN 3
                            WHEN 'auto_first_name' THEN 4
                            WHEN 'cricsheet' THEN 5
                            WHEN 'surname' THEN 6
                            ELSE 7
                        END,
                        pa.canonical_name
                    LIMIT 10
                """, [token]).fetchall()
            except Exception:
                alias_fuzzy_rows = []

            if alias_fuzzy_rows:
                unique = []
                seen = set()
                for r in alias_fuzzy_rows:
                    if r[0] not in seen:
                        seen.add(r[0])
                        unique.append({
                            "canonical_name": r[0],
                            "cricsheet_name": r[1] or "",
                            "team": r[2] or "",
                            "alias_type": r[3],
                            "kaggle_player_id": r[4],
                        })
                candidates_by_token[token] = unique
                confidence_by_token[token] = "low"
                matched_positions.add(i)
                continue

            # Fallback: search players table directly
            try:
                fuzzy_rows = con.execute("""
                    SELECT DISTINCT p.name, p.cricsheet_id, pm.kaggle_player_id
                    FROM players p
                    LEFT JOIN player_map pm ON p.name = pm.cricsheet_name
                    WHERE p.name ILIKE '%' || ? || '%'
                    LIMIT 10
                """, [token]).fetchall()
            except Exception:
                fuzzy_rows = []
            if fuzzy_rows:
                unique = []
                seen = set()
                for r in fuzzy_rows:
                    if r[0] not in seen:
                        seen.add(r[0])
                        unique.append({
                            "canonical_name": r[0],
                            "cricsheet_name": r[0],
                            "team": "",
                            "alias_type": "fuzzy",
                            "kaggle_player_id": r[2],
                        })
                candidates_by_token[token] = unique
                confidence_by_token[token] = "low"
                matched_positions.add(i)

        con.close()

        # Build result: only tokens that actually matched players
        result = []
        for token, cands in candidates_by_token.items():
            if cands:
                result.append({
                    "query_token": token,
                    "candidates": cands,
                    "confidence": confidence_by_token.get(token, "high"),
                })
        return result

    def _build_disambiguation_response(self, question: str, token: str, candidates: list[dict]) -> dict:
        """Build a disambiguation response when multiple players match."""
        # Enrich candidates with player_profiles data (playing_role, country, headshot)
        enriched_candidates = []
        try:
            con = self._get_connection()
            for c in candidates[:8]:
                cricsheet_name = c.get("cricsheet_name", "")
                role = ""
                country = c.get("team", "")
                headshot = ""
                if cricsheet_name:
                    # Try player_profiles first (richest data)
                    try:
                        row = con.execute("""
                            SELECT pp.playing_role, pp.country, pp.headshot_url
                            FROM players p
                            JOIN player_profiles pp ON p.cricsheet_id = pp.cricsheet_id
                            WHERE p.name = ?
                            LIMIT 1
                        """, [cricsheet_name]).fetchone()
                        if row:
                            role = row[0] or ""
                            country = row[1] or country
                            headshot = row[2] or ""
                    except Exception:
                        pass
                    # If still no country, try to infer from matches
                    if not country:
                        try:
                            # Try Cricsheet deliveries for recent players
                            row = con.execute("""
                                SELECT i.batting_team
                                FROM deliveries d
                                JOIN innings i ON d.match_id = i.match_id AND d.innings_num = i.innings_num
                                JOIN matches m ON d.match_id = m.match_id
                                WHERE d.batter = ? AND m.team_type = 'international'
                                GROUP BY i.batting_team
                                ORDER BY COUNT(*) DESC
                                LIMIT 1
                            """, [cricsheet_name]).fetchone()
                            if row:
                                country = row[0] or ""
                        except Exception:
                            pass
                    # If still no country, try Kaggle batting for historical players
                    if not country:
                        try:
                            row = con.execute("""
                                SELECT kb.team
                                FROM kaggle_batting kb
                                JOIN player_map pm ON kb.batsman = pm.kaggle_player_id
                                WHERE pm.cricsheet_name = ?
                                GROUP BY kb.team
                                ORDER BY COUNT(*) DESC
                                LIMIT 1
                            """, [cricsheet_name]).fetchone()
                            if row:
                                country = row[0] or ""
                        except Exception:
                            pass
                ctx_parts = [p for p in [role, country] if p]
                enriched_candidates.append({
                    "name": c["canonical_name"],
                    "context": " — ".join(ctx_parts) if ctx_parts else c.get("cricsheet_name", ""),
                    "headshot": headshot,
                })
            con.close()
        except Exception:
            # Fallback: use original data without enrichment
            enriched_candidates = [
                {"name": c["canonical_name"], "context": f"{c['team']} — {c['cricsheet_name']}" if c["team"] else c["cricsheet_name"], "headshot": ""}
                for c in candidates[:8]
            ]

        return {
            "question": question,
            "sql": None,
            "columns": [],
            "rows": [],
            "answer": f"Multiple players match '{token}'. Which one did you mean?",
            "error": None,
            "chart_config": None,
            "context_summary": None,
            "new_fact": None,
            "display_hint": {"format": "disambiguate"},
            "sections": None,
            "model_used": None,
            "candidates": enriched_candidates,
            "original_question": question,
        }

    def _get_sql_prompt(self, question: str = "") -> str:
        """Build a compact SQL system prompt tailored to the detected intent."""
        intent = self._detect_schema_intent(question) if question else "full"
        return _build_compact_sql_prompt(intent, include_facts=True)

    # ── Dynamic schema pruning ─────────────────────────────────────────────

    # Keywords that signal a Cricsheet-only query (IPL, T20, ODI, franchise)
    _KW_CRICSHEET = frozenset({
        "ipl", "odi", "t20", "t20i", "bbl", "cpl", "psl", "wpl",
        "powerplay", "death over", "death overs",
        "indian premier league", "world cup t20",
        "ball by ball", "ball-by-ball",
        "srh", "csk", "rcb", "kkr", "pbks", "lsg",
        "chennai super kings", "mumbai indians", "kolkata knight riders",
        "royal challengers", "sunrisers hyderabad", "rajasthan royals",
        "delhi capitals", "punjab kings", "gujarat titans", "lucknow super giants",
    })

    # Keywords that signal a Kaggle-only query (Test cricket)
    _KW_KAGGLE = frozenset({
        "test match", "test matches", "test cricket", "test centuries",
        "test average", "test batting", "test bowling",
        "all-time test", "all time test", "test career", "test debut",
        "test history", "test record",
    })

    @classmethod
    def _detect_schema_intent(cls, question: str) -> str:
        """Detect whether question needs kaggle, cricsheet, or full schema."""
        q = question.lower()
        has_cricsheet = any(kw in q for kw in cls._KW_CRICSHEET)
        has_kaggle = any(kw in q for kw in cls._KW_KAGGLE)
        if has_cricsheet and not has_kaggle:
            return "cricsheet"
        if has_kaggle and not has_cricsheet:
            return "kaggle"
        return "full"

    @staticmethod
    def _prune_schema_for_intent(intent: str) -> str:
        """Return DB_SCHEMA with irrelevant source tables removed."""
        if intent == "full":
            return DB_SCHEMA
        bar = "═" * 79
        sections = DB_SCHEMA.split(bar)
        if len(sections) != 7:
            return DB_SCHEMA  # unexpected format, use full
        # sections: [intro, src1_header, kaggle_tables, src2_header, cricsheet_tables, rules_header, rules_notes]
        if intent == "kaggle":
            return bar.join([sections[0], sections[1], sections[2], sections[5], sections[6]])
        if intent == "cricsheet":
            return bar.join([sections[0], sections[3], sections[4], sections[5], sections[6]])
        return DB_SCHEMA

    def _get_narrative_prompt(self) -> str:
        """Build the full narrative system prompt with live knowledge facts."""
        facts = _load_knowledge_facts()
        if facts:
            return NARRATIVE_SYSTEM_PROMPT + f"\n\nCRICKET DOMAIN KNOWLEDGE (use these facts for context and accuracy):\n{facts}\n"
        return NARRATIVE_SYSTEM_PROMPT

    def _generate_sql(self, question: str, history: list[dict] | None = None) -> str:
        """Ask GPT-4.1 to generate SQL for the question."""
        messages = [{"role": "system", "content": self._get_sql_prompt(question)}]
        # Inject compact conversation context
        if history:
            for turn in history:
                q = turn.get("question", "")
                ctx = turn.get("context_summary", "")
                prev_sql = turn.get("sql", "")
                messages.append({"role": "user", "content": q})
                assistant_ctx = ""
                if ctx:
                    assistant_ctx += f"[Context: {ctx}]"
                if prev_sql:
                    assistant_ctx += f"\n[Previous SQL: {prev_sql}]"
                if assistant_ctx:
                    messages.append({"role": "assistant", "content": assistant_ctx.strip()})
        messages.append({"role": "user", "content": question})
        sql = self._call_llm(messages, temperature=0.1)
        # Strip markdown code fences if present
        if sql.startswith("```"):
            lines = sql.split("\n")
            sql = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return sql.strip()

    def _retry_sql(self, question: str, failed_sql: str, error: str) -> str:
        """Retry SQL generation with a compact prompt to stay within token limits."""
        err_text = str(error)[:500]
        sql_text = failed_sql[:800] + ("..." if len(failed_sql) > 800 else "")
        intent = self._detect_schema_intent(question) if question else "full"
        messages = [
            {"role": "system", "content": _build_compact_sql_prompt(intent, include_facts=False)},
            {"role": "user", "content": (
                f"{question}\n\n"
                f"Previous SQL failed with error: {err_text}\n"
                f"Previous SQL was: {sql_text}\n"
                f"Please fix the query."
            )},
        ]
        raw = self._call_llm(messages, temperature=0.1)
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return raw.strip()

    @staticmethod
    def _extract_sql(text: str) -> str:
        """Extract SQL from a response that may contain prose mixed with SQL.
        Handles: pure SQL, markdown code blocks, or prose followed by SQL."""
        text = text.strip()
        # If it starts with a SQL keyword, it's already clean
        upper = text.lstrip("(").upper()
        sql_kw = ("SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "EXPLAIN")
        if any(upper.startswith(kw) for kw in sql_kw):
            return text
        # Try to extract from markdown code block
        import re
        m = re.search(r"```(?:sql)?\s*\n(.+?)```", text, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()
        # Try to find a SQL statement in the text
        for kw in ("WITH", "SELECT"):
            idx = text.upper().find(kw)
            if idx >= 0:
                candidate = text[idx:].rstrip().rstrip(";") + ";"
                # Remove trailing prose after the SQL
                candidate = re.split(r"\n\s*\n[A-Z]", candidate)[0]
                return candidate.rstrip(";")
        return text

    # ── IPL team name normalization ──────────────────────────────────────
    # Teams that were renamed — map current name to all historical variants.
    _TEAM_NAME_VARIANTS: dict[str, list[str]] = {
        "Royal Challengers Bengaluru": ["Royal Challengers Bengaluru", "Royal Challengers Bangalore"],
        "Royal Challengers Bangalore": ["Royal Challengers Bengaluru", "Royal Challengers Bangalore"],
        "Delhi Capitals": ["Delhi Capitals", "Delhi Daredevils"],
        "Delhi Daredevils": ["Delhi Capitals", "Delhi Daredevils"],
        "Punjab Kings": ["Punjab Kings", "Kings XI Punjab"],
        "Kings XI Punjab": ["Punjab Kings", "Kings XI Punjab"],
    }

    _MALE_QUERY_PATTERN = _re.compile(r"\b(?:men|men's|mens|male|boys?)\b", _re.IGNORECASE)
    _FEMALE_QUERY_PATTERN = _re.compile(r"\b(?:women|women's|womens|female|girls?)\b", _re.IGNORECASE)
    _CONTEXT_DEPENDENT_QUERY_PATTERN = _re.compile(
        r"\b(?:this|that|these|those|it|he|his|her|hers|they|them|their|same|previous|above|earlier|former|latter)\b",
        _re.IGNORECASE,
    )
    _MATCH_REFERENCE_PATTERN = _re.compile(
        r"\b(?:this|that|it)\b|\b(?:this|that)\s+(?:match|game|scorecard|odi|test|t20(?:i)?)\b",
        _re.IGNORECASE,
    )
    _PLAYER_OF_MATCH_PATTERN = _re.compile(
        r"\b(?:man|player)\s+of\s+the\s+match\b|\bmotm\b",
        _re.IGNORECASE,
    )
    _LATEST_MATCH_PATTERN = _re.compile(r"\b(?:latest|most recent|recent|last)\b", _re.IGNORECASE)
    _SCORECARD_PATTERN = _re.compile(r"\bscorecard\b", _re.IGNORECASE)
    _CATCHES_PATTERN = _re.compile(r"\bcatch(?:es|er)?\b", _re.IGNORECASE)
    _LEADER_PATTERN = _re.compile(r"\b(?:most|max(?:imum)?|top|highest)\b", _re.IGNORECASE)
    _IPL_PATTERN = _re.compile(r"\b(?:ipl|indian premier league)\b", _re.IGNORECASE)
    _TEAM_ENTITY_PATTERN = _re.compile(
        r"\b(?:csk|mi|rcb|kkr|pbks|dc|gt|lsg|srh|rr|"
        r"chennai super kings|mumbai indians|kolkata knight riders|"
        r"royal challengers (?:bengaluru|bangalore)|sunrisers hyderabad|"
        r"rajasthan royals|delhi capitals|delhi daredevils|punjab kings|"
        r"kings xi punjab|gujarat titans|lucknow super giants|india|australia|"
        r"england|pakistan|south africa|west indies|new zealand|sri lanka|"
        r"bangladesh|afghanistan)\b",
        _re.IGNORECASE,
    )
    _TEAM_WORD_PATTERN = _re.compile(r"\b(?:team|teams|side|sides)\b", _re.IGNORECASE)
    _COMPARISON_QUERY_PATTERN = _re.compile(
        r"\b(?:compare|comparison|compared\s+to|vs|versus|against|other\s+teams?|than)\b",
        _re.IGNORECASE,
    )
    _PHASE_QUERY_PATTERN = _re.compile(
        r"\b(?:powerplay|middle\s+overs?|death\s+overs?|first\s+\d+\s+overs?|last\s+\d+\s+overs?)\b",
        _re.IGNORECASE,
    )
    _SEASON_WINDOW_PATTERN = _re.compile(
        r"\b(?:season|seasons|last\s+\d+\s+seasons?|recent\s+seasons?)\b",
        _re.IGNORECASE,
    )
    _TEAM_ABBREVIATIONS = {
        "csk": "Chennai Super Kings",
        "mi": "Mumbai Indians",
        "rcb": "Royal Challengers Bengaluru",
        "kkr": "Kolkata Knight Riders",
        "dc": "Delhi Capitals",
        "pbks": "Punjab Kings",
        "rr": "Rajasthan Royals",
        "srh": "Sunrisers Hyderabad",
        "gt": "Gujarat Titans",
        "lsg": "Lucknow Super Giants",
    }
    _TEAM_CANONICAL_NAMES = {
        "Chennai Super Kings": "Chennai Super Kings",
        "Mumbai Indians": "Mumbai Indians",
        "Royal Challengers Bengaluru": "Royal Challengers Bengaluru",
        "Royal Challengers Bangalore": "Royal Challengers Bengaluru",
        "Kolkata Knight Riders": "Kolkata Knight Riders",
        "Delhi Capitals": "Delhi Capitals",
        "Delhi Daredevils": "Delhi Capitals",
        "Punjab Kings": "Punjab Kings",
        "Kings XI Punjab": "Punjab Kings",
        "Rajasthan Royals": "Rajasthan Royals",
        "Sunrisers Hyderabad": "Sunrisers Hyderabad",
        "Gujarat Titans": "Gujarat Titans",
        "Lucknow Super Giants": "Lucknow Super Giants",
    }
    _LIMITED_OVERS_BY_LABEL = {
        "IPL": 20,
        "BBL": 20,
        "CPL": 20,
        "PSL": 20,
        "WPL": 20,
        "T20I": 20,
        "T20": 20,
        "ODI": 50,
    }
    _PHASE_FIRST_OVERS_PATTERN = _re.compile(r"\bfirst\s+(\d+)\s+overs?\b", _re.IGNORECASE)
    _PHASE_LAST_OVERS_PATTERN = _re.compile(r"\blast\s+(\d+)\s+overs?\b", _re.IGNORECASE)
    _POWERPLAY_QUERY_PATTERN = _re.compile(r"\bpowerplay\b", _re.IGNORECASE)
    _SEASON_YEAR_PATTERN = _re.compile(r"\b(?:19|20)\d{2}\b")
    _LAST_N_SEASONS_PATTERN = _re.compile(r"\blast\s+(\d+)\s+seasons?\b", _re.IGNORECASE)
    _RECENT_SEASONS_PATTERN = _re.compile(r"\brecent\s+seasons?\b", _re.IGNORECASE)
    _BOWLING_PHASE_PATTERN = _re.compile(
        r"\b(?:bowling|bowlers?|economy|economical|conced(?:e|ed|ing)|restrict(?:ed|ing)?)\b",
        _re.IGNORECASE,
    )
    _BATTING_PHASE_PATTERN = _re.compile(
        r"\b(?:batting|scoring|score|scored|run\s+rate)\b",
        _re.IGNORECASE,
    )

    @classmethod
    def _detect_match_gender(cls, question: str) -> str | None:
        """Infer an explicit match gender filter from the user's question."""
        if not question:
            return None
        has_male = bool(cls._MALE_QUERY_PATTERN.search(question))
        has_female = bool(cls._FEMALE_QUERY_PATTERN.search(question))
        if has_male and not has_female:
            return "male"
        if has_female and not has_male:
            return "female"
        return None

    @classmethod
    def _should_suppress_low_confidence_player_matches(cls, question: str) -> bool:
        """Ignore weak player hints when the query is clearly team/comparison focused."""
        if not question:
            return False
        q = question.lower()
        has_team_entity = bool(cls._TEAM_ENTITY_PATTERN.search(question)) or bool(cls._TEAM_WORD_PATTERN.search(question))
        has_comparison = bool(cls._COMPARISON_QUERY_PATTERN.search(question))
        has_phase = bool(cls._PHASE_QUERY_PATTERN.search(question))
        has_season_window = bool(cls._SEASON_WINDOW_PATTERN.search(question))
        return (
            "other teams" in q
            or (has_team_entity and (has_comparison or has_phase or has_season_window))
            or (has_comparison and has_phase)
        )

    @staticmethod
    def _append_sql_condition(sql: str, condition: str) -> str:
        """Append a WHERE condition before GROUP/ORDER/HAVING/LIMIT clauses."""
        clause_match = _re.search(r"\b(GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|UNION|EXCEPT|INTERSECT)\b", sql, _re.IGNORECASE)
        insert_at = clause_match.start() if clause_match else len(sql)
        head = sql[:insert_at].rstrip()
        tail = sql[insert_at:]
        if _re.search(r"\bWHERE\b", head, _re.IGNORECASE):
            return f"{head} AND {condition} {tail}".rstrip()
        return f"{head} WHERE {condition} {tail}".rstrip()

    @staticmethod
    def _normalize_duckdb_date_arithmetic(sql: str) -> str:
        """Rewrite common non-DuckDB date arithmetic into DuckDB interval syntax."""
        if not sql:
            return sql

        def _normalize_unit(unit: str) -> str:
            clean = unit.strip().strip("'\"").upper()
            if clean.endswith("S") and len(clean) > 1:
                clean = clean[:-1]
            return clean

        def _interval_expr(expr: str, amount_text: str, unit: str, op_name: str) -> str:
            amount = int(amount_text)
            normalized_unit = _normalize_unit(unit)
            if amount == 0:
                return expr.strip()

            use_plus = (op_name == "ADD" and amount > 0) or (op_name == "SUB" and amount < 0)
            operator = "+" if use_plus else "-"
            return f"({expr.strip()} {operator} INTERVAL {abs(amount)} {normalized_unit})"

        sql = _re.sub(
            r"\bDATE_(ADD|SUB)\s*\(\s*('?\w+'?)\s*,\s*([+-]?\d+)\s*,\s*([^\)]+?)\s*\)",
            lambda m: _interval_expr(m.group(4), m.group(3), m.group(2), m.group(1).upper()),
            sql,
            flags=_re.IGNORECASE,
        )

        sql = _re.sub(
            r"\bDATE_(ADD|SUB)\s*\(\s*([^,\)]+?)\s*,\s*INTERVAL\s+([+-]?\d+)\s+(\w+)\s*\)",
            lambda m: _interval_expr(m.group(2), m.group(3), m.group(4), m.group(1).upper()),
            sql,
            flags=_re.IGNORECASE,
        )

        return sql

    @classmethod
    def _enforce_gender_filter(cls, sql: str, question: str) -> str:
        """Force explicit men's/women's filters into generated SQL."""
        gender = cls._detect_match_gender(question)
        if not gender:
            return sql

        sql_lower = sql.lower()
        if "matches.gender" in sql_lower or "gender_matches.gender" in sql_lower:
            return sql

        match_aliases = _re.findall(r"(?:FROM|JOIN)\s+matches(?:\s+AS)?\s+([A-Za-z_][A-Za-z0-9_]*)", sql, flags=_re.IGNORECASE)
        if match_aliases:
            alias = match_aliases[0]
            if f"{alias.lower()}.gender" in sql_lower:
                return sql
            return cls._append_sql_condition(sql, f"{alias}.gender = '{gender}'")

        kaggle_match = _re.search(r"FROM\s+kaggle_matches(?:\s+AS)?\s+([A-Za-z_][A-Za-z0-9_]*)", sql, flags=_re.IGNORECASE)
        if kaggle_match:
            alias = kaggle_match.group(1)
            join_sql = kaggle_match.group(0) + f' JOIN matches gender_matches ON CAST({alias}."Match ID" AS VARCHAR) = gender_matches.match_id'
            sql = sql[:kaggle_match.start()] + join_sql + sql[kaggle_match.end():]
            return cls._append_sql_condition(sql, f"gender_matches.gender = '{gender}'")

        return sql

    @staticmethod
    def _fix_player_names_in_sql(sql: str, player_name_map: dict[str, str]) -> str:
        """Replace canonical/display player names with cricsheet names in SQL.

        player_name_map: {canonical_name: cricsheet_name}  e.g. {"Krunal Pandya": "KH Pandya"}
        Handles both = 'Name' and ILIKE '%Name%' patterns.
        """
        if not player_name_map or not sql:
            return sql
        for canonical, cricsheet in player_name_map.items():
            if not canonical or not cricsheet or canonical == cricsheet:
                continue
            # Replace exact string matches inside SQL string literals
            # Case-insensitive replace of the canonical name with cricsheet name
            # Handle: = 'Krunal Pandya'  →  = 'KH Pandya'
            # Handle: ILIKE '%Krunal Pandya%'  →  ILIKE '%KH Pandya%'
            # Handle: ILIKE '%Krunal%'  →  could be from the canonical, replace if in map
            # Use re.sub for case-insensitive replacement inside quoted strings
            pattern = _re.compile(_re.escape(canonical), _re.IGNORECASE)
            sql = pattern.sub(cricsheet, sql)
        return sql

    @classmethod
    def _apply_question_sql_guards(cls, sql: str, question: str) -> str:
        """Apply deterministic SQL fixes inferred from the user question."""
        sql = cls._normalize_duckdb_date_arithmetic(sql)
        sql = cls._normalize_team_names(sql)
        sql = cls._enforce_gender_filter(sql, question)
        sql = cls._fix_bowling_wicket_join(sql)
        return sql

    @staticmethod
    def _fix_bowling_wicket_join(sql: str) -> str:
        """Remove erroneous 'd.bowler = w.player_out' from wicket joins.

        The LLM sometimes adds this condition thinking it links the bowler to the
        wicket, but player_out is the *dismissed* batter — so this always yields 0.
        """
        # Match patterns like: AND d.bowler = w.player_out  (with optional alias variations)
        sql = _re.sub(
            r"\s+AND\s+\w+\.bowler\s*=\s*\w+\.player_out\b",
            "",
            sql,
            flags=_re.IGNORECASE,
        )
        return sql

    @classmethod
    def _should_bypass_cache(cls, question: str, history: list[dict] | None = None) -> bool:
        """Avoid question-only cache hits for follow-ups that depend on prior context."""
        if not history or not question:
            return False
        return bool(cls._CONTEXT_DEPENDENT_QUERY_PATTERN.search(question))

    def _resolve_previous_match_context(self, history: list[dict] | None = None) -> dict | None:
        """Recover the last match-focused result from history by re-running prior SQL."""
        if not history:
            return None

        for turn in reversed(history):
            prev_sql = (turn.get("sql") or "").strip()
            if not prev_sql:
                continue
            try:
                columns, rows = self._execute_sql(prev_sql)
            except Exception:
                continue

            match_id = self._extract_match_id(columns, rows)
            if not match_id:
                continue

            turn_text = f"{turn.get('question', '')} {turn.get('context_summary', '')}".lower()
            is_single_match_turn = len(rows) == 1 or "scorecard" in turn_text or bool(
                _re.search(r"\b(?:latest|recent|last|this|that)\b.*\b(?:match|game|odi|test|t20(?:i)?)\b", turn_text)
            )
            if not is_single_match_turn:
                continue

            return {
                "match_id": match_id,
                "data_source": self._detect_data_source(prev_sql),
                "question": turn.get("question", ""),
                "context_summary": turn.get("context_summary", ""),
            }
        return None

    def _build_match_followup_response(self, question: str, history: list[dict] | None = None) -> dict | None:
        """Answer simple match follow-ups like player of the match from the previous match context."""
        if not history or not question:
            return None
        if not self._PLAYER_OF_MATCH_PATTERN.search(question):
            return None
        if not self._MATCH_REFERENCE_PATTERN.search(question):
            return None

        previous_match = self._resolve_previous_match_context(history)
        if not previous_match:
            return None

        data_source = previous_match["data_source"]
        match_id = previous_match["match_id"]
        con = self._get_connection()
        try:
            if data_source == "kaggle":
                sql = '''
                    SELECT km."Match ID" AS match_id,
                           km."Team1 Name" AS team1,
                           km."Team2 Name" AS team2,
                           km."Match Start Date" AS match_date,
                           COALESCE(kp.player_name, CAST(km."MOM Player" AS VARCHAR)) AS player_of_match
                    FROM kaggle_matches km
                    LEFT JOIN kaggle_players kp
                      ON TRY_CAST(km."MOM Player" AS BIGINT) = kp.player_id
                    WHERE km."Match ID" = ?
                '''.strip()
                row = con.execute(sql, [match_id]).fetchone()
                source_label = "Kaggle scorecards"
            else:
                sql = '''
                    SELECT match_id,
                           team1,
                           team2,
                           date_start AS match_date,
                           player_of_match
                    FROM matches
                    WHERE match_id = ?
                '''.strip()
                row = con.execute(sql, [str(match_id)]).fetchone()
                source_label = "Cricsheet data"
        finally:
            con.close()

        if not row:
            return None

        match_date = str(row[3]) if row[3] else ""
        team1 = row[1] or ""
        team2 = row[2] or ""
        player_of_match = row[4] or ""

        if player_of_match:
            answer = f"{player_of_match} was the player of the match in {team1} vs {team2} on {match_date}."
            context_summary = f"Player of the match for {team1} vs {team2} on {match_date} via {source_label}"
        else:
            answer = f"I found {team1} vs {team2} on {match_date}, but there is no player-of-the-match entry in the dataset."
            context_summary = f"No player-of-the-match entry for {team1} vs {team2} on {match_date} via {source_label}"

        return {
            "question": question,
            "sql": sql,
            "columns": ["match_id", "team1", "team2", "match_date", "player_of_match"],
            "rows": [[row[0], team1, team2, match_date, player_of_match or None]],
            "answer": answer,
            "error": None,
            "chart_config": None,
            "context_summary": context_summary,
            "new_fact": None,
            "display_hint": {"format": "stats", "stat_type": "match"},
            "sections": None,
            "model_used": "deterministic-followup",
        }

    @classmethod
    def _canonicalize_team_name(cls, team_name: str) -> str:
        """Map historical or abbreviated team names to a single canonical label."""
        return cls._TEAM_CANONICAL_NAMES.get(team_name, team_name)

    @classmethod
    def _extract_team_mentions(cls, text: str) -> list[str]:
        """Extract canonical team names from free text."""
        if not text:
            return []

        found: list[str] = []
        text_lower = text.lower()

        def _add(team_name: str) -> None:
            canonical_name = cls._canonicalize_team_name(team_name)
            if canonical_name and canonical_name not in found:
                found.append(canonical_name)

        for abbr, full_name in cls._TEAM_ABBREVIATIONS.items():
            if _re.search(rf"\b{_re.escape(abbr)}\b", text_lower):
                _add(full_name)

        for team_name in sorted(cls._TEAM_CANONICAL_NAMES, key=len, reverse=True):
            if _re.search(rf"\b{_re.escape(team_name.lower())}\b", text_lower):
                _add(team_name)

        return found

    @staticmethod
    def _format_year_list(years: list[int]) -> str:
        """Render season years in a short natural-language form."""
        if not years:
            return "all seasons"
        if len(years) == 1:
            return str(years[0])
        if len(years) == 2:
            return f"{years[0]} and {years[1]}"
        return ", ".join(str(year) for year in years[:-1]) + f", and {years[-1]}"

    @staticmethod
    def _ordinal_rank(rank: int) -> str:
        """Return 1st/2nd/3rd style ranking labels."""
        if 10 <= (rank % 100) <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(rank % 10, "th")
        return f"{rank}{suffix}"

    @staticmethod
    def _possessive_label(label: str) -> str:
        """Return a natural possessive form for team or player labels."""
        if not label:
            return ""
        return f"{label}'" if label.endswith("s") else f"{label}'s"

    def _resolve_team_phase_competition(self, question: str, history: list[dict] | None = None) -> dict | None:
        """Infer limited-overs competition context from the current turn or history."""
        candidate_texts = [question]
        for turn in reversed(history or []):
            candidate_texts.append(turn.get("question", ""))
            candidate_texts.append(turn.get("context_summary", ""))

        for text in candidate_texts:
            if not text:
                continue
            sql_filter, label = self._detect_event_filter(text)
            total_overs = self._LIMITED_OVERS_BY_LABEL.get(label)
            if sql_filter and total_overs:
                return {
                    "sql_filter": sql_filter,
                    "competition_label": label,
                    "total_overs": total_overs,
                }
        return None

    @classmethod
    def _detect_phase_window_from_text(cls, text: str, total_overs: int | None) -> dict | None:
        """Parse a phase window like first 10 overs, powerplay, or last 5 overs."""
        if not text:
            return None

        first_match = cls._PHASE_FIRST_OVERS_PATTERN.search(text)
        if first_match:
            overs = max(1, int(first_match.group(1)))
            if total_overs is not None:
                overs = min(overs, total_overs)
            return {
                "start_over": 0,
                "end_over": overs,
                "phase_label": f"first {overs} overs",
                "phase_alias": f"first_{overs}",
            }

        if cls._POWERPLAY_QUERY_PATTERN.search(text):
            overs = min(6, total_overs) if total_overs is not None else 6
            return {
                "start_over": 0,
                "end_over": overs,
                "phase_label": "powerplay",
                "phase_alias": "powerplay",
            }

        last_match = cls._PHASE_LAST_OVERS_PATTERN.search(text)
        if last_match and total_overs is not None:
            overs = max(1, int(last_match.group(1)))
            overs = min(overs, total_overs)
            return {
                "start_over": max(0, total_overs - overs),
                "end_over": total_overs,
                "phase_label": f"last {overs} overs",
                "phase_alias": f"last_{overs}",
            }

        return None

    def _resolve_team_phase_window(self, question: str, history: list[dict] | None,
                                   total_overs: int | None) -> dict | None:
        """Resolve the phase window, falling back to recent history when needed."""
        phase = self._detect_phase_window_from_text(question, total_overs)
        if phase is not None:
            return phase

        for turn in reversed(history or []):
            phase = self._detect_phase_window_from_text(turn.get("question", ""), total_overs)
            if phase is not None:
                return phase
        return None

    @classmethod
    def _extract_explicit_years(cls, text: str) -> list[int]:
        """Return sorted unique season years mentioned in text."""
        if not text:
            return []
        return sorted({int(year) for year in cls._SEASON_YEAR_PATTERN.findall(text)})

    def _resolve_recent_season_years(self, competition_sql_filter: str, season_count: int) -> list[int]:
        """Look up the last N completed seasons for a competition from match dates."""
        if not competition_sql_filter or season_count <= 0:
            return []

        season_count = min(max(int(season_count), 1), 5)
        con = self._get_connection()
        try:
            completed_rows = con.execute(f"""
                SELECT DISTINCT CAST(EXTRACT(YEAR FROM m.date_start) AS INT) AS season_year
                FROM matches m
                WHERE {competition_sql_filter}
                  AND CAST(EXTRACT(YEAR FROM m.date_start) AS INT) < CAST(EXTRACT(YEAR FROM CURRENT_DATE) AS INT)
                ORDER BY season_year DESC
                LIMIT {season_count}
            """).fetchall()
            years = [int(row[0]) for row in completed_rows if row[0] is not None]
            if len(years) >= season_count:
                return sorted(years)

            fallback_rows = con.execute(f"""
                SELECT DISTINCT CAST(EXTRACT(YEAR FROM m.date_start) AS INT) AS season_year
                FROM matches m
                WHERE {competition_sql_filter}
                ORDER BY season_year DESC
                LIMIT {season_count}
            """).fetchall()
            return sorted(int(row[0]) for row in fallback_rows if row[0] is not None)
        finally:
            con.close()

    def _resolve_team_phase_years(self, question: str, history: list[dict] | None,
                                  competition_sql_filter: str) -> tuple[list[int], bool]:
        """Resolve season years and whether the user asked for a season-by-season split."""
        explicit_years = self._extract_explicit_years(question)
        if explicit_years:
            return explicit_years, len(explicit_years) > 1

        season_match = self._LAST_N_SEASONS_PATTERN.search(question or "")
        if season_match:
            years = self._resolve_recent_season_years(competition_sql_filter, int(season_match.group(1)))
            return years, False

        if self._RECENT_SEASONS_PATTERN.search(question or ""):
            years = self._resolve_recent_season_years(competition_sql_filter, 2)
            return years, False

        for turn in reversed(history or []):
            previous_question = turn.get("question", "")
            explicit_years = self._extract_explicit_years(previous_question)
            if explicit_years:
                return explicit_years, len(explicit_years) > 1

            season_match = self._LAST_N_SEASONS_PATTERN.search(previous_question or "")
            if season_match:
                years = self._resolve_recent_season_years(competition_sql_filter, int(season_match.group(1)))
                return years, False

            if self._RECENT_SEASONS_PATTERN.search(previous_question or ""):
                years = self._resolve_recent_season_years(competition_sql_filter, 2)
                return years, False

        return [], False

    def _resolve_team_phase_mode(self, question: str, history: list[dict] | None = None) -> str:
        """Determine whether the team phase question is about batting or bowling."""
        candidate_texts = [question]
        for turn in reversed(history or []):
            candidate_texts.append(turn.get("question", ""))
            candidate_texts.append(turn.get("context_summary", ""))

        for text in candidate_texts:
            if not text:
                continue
            if self._BOWLING_PHASE_PATTERN.search(text):
                return "bowling"
            if self._BATTING_PHASE_PATTERN.search(text):
                return "batting"

        return "batting"

    def _resolve_team_phase_target_team(self, question: str, history: list[dict] | None = None) -> str | None:
        """Resolve the focal team from the current turn or prior questions."""
        teams = self._extract_team_mentions(question)
        if teams:
            return teams[0]

        for turn in reversed(history or []):
            teams = self._extract_team_mentions(turn.get("question", ""))
            if teams:
                return teams[0]
        return None

    def _resolve_league_comparison_scope(self, question: str, history: list[dict] | None = None) -> bool:
        """Determine whether the user wants one team benchmarked against the league."""
        if question and self._COMPARISON_QUERY_PATTERN.search(question):
            return True

        for turn in reversed(history or []):
            previous_question = turn.get("question", "")
            if previous_question and self._COMPARISON_QUERY_PATTERN.search(previous_question):
                return True
        return False

    def _detect_team_phase_comparison_request(self, question: str,
                                              history: list[dict] | None = None) -> dict | None:
        """Recognize team phase comparison questions that are safer to answer deterministically."""
        competition = self._resolve_team_phase_competition(question, history)
        if not competition:
            return None

        phase_window = self._resolve_team_phase_window(question, history, competition["total_overs"])
        if not phase_window:
            return None

        years, split_by_year = self._resolve_team_phase_years(question, history, competition["sql_filter"])
        if not years:
            return None

        target_team = self._resolve_team_phase_target_team(question, history)
        compare_against_league = self._resolve_league_comparison_scope(question, history)
        has_team_signal = bool(target_team) or compare_against_league or bool(self._TEAM_WORD_PATTERN.search(question or ""))
        if not has_team_signal:
            return None

        return {
            **competition,
            **phase_window,
            "years": years,
            "split_by_year": split_by_year,
            "mode": self._resolve_team_phase_mode(question, history),
            "target_team": target_team,
            "compare_against_league": compare_against_league,
        }

    def _build_team_phase_comparison_response(self, question: str,
                                              history: list[dict] | None = None) -> dict | None:
        """Build deterministic team phase comparison stats from Cricsheet deliveries."""
        request = self._detect_team_phase_comparison_request(question, history)
        if not request:
            return None

        year_sql = ", ".join(str(year) for year in request["years"])
        phase_condition = (
            f"d.over_num >= {request['start_over']} "
            f"AND d.over_num < {request['end_over']}"
        )
        years_text = self._format_year_list(request["years"])
        years_title = "-".join(str(year) for year in request["years"]) if request["years"] else "all-seasons"
        target_team_sql = (request["target_team"] or "").replace("'", "''")
        select_season = "season_year AS season,\n    " if request["split_by_year"] else ""
        group_season = "season_year, " if request["split_by_year"] else ""

        chart_config = None
        if request["mode"] == "bowling":
            team_column = "bowling_team"
            primary_metric = f"avg_runs_conceded_{request['phase_alias']}"
            secondary_metric = f"avg_economy_{request['phase_alias']}"
            outer_where = "WHERE bowling_team IS NOT NULL"
            if request["target_team"] and not request["compare_against_league"]:
                outer_where += f" AND bowling_team = '{target_team_sql}'"
            sql = f"""
WITH phase_innings AS (
    SELECT
        CAST(EXTRACT(YEAR FROM m.date_start) AS INT) AS season_year,
        m.match_id,
        i.innings_num,
        CASE
            WHEN i.batting_team = m.team1 THEN m.team2
            WHEN i.batting_team = m.team2 THEN m.team1
            ELSE NULL
        END AS bowling_team,
        SUM(d.runs_total) AS phase_runs,
        COUNT(*) FILTER (
            WHERE COALESCE(d.extras_wides, 0) = 0
              AND COALESCE(d.extras_noballs, 0) = 0
        ) AS legal_balls
    FROM matches m
    JOIN innings i ON m.match_id = i.match_id
    JOIN deliveries d ON m.match_id = d.match_id AND i.innings_num = d.innings_num
    WHERE {request['sql_filter']}
      AND CAST(EXTRACT(YEAR FROM m.date_start) AS INT) IN ({year_sql})
      AND {phase_condition}
    GROUP BY 1, 2, 3, 4
)
SELECT
    {select_season}{team_column},
    COUNT(DISTINCT match_id) AS matches_played,
    ROUND(AVG(phase_runs), 2) AS {primary_metric},
    ROUND(SUM(phase_runs) * 6.0 / NULLIF(SUM(legal_balls), 0), 2) AS {secondary_metric}
FROM phase_innings
{outer_where}
GROUP BY {group_season}{team_column}
ORDER BY {('season ASC, ' if request['split_by_year'] else '')}{primary_metric} ASC, {team_column}
LIMIT 50
""".strip()
        else:
            team_column = "batting_team"
            primary_metric = f"avg_runs_{request['phase_alias']}"
            secondary_metric = f"avg_run_rate_{request['phase_alias']}"
            outer_where = ""
            if request["target_team"] and not request["compare_against_league"]:
                outer_where = f"WHERE batting_team = '{target_team_sql}'"
            sql = f"""
WITH phase_innings AS (
    SELECT
        CAST(EXTRACT(YEAR FROM m.date_start) AS INT) AS season_year,
        m.match_id,
        i.innings_num,
        i.batting_team AS batting_team,
        SUM(d.runs_total) AS phase_runs,
        COUNT(*) FILTER (
            WHERE COALESCE(d.extras_wides, 0) = 0
              AND COALESCE(d.extras_noballs, 0) = 0
        ) AS legal_balls
    FROM matches m
    JOIN innings i ON m.match_id = i.match_id
    JOIN deliveries d ON m.match_id = d.match_id AND i.innings_num = d.innings_num
    WHERE {request['sql_filter']}
      AND CAST(EXTRACT(YEAR FROM m.date_start) AS INT) IN ({year_sql})
      AND {phase_condition}
    GROUP BY 1, 2, 3, 4
)
SELECT
    {select_season}{team_column},
    COUNT(DISTINCT match_id) AS matches_played,
    ROUND(AVG(phase_runs), 2) AS {primary_metric},
    ROUND(SUM(phase_runs) * 6.0 / NULLIF(SUM(legal_balls), 0), 2) AS {secondary_metric}
FROM phase_innings
{outer_where}
GROUP BY {group_season}{team_column}
ORDER BY {('season ASC, ' if request['split_by_year'] else '')}{primary_metric} DESC, {team_column}
LIMIT 50
""".strip()

            if not request["split_by_year"]:
                chart_config = {
                    "type": "horizontalbar",
                    "title": (
                        f"{request['competition_label']} Teams: Batting in {request['phase_label'].title()} "
                        f"({years_title})"
                    ),
                    "x_field": team_column,
                    "y_field": primary_metric,
                }

        columns, rows = self._execute_sql(sql)
        row_lists = [list(row) for row in rows]
        if not row_lists:
            return {
                "question": question,
                "sql": sql,
                "columns": columns,
                "rows": [],
                "answer": "No matching records found in the database for this team phase comparison.",
                "error": None,
                "chart_config": None,
                "context_summary": (
                    f"No {request['competition_label']} {request['mode']} {request['phase_label']} results "
                    f"for seasons {years_text}"
                ),
                "new_fact": None,
                "display_hint": {"format": "table", "stat_type": "team"},
                "sections": None,
                "model_used": "deterministic-team-phase",
            }

        team_idx = columns.index(team_column)
        primary_idx = columns.index(primary_metric)
        secondary_idx = columns.index(secondary_metric)
        season_idx = columns.index("season") if request["split_by_year"] else None

        def _metric_text(row: list) -> str:
            primary_val = float(row[primary_idx]) if row[primary_idx] is not None else 0.0
            secondary_val = float(row[secondary_idx]) if row[secondary_idx] is not None else 0.0
            if request["mode"] == "bowling":
                return f"conceded {primary_val:.2f} runs at an economy of {secondary_val:.2f}"
            return f"averaged {primary_val:.2f} runs at a run rate of {secondary_val:.2f}"

        if request["split_by_year"]:
            season_groups: dict[int, list[list]] = {}
            for row in row_lists:
                season_groups.setdefault(int(row[season_idx]), []).append(row)

            if request["target_team"]:
                season_bits = []
                for season in sorted(season_groups):
                    season_rows = season_groups[season]
                    target_row = next((row for row in season_rows if row[team_idx] == request["target_team"]), None)
                    if not target_row:
                        continue
                    rank = season_rows.index(target_row) + 1
                    season_bits.append(
                        f"In {season}, {request['target_team']} ranked {self._ordinal_rank(rank)} of {len(season_rows)} teams and {_metric_text(target_row)}."
                    )

                if season_bits:
                    answer = (
                        f"For the {request['competition_label']} {years_text} seasons, "
                        f"{self._possessive_label(request['target_team'])} {request['mode']} in the {request['phase_label']} compares as follows: "
                        + " ".join(season_bits)
                    )
                else:
                    answer = (
                        f"The table shows {request['competition_label']} {request['mode']} comparisons in the "
                        f"{request['phase_label']} for the {years_text} seasons."
                    )
            else:
                leader_bits = []
                for season in sorted(season_groups):
                    leader_row = season_groups[season][0]
                    leader_bits.append(
                        f"In {season}, {leader_row[team_idx]} led the league and {_metric_text(leader_row)}."
                    )
                answer = (
                    f"The {request['competition_label']} {request['mode']} comparison in the {request['phase_label']} "
                    f"for {years_text} shows clear season-by-season differences. " + " ".join(leader_bits)
                )
        else:
            target_row = None
            if request["target_team"]:
                target_row = next((row for row in row_lists if row[team_idx] == request["target_team"]), None)

            if target_row is not None and request["compare_against_league"]:
                rank = row_lists.index(target_row) + 1
                answer = (
                    f"Across the {request['competition_label']} {years_text} seasons, {request['target_team']} "
                    f"{_metric_text(target_row)} in the {request['phase_label']}, ranking "
                    f"{self._ordinal_rank(rank)} of {len(row_lists)} teams. The table shows how they compare with the rest of the league."
                )
            elif target_row is not None:
                answer = (
                    f"Across the {request['competition_label']} {years_text} seasons, {request['target_team']} "
                    f"{_metric_text(target_row)} in the {request['phase_label']}."
                )
            else:
                leader_row = row_lists[0]
                answer = (
                    f"Across the {request['competition_label']} {years_text} seasons, {leader_row[team_idx]} had the best "
                    f"{request['mode']} numbers in the {request['phase_label']} and {_metric_text(leader_row)}."
                )

        context_target = f" for {request['target_team']}" if request["target_team"] else ""
        context_scope = " vs league" if request["compare_against_league"] else ""
        context_summary = (
            f"{request['competition_label']} {request['mode']} {request['phase_label']}{context_target}{context_scope}, "
            f"seasons {years_text} via Cricsheet data"
        )

        return {
            "question": question,
            "sql": sql,
            "columns": columns,
            "rows": row_lists,
            "answer": answer,
            "error": None,
            "chart_config": chart_config,
            "context_summary": context_summary,
            "new_fact": None,
            "display_hint": {"format": "table", "stat_type": "team"},
            "sections": None,
            "model_used": "deterministic-team-phase",
        }

    def _detect_latest_match_request(self, question: str) -> dict | None:
        """Recognize latest-match scorecard requests that should be handled deterministically."""
        if not question:
            return None
        question_lower = question.lower()
        if not self._SCORECARD_PATTERN.search(question_lower):
            return None
        if not self._LATEST_MATCH_PATTERN.search(question_lower):
            return None

        gender = self._detect_match_gender(question_lower)
        match_type = None

        if _re.search(r"\btest(?:\s+match)?\b", question_lower):
            match_type = "Test"
        elif _re.search(r"\bodi(?:\s+match)?\b", question_lower):
            match_type = "ODI"
        elif _re.search(r"\bt20(?:\s+international|i)?\b|\bit20\b|\bt20i\b", question_lower):
            match_type = "IT20"

        if not match_type:
            return None

        return {
            "match_type": match_type,
            "gender": gender,
        }

    def _build_latest_match_scorecard_response(self, question: str) -> dict | None:
        """Resolve latest match scorecard queries directly from the matches table."""
        latest_request = self._detect_latest_match_request(question)
        if not latest_request:
            return None

        conditions = [f"match_type = '{latest_request['match_type']}'"]
        if latest_request.get("gender"):
            conditions.append(f"gender = '{latest_request['gender']}'")
        where_clause = " AND ".join(conditions)
        sql = (
            "SELECT match_id, date_start AS match_date, team1, team2, player_of_match "
            f"FROM matches WHERE {where_clause} ORDER BY date_start DESC LIMIT 1"
        )

        columns, rows = self._execute_sql(sql)
        if not rows:
            return None

        row = list(rows[0])
        match_id = row[0]
        match_date = str(row[1]) if row[1] else ""
        team1 = row[2] or ""
        team2 = row[3] or ""
        player_of_match = row[4] or ""
        scorecard = self._build_scorecard(match_id, "cricsheet")

        if not scorecard:
            return None

        format_label_map = {
            "Test": "Test",
            "ODI": "ODI",
            "IT20": "T20 International",
        }
        format_label = format_label_map.get(latest_request["match_type"], latest_request["match_type"])
        gender_label = ""
        if latest_request.get("gender") == "male":
            gender_label = " men's"
        elif latest_request.get("gender") == "female":
            gender_label = " women's"

        answer = f"Here is the scorecard for the latest{gender_label} {format_label} match: {team1} vs {team2} on {match_date}."
        if player_of_match:
            answer += f" Player of the match: {player_of_match}."

        return {
            "question": question,
            "sql": sql,
            "columns": columns,
            "rows": [row],
            "answer": answer,
            "error": None,
            "chart_config": None,
            "context_summary": f"Latest{gender_label} {format_label} scorecard: {team1} vs {team2} on {match_date} via Cricsheet data",
            "new_fact": None,
            "display_hint": {"format": "scorecard", "stat_type": "match"},
            "sections": scorecard,
            "model_used": "deterministic-latest-scorecard",
        }

    def _detect_fielding_leader_request(self, question: str) -> dict | None:
        """Recognize top-fielding queries that are safer to answer deterministically."""
        if not question:
            return None

        question_lower = question.lower()
        if not self._IPL_PATTERN.search(question_lower):
            return None
        if not self._CATCHES_PATTERN.search(question_lower):
            return None
        if not self._LEADER_PATTERN.search(question_lower):
            return None
        if "stumping" in question_lower or "run out" in question_lower:
            return None

        return {
            "event_name": "Indian Premier League",
            "competition_label": "IPL",
            "stat_label": "catches",
        }

    def _build_fielding_leader_response(self, question: str) -> dict | None:
        """Resolve top catches questions directly from Cricsheet wickets data."""
        request = self._detect_fielding_leader_request(question)
        if not request:
            return None

        sql = '''
            WITH catches AS (
                SELECT w.fielder1 AS fielder
                FROM wickets w
                JOIN matches m ON w.match_id = m.match_id
                WHERE m.event_name = 'Indian Premier League'
                  AND LOWER(w.kind) = 'caught'
                  AND w.fielder1 IS NOT NULL

                UNION ALL

                SELECT w.fielder2 AS fielder
                FROM wickets w
                JOIN matches m ON w.match_id = m.match_id
                WHERE m.event_name = 'Indian Premier League'
                  AND LOWER(w.kind) = 'caught'
                  AND w.fielder2 IS NOT NULL
            )
            SELECT fielder, COUNT(*) AS catches
            FROM catches
            GROUP BY fielder
            ORDER BY catches DESC, fielder
            LIMIT 1
        '''.strip()

        columns, rows = self._execute_sql(sql)
        if not rows:
            return None

        fielder = rows[0][0] or ""
        catches = int(rows[0][1] or 0)
        competition_label = request["competition_label"]
        answer = f"{fielder} has taken the most catches in {competition_label} with {catches} catches."

        return {
            "question": question,
            "sql": sql,
            "columns": columns,
            "rows": [list(rows[0])],
            "answer": answer,
            "error": None,
            "chart_config": None,
            "context_summary": f"Top {request['stat_label']} leader in {competition_label}: {fielder} with {catches} catches via Cricsheet wickets",
            "new_fact": None,
            "display_hint": {"format": "stats", "stat_type": "fielding"},
            "sections": None,
            "model_used": "deterministic-fielding-leader",
        }

    # ── Scenario-based score prediction ─────────────────────────────────

    # Patterns to detect "scored X for Y in Z overs" style questions
    _PREDICT_SCORE_PATTERNS = [
        # "scored 150 for 3 in 14 overs" / "150 for 3 after 14 overs" / "150/3 in 14 overs"
        _re.compile(
            r'(\d+)\s*(?:for|/)\s*(\d+)\s*(?:in|after|at|of)\s*(\d+(?:\.\d+)?)\s*overs?',
            _re.IGNORECASE,
        ),
        # "150 runs, 3 wickets, 14 overs"
        _re.compile(
            r'(\d+)\s*runs?\b.*?(\d+)\s*wickets?\b.*?(\d+(?:\.\d+)?)\s*overs?',
            _re.IGNORECASE,
        ),
        # "at 14 overs scored 150/3"
        _re.compile(
            r'(?:at|after)\s*(\d+(?:\.\d+)?)\s*overs?\b.*?(\d+)\s*(?:for|/)\s*(\d+)',
            _re.IGNORECASE,
        ),
    ]
    _PREDICT_TRIGGER = _re.compile(
        r'predict|forecast|projected?\b|what.*(?:final|end|total).*score|estimated?\s+(?:score|total)',
        _re.IGNORECASE,
    )
    _FORMAT_EVENT_MAP = {
        "ipl": ("Indian Premier League", "T20", 20),
        "indian premier league": ("Indian Premier League", "T20", 20),
        "bbl": ("Big Bash League", "T20", 20),
        "big bash": ("Big Bash League", "T20", 20),
        "cpl": ("Caribbean Premier League", "T20", 20),
        "psl": ("Pakistan Super League", "T20", 20),
        "t20i": (None, "T20", 20),
        "t20": (None, "T20", 20),
        "odi": (None, "ODI", 50),
        "one day": (None, "ODI", 50),
        "test": (None, "Test", None),
    }

    def _detect_scenario_prediction(self, question: str) -> dict | None:
        """Parse a scenario-based score prediction question.

        Returns dict with keys: current_score, wickets, overs_completed,
        total_overs, event_name, match_type, team (optional).
        """
        q = question.lower()

        # Must be a prediction-type question
        if not self._PREDICT_TRIGGER.search(question):
            return None

        # Try each pattern to extract score/wickets/overs
        score = wickets = overs = None
        for i, pat in enumerate(self._PREDICT_SCORE_PATTERNS):
            m = pat.search(question)
            if m:
                if i == 2:  # "at 14 overs, 150/3" — groups reordered
                    overs, score, wickets = m.group(1), m.group(2), m.group(3)
                else:
                    score, wickets, overs = m.group(1), m.group(2), m.group(3)
                break

        if score is None:
            return None

        current_score = int(score)
        wickets_lost = int(wickets)
        overs_completed = float(overs)

        # Detect format
        total_overs = 20  # default T20
        event_name = None
        match_type = "T20"
        for keyword, (evt, mt, tot) in self._FORMAT_EVENT_MAP.items():
            if keyword in q:
                event_name = evt
                match_type = mt
                if tot:
                    total_overs = tot
                break

        # Detect team
        team = None
        for abbr, variants in self._TEAM_NAME_VARIANTS.items():
            for v in variants:
                if v.lower() in q:
                    team = v
                    break
            if team:
                break
        # Also check short abbreviations in the question
        team_abbrs = {
            "rr": "Rajasthan Royals", "csk": "Chennai Super Kings",
            "mi": "Mumbai Indians", "rcb": "Royal Challengers Bengaluru",
            "kkr": "Kolkata Knight Riders", "dc": "Delhi Capitals",
            "srh": "Sunrisers Hyderabad", "pbks": "Punjab Kings",
            "gt": "Gujarat Titans", "lsg": "Lucknow Super Giants",
        }
        if not team:
            for abbr, full_name in team_abbrs.items():
                # Match whole word only
                if _re.search(r'\b' + abbr + r'\b', q):
                    team = full_name
                    break

        return {
            "current_score": current_score,
            "wickets": wickets_lost,
            "overs_completed": overs_completed,
            "total_overs": total_overs,
            "event_name": event_name,
            "match_type": match_type,
            "team": team,
        }

    def _build_scenario_prediction_response(self, question: str) -> dict | None:
        """Build a score prediction using pre-computed WASP V(b,w) lookup tables.

        V(b,w) = average additional runs from ball b with w wickets lost.
        Prediction = current_score + V(ball_index, wickets_lost).
        """
        params = self._detect_scenario_prediction(question)
        if not params:
            return None

        current_score = params["current_score"]
        wickets = params["wickets"]
        overs_done = params["overs_completed"]
        total_overs = params["total_overs"]
        event_name = params["event_name"]
        match_type = params["match_type"]
        team = params["team"]

        if overs_done >= total_overs or overs_done < 1:
            return None

        # Convert overs to ball index (e.g. 14.0 overs = ball 84)
        whole_overs = int(overs_done)
        part_balls = round((overs_done - whole_overs) * 10)
        ball_idx = whole_overs * 6 + part_balls

        # Pick the best WASP table for this query
        if event_name and "Indian Premier League" in event_name:
            wasp_label = "IPL"
        elif match_type == "ODI":
            wasp_label = "ODI"
        else:
            wasp_label = "T20"

        # Main lookup
        v_now, n_now = self._wasp_predict(ball_idx, wickets, wasp_label)

        # Fallback to generic T20 if IPL/specific table had no data
        if v_now is None and wasp_label != "T20" and match_type != "ODI":
            wasp_label = "T20"
            v_now, n_now = self._wasp_predict(ball_idx, wickets, wasp_label)

        if v_now is None:
            return None

        predicted_total = round(current_score + v_now, 0)

        # Get V at start of innings for resource % calculation
        v_start, _ = self._wasp_predict(0, 0, wasp_label)

        # Confidence range: look up V at (ball, wickets-1) and (ball, wickets+1)
        v_optimistic, _ = self._wasp_predict(ball_idx, max(0, wickets - 1), wasp_label)
        v_pessimistic, _ = self._wasp_predict(ball_idx, min(9, wickets + 1), wasp_label)
        predicted_high = round(current_score + v_optimistic, 0) if v_optimistic else predicted_total
        predicted_low = round(current_score + v_pessimistic, 0) if v_pessimistic else predicted_total

        # Naive linear extrapolation for comparison
        run_rate = current_score / overs_done if overs_done > 0 else 0
        naive_predicted = round(run_rate * total_overs, 0)

        overs_remaining = total_overs - overs_done
        wickets_in_hand = 10 - wickets
        format_label = event_name or match_type

        # Resource % used (DLS-style insight)
        resource_used_pct = None
        if v_start and v_start > 0:
            resource_used_pct = round((1 - v_now / v_start) * 100, 1)

        answer_parts = [
            f"**Predicted final score: {predicted_total:.0f}**",
            f"",
            f"Using WASP (Winning and Score Predictor) model built from historical "
            f"{format_label} first-innings ball-by-ball data:",
            f"",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Current score | {current_score}/{wickets} in {overs_done} overs |",
            f"| Run rate | {run_rate:.2f} per over |",
            f"| WASP expected additional runs | {v_now:.1f} in {overs_remaining:.0f} overs |",
            f"| **WASP predicted total** | **{predicted_total:.0f}** |",
            f"| Likely range | {predicted_low:.0f} - {predicted_high:.0f} |",
            f"| Naive run-rate extrapolation | {naive_predicted:.0f} |",
            f"| Wickets in hand | {wickets_in_hand} |",
        ]
        if resource_used_pct is not None:
            answer_parts.append(
                f"| Resources used | {resource_used_pct:.1f}% |"
            )
        answer_parts.extend([
            f"| Data points at this state | {n_now:,} |",
            f"| Model | {wasp_label} WASP |",
        ])

        context_parts = [
            f"WASP[{wasp_label}] prediction at ball {ball_idx} ({overs_done} ov), "
            f"{wickets}w: V={v_now:.1f}, predicted={predicted_total:.0f}, "
            f"range={predicted_low:.0f}-{predicted_high:.0f}, "
            f"naive={naive_predicted:.0f}, n={n_now}."
        ]

        display_columns = [
            "predicted_total", "likely_low", "likely_high",
            "naive_extrapolation", "wasp_additional", "data_points",
        ]
        display_rows = [[
            predicted_total, predicted_low, predicted_high,
            naive_predicted, round(v_now, 1), n_now,
        ]]

        return {
            "question": question,
            "sql": f"-- WASP[{wasp_label}] V(ball={ball_idx}, wickets={wickets}) = {v_now:.2f}",
            "columns": display_columns,
            "rows": display_rows,
            "answer": "\n".join(answer_parts),
            "error": None,
            "chart_config": None,
            "context_summary": " ".join(context_parts),
            "new_fact": None,
            "display_hint": {"format": "prediction"},
            "sections": None,
            "model_used": f"wasp-{wasp_label.lower()}",
        }

    @classmethod
    def _normalize_team_names(cls, sql: str) -> str:
        """Expand renamed IPL team names so SQL matches all historical variants.

        Handles both ``= 'Name'`` and ``IN ('Name', ...)`` patterns.
        """
        processed: set[frozenset[str]] = set()

        for name, variants in cls._TEAM_NAME_VARIANTS.items():
            vkey = frozenset(variants)
            if vkey in processed:
                continue
            # Only act when at least one variant is quoted in the SQL
            if not any(f"'{v}'" in sql for v in variants):
                continue
            processed.add(vkey)

            in_list = ", ".join(f"'{v}'" for v in variants)

            # 1) ``= 'AnyVariant'``  →  ``IN ('v1', 'v2', ...)``
            for v in variants:
                sql = sql.replace(f"= '{v}'", f"IN ({in_list})")

            # 2) Inside existing IN(...) clauses, inject missing variants
            def _expand_in(m: _re.Match, _variants: list[str] = variants) -> str:
                body = m.group(1)
                present = [v for v in _variants if f"'{v}'" in body]
                if not present:
                    return m.group(0)
                missing = [v for v in _variants if f"'{v}'" not in body]
                if not missing:
                    return m.group(0)
                extras = ", ".join(f"'{v}'" for v in missing)
                return f"IN ({body}, {extras})"

            sql = _re.sub(r"IN\s*\(([^)]+)\)", _expand_in, sql, flags=_re.IGNORECASE)

        return sql

    def _execute_sql(self, sql: str) -> tuple[list[str], list[tuple]]:
        """Execute SQL and return (column_names, rows)."""
        sql = self._normalize_duckdb_date_arithmetic(sql)
        sql = self._normalize_team_names(sql)
        con = self._get_connection()
        try:
            result = con.execute(sql)
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
            return columns, rows
        finally:
            con.close()

    @staticmethod
    def _prune_empty_metric_rows(columns: list[str], rows: list[list]) -> list[list]:
        """Drop rows whose non-identifier fields are entirely null when better rows exist."""
        if len(rows) <= 1 or not columns:
            return rows

        identifier_tokens = (
            "name",
            "player",
            "team",
            "format",
            "competition",
            "season",
            "year",
            "match",
            "innings",
        )
        metric_indexes = [
            idx for idx, col in enumerate(columns)
            if not any(token in col.lower().replace('"', '') for token in identifier_tokens)
        ]
        if not metric_indexes:
            return rows

        filtered_rows = [
            row for row in rows
            if any(row[idx] is not None and row[idx] != "" for idx in metric_indexes)
        ]
        return filtered_rows or rows

    def _format_results(self, columns: list[str], rows: list[tuple]) -> str:
        """Format query results as a readable string for the LLM."""
        if not rows:
            return "No results found."
        lines = [" | ".join(columns)]
        lines.append("-" * len(lines[0]))
        for row in rows[:50]:
            lines.append(" | ".join(str(v) if v is not None else "N/A" for v in row))
        if len(rows) > 50:
            lines.append(f"... and {len(rows) - 50} more rows")
        return "\n".join(lines)

    def _generate_narrative(self, question: str, sql: str, data_text: str) -> tuple[str, dict | None, str | None, str | None, dict | None]:
        """Ask GPT-4.1 to narrate the results. Returns (narrative, chart_config, context_summary, new_fact, display_hint)."""
        user_msg = f"""Question: {question}

SQL query used:
{sql}

Results:
{data_text}"""

        raw = self._call_llm([
                {"role": "system", "content": self._get_narrative_prompt()},
                {"role": "user", "content": user_msg},
            ], temperature=0.3)

        # Parse chart config, context summary, new facts, and display hint
        chart_config = None
        context_summary = None
        new_fact = None
        display_hint = None
        narrative = raw
        for line in raw.split("\n"):
            stripped = line.strip()
            if stripped.startswith("CHART_CONFIG:"):
                try:
                    chart_config = json.loads(stripped[len("CHART_CONFIG:"):])
                except (json.JSONDecodeError, ValueError):
                    pass
                narrative = narrative.replace(line, "")
            elif stripped.startswith("CONTEXT_SUMMARY:"):
                context_summary = stripped[len("CONTEXT_SUMMARY:"):].strip()
                narrative = narrative.replace(line, "")
            elif stripped.startswith("NEW_FACT:"):
                new_fact = stripped[len("NEW_FACT:"):].strip()[:200]
                narrative = narrative.replace(line, "")
            elif stripped.startswith("DISPLAY_HINT:"):
                try:
                    display_hint = json.loads(stripped[len("DISPLAY_HINT:"):])
                except (json.JSONDecodeError, ValueError):
                    pass
                narrative = narrative.replace(line, "")
        narrative = narrative.strip()

        return narrative, chart_config, context_summary, new_fact, display_hint

    # ── Template narrative (skip LLM for simple results) ───────────────────

    @staticmethod
    def _template_narrative(
        question: str, sql: str, columns: list[str], rows: list
    ) -> tuple[str, dict | None, str | None, str | None, dict | None] | None:
        """Generate narrative without LLM for simple (0 or 1 row) results.

        Returns (narrative, chart_config, context_summary, new_fact, display_hint)
        or None if LLM should be used instead.
        """
        # Empty results
        if not rows:
            return (
                "No matching records found in the database for this query.",
                None,
                f"No results for: {question[:60]}",
                None,
                {"format": "stats", "stat_type": "match"},
            )

        # Only template single-row results
        if len(rows) != 1:
            return None

        # Skip template for scorecard-like queries (let LLM handle)
        col_names_lower = {c.lower().replace(" ", "_").replace('"', "") for c in columns}
        if "match_id" in col_names_lower:
            return None

        row = rows[0]
        pairs = [(col, val) for col, val in zip(columns, row) if val is not None]
        if not pairs:
            return (
                "The query returned a result with no data values.",
                None, None, None,
                {"format": "stats", "stat_type": "match"},
            )

        # Build natural prose from column-value pairs
        parts = []
        for col, val in pairs:
            label = col.replace("_", " ").strip()
            if isinstance(val, float):
                parts.append(f"{label} is {val:g}")
            else:
                parts.append(f"{label} is {val}")

        if len(parts) == 1:
            narrative = f"Based on the available data, the {parts[0]}."
        elif len(parts) == 2:
            narrative = f"Based on the available data, the {parts[0]} and the {parts[1]}."
        else:
            narrative = "Based on the available data, the " + ", ".join(parts[:-1]) + f", and the {parts[-1]}."

        # Detect stat_type from column names
        bat_cols = col_names_lower & {"batting_avg", "runs", "centuries", "strike_rate", "fifties", "highest_score", "total_runs", "runs_scored", "balls_faced", "sixes", "fours"}
        bowl_cols = col_names_lower & {"bowling_avg", "economy", "wickets", "overs_bowled", "bowling_sr", "runs_conceded", "total_wickets", "maidens"}
        if bat_cols and bowl_cols:
            stat_type = "allround"
        elif bat_cols:
            stat_type = "batting"
        elif bowl_cols:
            stat_type = "bowling"
        elif col_names_lower & {
            "win_percent", "win_percentage", "wins", "losses", "win_pct",
            "win_percent_csk", "win_percent_rcb", "matches_played",
        }:
            stat_type = "team"
        else:
            stat_type = "match"

        display_hint = {"format": "stats", "stat_type": stat_type}

        # Build context summary
        summary_parts = [f"{col}={val}" for col, val in pairs[:4]]
        context_summary = "Result: " + ", ".join(summary_parts)
        if "kaggle" in (sql or "").lower():
            context_summary += " (via Kaggle scorecards)"
        else:
            context_summary += " (via Cricsheet data)"

        return narrative, None, context_summary, None, display_hint

    # ── Profile detection patterns ─────────────────────────────────────────
    _PROFILE_PATTERNS = [
        _re.compile(r"^(?:tell\s+me\s+about|who\s+is|who\s+was|about|profile\s+(?:of|for)?|info\s+(?:on|about))\s+(.+)", _re.IGNORECASE),
        _re.compile(r"^(?:(?:show|give)\s+me\s+)?(?:the\s+)?(?:(?:player|career)\s+)?(?:summary|profile|bio(?:graphy)?|details?|stats?|record)\s+(?:of|for|about|on)\s+(.+)", _re.IGNORECASE),
        _re.compile(r"^(?:career\s+)?(?:summary|stats?|record)\s+(?:of|for)\s+(.+)", _re.IGNORECASE),
        _re.compile(r"^(?:summarize|describe)\s+(.+?)(?:\s+(?:as\s+a\s+)?player)?$", _re.IGNORECASE),
        _re.compile(r"^(.+?)(?:\s+(?:player\s+)?summary|\s+career\s+(?:summary|stats?|record)|\s+profile|\s+bio(?:graphy)?|\s+info(?:rmation)?)$", _re.IGNORECASE),
    ]

    def _detect_profile_query(self, question: str) -> str | None:
        """Check if the question is asking for a player's profile. Returns the player name or None."""
        q = question.strip().rstrip("?. ")
        for pat in self._PROFILE_PATTERNS:
            m = pat.match(q)
            if m:
                return m.group(1).strip()
        return None

    def _build_profile_response(self, question: str, player_name: str, cricsheet_name: str) -> dict | None:
        """Look up player_profiles and return a profile card response if found, including career stats."""
        try:
            con = self._get_connection()
            row = con.execute("""
                SELECT pp.full_name, pp.display_name, pp.batting_style, pp.bowling_style,
                       pp.playing_role, pp.country, pp.dob, pp.debut_year, pp.is_active,
                       pp.birth_place, pp.jersey_number, pp.major_teams, pp.headshot_url,
                       pp.gender
                FROM players p
                JOIN player_profiles pp ON p.cricsheet_id = pp.cricsheet_id
                WHERE p.name = ?
                LIMIT 1
            """, [cricsheet_name]).fetchone()

            if not row:
                # No profile row yet – build a minimal stub so stats still render
                profile_data = {
                    "full_name": player_name,
                    "display_name": player_name,
                    "batting_style": None,
                    "bowling_style": None,
                    "playing_role": None,
                    "country": None,
                    "dob": None,
                    "debut_year": None,
                    "is_active": None,
                    "birth_place": None,
                    "jersey_number": None,
                    "major_teams": None,
                    "headshot_url": None,
                    "gender": None,
                }
            else:
                profile_data = {
                    "full_name": row[0] or player_name,
                    "display_name": row[1] or player_name,
                    "batting_style": row[2],
                    "bowling_style": row[3],
                    "playing_role": row[4],
                    "country": row[5],
                    "dob": str(row[6]) if row[6] else None,
                    "debut_year": row[7],
                    "is_active": row[8],
                    "birth_place": row[9],
                    "jersey_number": row[10],
                    "major_teams": row[11],
                    "headshot_url": row[12],
                    "gender": row[13],
                }

            profile_data["major_teams"] = self._format_major_teams(profile_data.get("major_teams"))
            espn_test_summary = self._fetch_espn_test_career_summary(
                self._extract_espn_athlete_id(profile_data.get("headshot_url"))
            )

            coverage_notes: list[str] = []

            def add_coverage_note(note: str | None):
                if note and note not in coverage_notes:
                    coverage_notes.append(note)

            coverage_rows = con.execute("""
                SELECT format, MIN(match_year) AS first_year
                FROM (
                    SELECT
                        CASE
                            WHEN match_type IN ('ODI', 'ODM') THEN 'ODI'
                            WHEN match_type IN ('T20', 'IT20') THEN 'T20I'
                            ELSE NULL
                        END AS format,
                        EXTRACT(YEAR FROM date_start) AS match_year
                    FROM matches
                    WHERE team_type = 'international'
                      AND match_type IN ('ODI', 'ODM', 'T20', 'IT20')
                ) src
                WHERE format IS NOT NULL
                GROUP BY format
            """).fetchall()
            coverage_starts = {r[0]: int(r[1]) for r in coverage_rows if r[0] and r[1] is not None}

            kaggle_player_id = None
            kaggle_player_name = player_name
            try:
                kaggle_row = con.execute("""
                    SELECT pm.kaggle_player_id, kp.player_name
                    FROM player_map pm
                    LEFT JOIN kaggle_players kp ON pm.kaggle_player_id = kp.player_id
                    WHERE pm.cricsheet_name = ?
                    LIMIT 1
                """, [cricsheet_name]).fetchone()
                if kaggle_row:
                    kaggle_player_id = kaggle_row[0]
                    if kaggle_row[1]:
                        kaggle_player_name = kaggle_row[1]
            except Exception:
                pass
            if not kaggle_player_id:
                try:
                    kaggle_row = con.execute("""
                        SELECT player_id, player_name
                        FROM kaggle_players
                        WHERE player_name ILIKE ?
                        ORDER BY CASE WHEN lower(player_name) = lower(?) THEN 0 ELSE 1 END, player_name
                        LIMIT 1
                    """, [f"%{player_name}%", player_name]).fetchone()
                    if kaggle_row:
                        kaggle_player_id = kaggle_row[0]
                        kaggle_player_name = kaggle_row[1] or player_name
                except Exception:
                    pass

            career_start_year = profile_data.get("debut_year")
            if not career_start_year and kaggle_player_id:
                try:
                    year_row = con.execute("""
                        SELECT MIN(start_year)
                        FROM (
                            SELECT MIN(EXTRACT(YEAR FROM km."Match Start Date")) AS start_year
                            FROM kaggle_batting kb
                            JOIN kaggle_matches km ON kb."Match ID" = km."Match ID"
                            WHERE kb.batsman = ?
                            UNION ALL
                            SELECT MIN(EXTRACT(YEAR FROM km."Match Start Date")) AS start_year
                            FROM kaggle_bowling kb
                            JOIN kaggle_matches km ON kb."Match ID" = km."Match ID"
                            WHERE kb."bowler id" = ?
                        ) src
                    """, [kaggle_player_id, kaggle_player_id]).fetchone()
                    if year_row and year_row[0] is not None:
                        career_start_year = int(year_row[0])
                except Exception:
                    pass
            if not career_start_year:
                try:
                    year_row = con.execute("""
                        SELECT MIN(start_year)
                        FROM (
                            SELECT MIN(EXTRACT(YEAR FROM m.date_start)) AS start_year
                            FROM deliveries d
                            JOIN matches m ON d.match_id = m.match_id
                            WHERE d.batter = ?
                            UNION ALL
                            SELECT MIN(EXTRACT(YEAR FROM m.date_start)) AS start_year
                            FROM deliveries d
                            JOIN matches m ON d.match_id = m.match_id
                            WHERE d.bowler = ?
                        ) src
                    """, [cricsheet_name, cricsheet_name]).fetchone()
                    if year_row and year_row[0] is not None:
                        career_start_year = int(year_row[0])
                except Exception:
                    pass

            # ── Format-wise batting stats ──────────────────────────────
            batting_stats = []
            if kaggle_player_id:
                try:
                    test_bat = con.execute("""
                        SELECT
                            COUNT(DISTINCT kb."Match ID") AS matches,
                            COUNT(*) FILTER (
                                WHERE kb.runs IS NOT NULL OR kb.balls IS NOT NULL OR COALESCE(kb.isOut, FALSE)
                            ) AS innings,
                            COUNT(*) FILTER (WHERE COALESCE(kb.isOut, FALSE)) AS dismissals,
                            COALESCE(SUM(kb.runs), 0) AS runs,
                            COALESCE(SUM(kb.balls), 0) AS balls_faced,
                            MAX(kb.runs) AS highest_score,
                            ROUND(COALESCE(SUM(kb.runs), 0) * 1.0 / NULLIF(COUNT(*) FILTER (WHERE COALESCE(kb.isOut, FALSE)), 0), 2) AS batting_avg,
                            ROUND(COALESCE(SUM(kb.runs), 0) * 100.0 / NULLIF(COALESCE(SUM(kb.balls), 0), 0), 2) AS strike_rate,
                            COUNT(*) FILTER (WHERE kb.runs >= 100) AS centuries,
                            COUNT(*) FILTER (WHERE kb.runs >= 50 AND kb.runs < 100) AS fifties
                        FROM kaggle_batting kb
                        WHERE kb.batsman = ?
                    """, [kaggle_player_id]).fetchone()
                    if test_bat and (test_bat[0] or 0) > 0:
                        dismissals = test_bat[2] or 0
                        innings = test_bat[1] or 0
                        matches = test_bat[0]
                        batting_avg = float(test_bat[6]) if test_bat[6] is not None else None
                        centuries = test_bat[8]
                        fifties = test_bat[9]
                        test_bat_note = None
                        if espn_test_summary and espn_test_summary.get("batting"):
                            espn_bat = espn_test_summary["batting"]
                            if espn_bat.get("matches") and espn_bat["matches"] != matches:
                                test_bat_note = "Test appearance totals are cross-checked with ESPN Cricinfo because the local Test scorecards can miss non-batting appearances."
                            matches = espn_bat.get("matches") or matches
                            innings = espn_bat.get("innings") or innings
                            if espn_bat.get("not_outs") is not None:
                                dismissals = max(innings - espn_bat["not_outs"], 0)
                            batting_avg = espn_bat.get("batting_avg") or batting_avg
                            centuries = espn_bat.get("centuries") or centuries
                            fifties = espn_bat.get("fifties") or fifties
                        add_coverage_note(test_bat_note)
                        batting_stats.append({
                            "format": "Test",
                            "matches": matches,
                            "innings": innings,
                            "not_outs": max(innings - dismissals, 0),
                            "runs": test_bat[3],
                            "balls_faced": test_bat[4],
                            "highest_score": test_bat[5],
                            "batting_avg": batting_avg,
                            "strike_rate": float(test_bat[7]) if test_bat[7] is not None else None,
                            "centuries": centuries,
                            "fifties": fifties,
                            "source": "Kaggle Test scorecards",
                            "coverage_note": test_bat_note,
                            "is_partial": False,
                        })
                except Exception:
                    pass
            try:
                cricsheet_batting_formats = "('ODI', 'T20I')" if kaggle_player_id else "('Test', 'ODI', 'T20I')"
                bat_rows = con.execute(f"""
                    WITH innings_stats AS (
                        SELECT d.batter,
                               CASE m.match_type WHEN 'Test' THEN 'Test' WHEN 'ODI' THEN 'ODI'
                                    WHEN 'T20' THEN 'T20I' WHEN 'IT20' THEN 'T20I'
                                    WHEN 'ODM' THEN 'ODI' ELSE m.match_type END AS format,
                               EXTRACT(YEAR FROM m.date_start) AS match_year,
                               d.match_id, d.innings_num,
                               SUM(d.runs_batter) AS runs,
                               COUNT(*) FILTER (WHERE d.extras_wides = 0) AS balls
                        FROM deliveries d
                        JOIN matches m ON d.match_id = m.match_id
                        WHERE d.batter = ? AND m.team_type = 'international'
                        GROUP BY d.batter, format, match_year, d.match_id, d.innings_num
                    ),
                    dismissals AS (
                        SELECT w.player_out AS batter,
                               CASE m.match_type WHEN 'Test' THEN 'Test' WHEN 'ODI' THEN 'ODI'
                                    WHEN 'T20' THEN 'T20I' WHEN 'IT20' THEN 'T20I'
                                    WHEN 'ODM' THEN 'ODI' ELSE m.match_type END AS format,
                               COUNT(*) AS outs
                        FROM wickets w
                        JOIN matches m ON w.match_id = m.match_id
                        WHERE w.player_out = ? AND m.team_type = 'international'
                          AND w.kind NOT IN ('retired hurt', 'retired out')
                        GROUP BY w.player_out, format
                    )
                    SELECT
                        i.format,
                        COUNT(DISTINCT i.match_id) AS matches,
                        COUNT(DISTINCT (i.match_id, i.innings_num)) AS innings,
                        SUM(i.runs) AS runs,
                        SUM(i.balls) AS balls_faced,
                        COALESCE(d.outs, 0) AS dismissals,
                        MAX(i.runs) AS highest_score,
                        ROUND(SUM(i.runs) * 1.0 / NULLIF(COALESCE(d.outs, 0), 0), 2) AS batting_avg,
                        ROUND(SUM(i.runs) * 100.0 / NULLIF(SUM(i.balls), 0), 2) AS strike_rate,
                        COUNT(*) FILTER (WHERE i.runs >= 100) AS centuries,
                        COUNT(*) FILTER (WHERE i.runs >= 50 AND i.runs < 100) AS fifties,
                        MIN(i.match_year) AS first_year
                    FROM innings_stats i
                    LEFT JOIN dismissals d ON i.batter = d.batter AND i.format = d.format
                    WHERE i.format IN {cricsheet_batting_formats}
                    GROUP BY i.format, d.outs
                    ORDER BY CASE i.format WHEN 'Test' THEN 1 WHEN 'ODI' THEN 2 WHEN 'T20I' THEN 3 ELSE 4 END
                """, [cricsheet_name, cricsheet_name]).fetchall()
                for br in bat_rows:
                    not_outs = (br[2] or 0) - (br[5] or 0)
                    format_name = br[0]
                    first_year = int(br[11]) if br[11] is not None else None
                    coverage_note = None
                    is_partial = False
                    if format_name == "Test" and not kaggle_player_id:
                        coverage_note = "Test stats are using Cricsheet ball-by-ball records because no Kaggle Test mapping was found, so earlier matches may be missing."
                        is_partial = True
                    elif format_name == "ODI":
                        coverage_start = coverage_starts.get("ODI")
                        if coverage_start and career_start_year and first_year and career_start_year < coverage_start and first_year <= coverage_start:
                            coverage_note = f"ODI stats are from Cricsheet ball-by-ball records starting in {coverage_start}, so earlier ODI matches may be missing for this player."
                            is_partial = True
                    add_coverage_note(coverage_note)
                    batting_stats.append({
                        "format": format_name, "matches": br[1], "innings": br[2],
                        "not_outs": max(not_outs, 0), "runs": br[3], "balls_faced": br[4],
                        "highest_score": br[6], "batting_avg": float(br[7]) if br[7] else None,
                        "strike_rate": float(br[8]) if br[8] else None,
                        "centuries": br[9], "fifties": br[10],
                        "source": "Cricsheet ball-by-ball",
                        "coverage_note": coverage_note,
                        "is_partial": is_partial,
                    })
            except Exception:
                pass

            # ── Format-wise bowling stats ──────────────────────────────
            bowling_stats = []
            if kaggle_player_id:
                try:
                    test_bowl = con.execute("""
                        SELECT
                            COUNT(DISTINCT kb."Match ID") AS matches,
                            COUNT(*) AS innings_bowled,
                            COALESCE(SUM(kb.balls), 0) AS total_balls,
                            COALESCE(SUM(kb.conceded), 0) AS total_runs,
                            COALESCE(SUM(kb.wickets), 0) AS total_wickets,
                            ROUND(COALESCE(SUM(kb.conceded), 0) * 1.0 / NULLIF(COALESCE(SUM(kb.wickets), 0), 0), 2) AS bowling_avg,
                            ROUND(COALESCE(SUM(kb.conceded), 0) * 6.0 / NULLIF(COALESCE(SUM(kb.balls), 0), 0), 2) AS economy,
                            ROUND(COALESCE(SUM(kb.balls), 0) * 1.0 / NULLIF(COALESCE(SUM(kb.wickets), 0), 0), 2) AS bowling_sr,
                            MAX(kb.wickets) AS best_innings_wickets
                        FROM kaggle_bowling kb
                        WHERE kb."bowler id" = ?
                    """, [kaggle_player_id]).fetchone()
                    if test_bowl and (test_bowl[4] or 0) > 0:
                        legal_b = test_bowl[2] or 0
                        overs_str = f"{legal_b // 6}.{legal_b % 6}" if legal_b else "0.0"
                        matches = test_bowl[0]
                        innings_bowled = test_bowl[1]
                        bowling_avg = float(test_bowl[5]) if test_bowl[5] is not None else None
                        economy = float(test_bowl[6]) if test_bowl[6] is not None else None
                        bowling_sr = float(test_bowl[7]) if test_bowl[7] is not None else None
                        best_innings_wickets = test_bowl[8]
                        test_bowl_note = None
                        if espn_test_summary and espn_test_summary.get("bowling"):
                            espn_bowl = espn_test_summary["bowling"]
                            if espn_bowl.get("matches") and espn_bowl["matches"] != matches:
                                test_bowl_note = "Test appearance totals are cross-checked with ESPN Cricinfo because the local Test scorecards can miss non-batting appearances."
                            matches = espn_bowl.get("matches") or matches
                            innings_bowled = espn_bowl.get("innings") or innings_bowled
                            overs_str = espn_bowl.get("overs") or overs_str
                            bowling_avg = espn_bowl.get("bowling_avg") or bowling_avg
                            economy = espn_bowl.get("economy") or economy
                            bowling_sr = espn_bowl.get("bowling_sr") or bowling_sr
                            best_innings_wickets = espn_bowl.get("best_innings_wickets") or best_innings_wickets
                        add_coverage_note(test_bowl_note)
                        bowling_stats.append({
                            "format": "Test",
                            "matches": matches,
                            "innings_bowled": innings_bowled,
                            "overs": overs_str,
                            "runs_conceded": test_bowl[3],
                            "wickets": test_bowl[4],
                            "bowling_avg": bowling_avg,
                            "economy": economy,
                            "bowling_sr": bowling_sr,
                            "best_innings_wickets": best_innings_wickets,
                            "source": "Kaggle Test scorecards",
                            "coverage_note": test_bowl_note,
                            "is_partial": False,
                        })
                except Exception:
                    pass
            try:
                cricsheet_bowling_formats = "('ODI', 'T20I')" if kaggle_player_id else "('Test', 'ODI', 'T20I')"
                bowl_rows = con.execute(f"""
                    WITH bowl_agg AS (
                        SELECT d.bowler,
                            CASE m.match_type WHEN 'Test' THEN 'Test' WHEN 'ODI' THEN 'ODI'
                                 WHEN 'T20' THEN 'T20I' WHEN 'IT20' THEN 'T20I'
                                 WHEN 'ODM' THEN 'ODI' ELSE m.match_type END AS format,
                            EXTRACT(YEAR FROM m.date_start) AS match_year,
                            d.match_id, d.innings_num,
                            COUNT(*) FILTER (WHERE d.extras_wides = 0 AND d.extras_noballs = 0) AS legal_balls,
                            SUM(d.runs_total) AS runs_conceded
                        FROM deliveries d
                        JOIN matches m ON d.match_id = m.match_id
                        WHERE d.bowler = ? AND m.team_type = 'international'
                        GROUP BY d.bowler, format, match_year, d.match_id, d.innings_num
                    ),
                    bowl_wickets AS (
                        SELECT d.bowler,
                            CASE m.match_type WHEN 'Test' THEN 'Test' WHEN 'ODI' THEN 'ODI'
                                 WHEN 'T20' THEN 'T20I' WHEN 'IT20' THEN 'T20I'
                                 WHEN 'ODM' THEN 'ODI' ELSE m.match_type END AS format,
                            d.match_id, d.innings_num,
                            COUNT(*) AS wkts
                        FROM wickets w
                        JOIN deliveries d ON w.match_id = d.match_id AND w.innings_num = d.innings_num
                            AND w.over_num = d.over_num AND w.ball_num = d.ball_num
                        JOIN matches m ON w.match_id = m.match_id
                        WHERE d.bowler = ? AND m.team_type = 'international'
                          AND w.kind NOT IN ('run out', 'retired hurt', 'obstructing the field', 'retired out')
                        GROUP BY d.bowler, format, d.match_id, d.innings_num
                    )
                    SELECT
                        ba.format,
                        COUNT(DISTINCT ba.match_id) AS matches,
                        COUNT(DISTINCT (ba.match_id, ba.innings_num)) AS innings_bowled,
                        SUM(ba.legal_balls) AS total_legal_balls,
                        SUM(ba.runs_conceded) AS total_runs,
                        COALESCE(SUM(bw.wkts), 0) AS total_wickets,
                        ROUND(SUM(ba.runs_conceded) * 1.0 / NULLIF(COALESCE(SUM(bw.wkts), 0), 0), 2) AS bowling_avg,
                        ROUND(SUM(ba.runs_conceded) * 6.0 / NULLIF(SUM(ba.legal_balls), 0), 2) AS economy,
                        ROUND(SUM(ba.legal_balls) * 1.0 / NULLIF(COALESCE(SUM(bw.wkts), 0), 0), 2) AS bowling_sr,
                        MAX(bw.wkts) AS best_innings_wickets,
                        MIN(ba.match_year) AS first_year
                    FROM bowl_agg ba
                    LEFT JOIN bowl_wickets bw ON ba.bowler = bw.bowler AND ba.format = bw.format
                        AND ba.match_id = bw.match_id AND ba.innings_num = bw.innings_num
                    WHERE ba.format IN {cricsheet_bowling_formats}
                    GROUP BY ba.format
                    ORDER BY CASE ba.format WHEN 'Test' THEN 1 WHEN 'ODI' THEN 2 WHEN 'T20I' THEN 3 ELSE 4 END
                """, [cricsheet_name, cricsheet_name]).fetchall()
                for bw in bowl_rows:
                    if (bw[5] or 0) > 0:  # only include if they have taken at least 1 wicket
                        legal_b = bw[3] or 0
                        overs_str = f"{legal_b // 6}.{legal_b % 6}" if legal_b else "0.0"
                        format_name = bw[0]
                        first_year = int(bw[10]) if bw[10] is not None else None
                        coverage_note = None
                        is_partial = False
                        if format_name == "Test" and not kaggle_player_id:
                            coverage_note = "Test bowling stats are using Cricsheet ball-by-ball records because no Kaggle Test mapping was found, so earlier matches may be missing."
                            is_partial = True
                        elif format_name == "ODI":
                            coverage_start = coverage_starts.get("ODI")
                            if coverage_start and career_start_year and first_year and career_start_year < coverage_start and first_year <= coverage_start:
                                coverage_note = f"ODI bowling stats are from Cricsheet ball-by-ball records starting in {coverage_start}, so earlier ODI matches may be missing for this player."
                                is_partial = True
                        add_coverage_note(coverage_note)
                        bowling_stats.append({
                            "format": format_name, "matches": bw[1], "innings_bowled": bw[2],
                            "overs": overs_str, "runs_conceded": bw[4],
                            "wickets": bw[5],
                            "bowling_avg": float(bw[6]) if bw[6] else None,
                            "economy": float(bw[7]) if bw[7] else None,
                            "bowling_sr": float(bw[8]) if bw[8] else None,
                            "best_innings_wickets": bw[9],
                            "source": "Cricsheet ball-by-ball",
                            "coverage_note": coverage_note,
                            "is_partial": is_partial,
                        })
            except Exception:
                pass

            con.close()

            profile_data["batting_stats"] = batting_stats
            profile_data["bowling_stats"] = bowling_stats
            profile_data["coverage_notes"] = coverage_notes
            profile_data["test_stat_source"] = "Kaggle Test scorecards" if kaggle_player_id else "Cricsheet ball-by-ball"
            profile_data["resolved_stat_name"] = {"cricsheet_name": cricsheet_name, "kaggle_name": kaggle_player_name}

            # Build a natural language summary
            name = profile_data["display_name"] or profile_data["full_name"]
            parts = []
            if profile_data["playing_role"]:
                parts.append(profile_data["playing_role"])
            if profile_data["country"]:
                parts.append(f"from {profile_data['country']}")
            desc = f"{name} is a {'former ' if profile_data.get('is_active') is False else ''}{', '.join(parts)}." if parts else f"{name}."

            details = []
            if profile_data["batting_style"]:
                details.append(f"Batting: {profile_data['batting_style']}")
            if profile_data["bowling_style"]:
                details.append(f"Bowling: {profile_data['bowling_style']}")
            if profile_data["debut_year"]:
                details.append(f"Debut: {profile_data['debut_year']}")
            if profile_data["birth_place"]:
                details.append(f"Born: {profile_data['birth_place']}")
            if profile_data["major_teams"]:
                details.append(f"Teams: {profile_data['major_teams']}")

            answer = desc
            if details:
                answer += " " + ". ".join(details) + "."

            # Append brief career highlights to narrative
            if batting_stats:
                for bs in batting_stats:
                    answer += f" {bs['format']}: {bs['runs']} runs in {bs['innings']} innings"
                    if bs.get('batting_avg'):
                        answer += f" (avg {bs['batting_avg']})"
                    if bs.get('centuries'):
                        answer += f", {bs['centuries']} centuries"
                    answer += "."
            if bowling_stats:
                for bws in bowling_stats:
                    answer += f" {bws['format']}: {bws['wickets']} wickets"
                    if bws.get('bowling_avg'):
                        answer += f" (avg {bws['bowling_avg']})"
                    if bws.get('economy'):
                        answer += f", econ {bws['economy']}"
                    answer += "."
            if coverage_notes:
                answer += " Note: " + " ".join(coverage_notes)

            return {
                "question": question,
                "sql": None,
                "columns": [],
                "rows": [],
                "answer": answer,
                "error": None,
                "chart_config": None,
                "context_summary": f"Profile of {name}" + (f", {profile_data['country']}" if profile_data['country'] else ""),
                "new_fact": None,
                "display_hint": {"format": "profile", "stat_type": "batting"},
                "sections": None,
                "model_used": None,
                "candidates": None,
                "original_question": None,
                "profile": profile_data,
            }
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("_build_profile_response failed: %s", exc, exc_info=True)
            return None

    # ── Career record template (zero LLM calls) ──────────────────────────

    _RECORD_QUERY_PATTERN = _re.compile(
        r"\b(?:record|stats?|statistics|career|performance|numbers?)\b",
        _re.IGNORECASE,
    )
    _PLAYER_BATTING_SUMMARY_PATTERN = _re.compile(
        r"\b(?:batting|batter|runs?|average|avg|strike\s*rate|sr|balls?\s*faced|dismissals?|not\s*out|centur(?:y|ies)|fift(?:y|ies)|form)\b",
        _re.IGNORECASE,
    )
    _PLAYER_BOWLING_SUMMARY_PATTERN = _re.compile(
        r"\b(?:bowling|bowler|wickets?|economy|maiden|maidens|conceded|bowling\s+average|bowling\s+strike\s+rate)\b",
        _re.IGNORECASE,
    )
    _PLAYER_FIELDING_SUMMARY_PATTERN = _re.compile(
        r"\b(?:fielding|catches?|stumpings?|run\s+outs?|keeper|dismissals?\s+as\s+(?:keeper|fielder))\b",
        _re.IGNORECASE,
    )
    _RELATIVE_DATE_WINDOW_PATTERN = _re.compile(
        r"\b(?:last|past)\s+(\d+)\s*(day|days|week|weeks|month|months|year|years)\b",
        _re.IGNORECASE,
    )
    _RELATIVE_DATE_WINDOW_SINGLE_PATTERN = _re.compile(
        r"\b(?:last|past)\s+(day|week|month|year)\b",
        _re.IGNORECASE,
    )

    # Event-name mapping for franchise/format filters
    _EVENT_FILTER_MAP = {
        "ipl": ("m.event_name = 'Indian Premier League'", "IPL"),
        "indian premier league": ("m.event_name = 'Indian Premier League'", "IPL"),
        "bbl": ("m.event_name = 'Big Bash League'", "BBL"),
        "cpl": ("m.event_name = 'Caribbean Premier League'", "CPL"),
        "psl": ("m.event_name = 'Pakistan Super League'", "PSL"),
        "wpl": ("m.event_name = 'Womens Premier League'", "WPL"),
        "t20i": ("m.match_type = 'T20'  AND m.team_type = 'international'", "T20I"),
        "t20": ("m.match_type = 'T20'", "T20"),
        "odi": ("m.match_type = 'ODI'", "ODI"),
        "test": ("m.match_type = 'Test'", "Test"),
    }

    def _detect_event_filter(self, question: str) -> tuple[str, str]:
        """Detect the competition/format filter from the question.
        Returns (sql_filter, label) or ('', 'all formats') if none detected.
        """
        q_lower = question.lower()
        for key, (sql_filt, label) in self._EVENT_FILTER_MAP.items():
            if key in q_lower:
                return sql_filt, label
        return "", "all formats"

    def _resolve_event_filter_from_context(self, question: str,
                                           history: list[dict] | None = None) -> tuple[str, str]:
        """Resolve a competition filter from the current turn, falling back to history."""
        candidate_texts = [question]
        for turn in reversed(history or []):
            candidate_texts.append(turn.get("question", ""))

        for text in candidate_texts:
            if not text:
                continue
            sql_filter, label = self._detect_event_filter(text)
            if sql_filter:
                return sql_filter, label

        return "", "all formats"

    @classmethod
    def _resolve_relative_date_filter(cls, question: str,
                                      history: list[dict] | None = None) -> tuple[str | None, str | None]:
        """Resolve a recent time window like last year or past 3 months."""
        candidate_texts = [question]
        for turn in reversed(history or []):
            candidate_texts.append(turn.get("question", ""))

        for text in candidate_texts:
            if not text:
                continue

            amount = None
            unit_text = None

            match = cls._RELATIVE_DATE_WINDOW_PATTERN.search(text)
            if match:
                amount = int(match.group(1))
                unit_text = match.group(2)
            else:
                match = cls._RELATIVE_DATE_WINDOW_SINGLE_PATTERN.search(text)
                if match:
                    amount = 1
                    unit_text = match.group(1)

            if amount is None or unit_text is None:
                continue

            unit = unit_text.lower().rstrip("s")
            label = f"last {amount} {unit}" + ("" if amount == 1 else "s")
            return f"m.date_start >= CURRENT_DATE - INTERVAL {amount} {unit.upper()}", label

        return None, None

    def _build_recent_player_batting_summary_response(self, question: str, cricsheet_name: str,
                                                      canonical_name: str,
                                                      history: list[dict] | None = None) -> dict | None:
        """Build a deterministic batting summary for recent single-player form queries."""
        if not question:
            return None
        if self._PLAYER_BOWLING_SUMMARY_PATTERN.search(question):
            return None
        if self._PLAYER_FIELDING_SUMMARY_PATTERN.search(question):
            return None
        if not self._PLAYER_BATTING_SUMMARY_PATTERN.search(question):
            return None

        date_filter, date_label = self._resolve_relative_date_filter(question, history)
        if not date_filter:
            return None

        event_filter, event_label = self._resolve_event_filter_from_context(question, history)
        safe_name = cricsheet_name.replace("'", "''")
        where_clauses = [f"d.batter = '{safe_name}'", date_filter]
        if event_filter:
            where_clauses.append(event_filter)
        where_sql = "\n      AND ".join(where_clauses)

        sql = f"""
WITH bat_innings AS (
    SELECT
        d.batter AS player_name,
        d.match_id,
        d.innings_num,
        SUM(d.runs_batter) AS innings_runs,
        COUNT(*) FILTER (WHERE COALESCE(d.extras_wides, 0) = 0) AS balls_faced,
        MAX(
            CASE
                WHEN w.player_out IS NOT NULL
                 AND w.kind NOT IN ('retired hurt', 'retired out') THEN 1
                ELSE 0
            END
        ) AS dismissed
    FROM deliveries d
    JOIN matches m ON d.match_id = m.match_id
    LEFT JOIN wickets w
        ON d.match_id = w.match_id
       AND d.innings_num = w.innings_num
       AND d.over_num = w.over_num
       AND d.ball_num = w.ball_num
       AND w.player_out = d.batter
    WHERE {where_sql}
    GROUP BY d.batter, d.match_id, d.innings_num
)
SELECT
    player_name,
    COUNT(DISTINCT match_id) AS matches,
    COUNT(*) AS innings,
    SUM(innings_runs) AS runs,
    SUM(balls_faced) AS balls_faced,
    SUM(dismissed) AS dismissals,
    ROUND(SUM(innings_runs) * 1.0 / NULLIF(SUM(dismissed), 0), 2) AS batting_avg,
    ROUND(SUM(innings_runs) * 100.0 / NULLIF(SUM(balls_faced), 0), 2) AS strike_rate
FROM bat_innings
GROUP BY player_name
LIMIT 1
""".strip()

        try:
            columns, rows = self._execute_sql(sql)
        except Exception:
            return None

        row_lists = [list(row) for row in rows]
        if not row_lists:
            scope_label = f"{event_label} over the {date_label}" if event_filter else f"all formats over the {date_label}"
            return {
                "question": question,
                "sql": sql,
                "columns": columns,
                "rows": [],
                "answer": f"No batting records were found for {canonical_name} in {scope_label}.",
                "error": None,
                "chart_config": None,
                "context_summary": f"No recent batting data for {canonical_name}",
                "new_fact": None,
                "display_hint": {"format": "stats", "stat_type": "batting"},
                "sections": None,
                "model_used": "deterministic-player-batting",
                "cached": False,
                "candidates": None,
                "original_question": None,
                "profile": None,
            }

        row = row_lists[0]
        matches = int(row[1] or 0)
        innings = int(row[2] or 0)
        runs = int(row[3] or 0)
        batting_avg = float(row[6]) if row[6] is not None else None
        strike_rate = float(row[7]) if row[7] is not None else None
        scope_label = f"{event_label} over the {date_label}" if event_filter else f"all formats over the {date_label}"

        answer = (
            f"Across {scope_label}, {canonical_name} scored {runs} runs in {innings} innings "
            f"from {matches} matches"
        )
        if batting_avg is not None and strike_rate is not None:
            answer += f", averaging {batting_avg:.2f} at a strike rate of {strike_rate:.2f}."
        elif batting_avg is not None:
            answer += f", averaging {batting_avg:.2f}."
        elif strike_rate is not None:
            answer += f" at a strike rate of {strike_rate:.2f}."
        else:
            answer += "."

        return {
            "question": question,
            "sql": sql,
            "columns": columns,
            "rows": row_lists,
            "answer": answer,
            "error": None,
            "chart_config": None,
            "context_summary": f"{canonical_name} batting summary, {scope_label} via Cricsheet data",
            "new_fact": None,
            "display_hint": {"format": "stats", "stat_type": "batting"},
            "sections": None,
            "model_used": "deterministic-player-batting",
            "cached": False,
            "candidates": None,
            "original_question": None,
            "profile": None,
        }

    def _build_career_record_response(self, question: str, cricsheet_name: str,
                                      canonical_name: str) -> dict | None:
        """Generate career record using known-good template SQL.
        Returns a complete response dict or None if execution fails.
        """
        event_filter, event_label = self._detect_event_filter(question)
        where_event = f"AND {event_filter}" if event_filter else ""

        sql = f"""
WITH bat_innings AS (
    SELECT d.batter, d.match_id, d.innings_num,
           SUM(d.runs_batter) AS innings_runs,
           COUNT(*) FILTER (WHERE d.extras_wides = 0) AS balls_faced,
           MAX(CASE WHEN w.player_out IS NOT NULL
                     AND w.kind NOT IN ('retired hurt','retired out') THEN 1 ELSE 0 END) AS dismissed
    FROM deliveries d
    LEFT JOIN wickets w ON d.match_id = w.match_id AND d.innings_num = w.innings_num
                        AND d.over_num = w.over_num AND d.ball_num = w.ball_num
                        AND w.player_out = d.batter
    JOIN matches m ON d.match_id = m.match_id
    WHERE d.batter = '{cricsheet_name}' {where_event}
    GROUP BY d.batter, d.match_id, d.innings_num
),
bat_agg AS (
    SELECT batter AS player_name,
           COUNT(DISTINCT match_id) AS matches,
           COUNT(*) AS innings,
           SUM(innings_runs) AS total_runs,
           MAX(innings_runs) AS highest_score,
           SUM(balls_faced) AS balls_faced,
           ROUND(SUM(innings_runs) * 100.0 / NULLIF(SUM(balls_faced), 0), 2) AS strike_rate,
           SUM(dismissed) AS dismissals,
           ROUND(SUM(innings_runs) * 1.0 / NULLIF(SUM(dismissed), 0), 2) AS batting_avg,
           COUNT(*) FILTER (WHERE innings_runs >= 100) AS centuries,
           COUNT(*) FILTER (WHERE innings_runs >= 50 AND innings_runs < 100) AS fifties
    FROM bat_innings
    GROUP BY batter
),
bowl_balls AS (
    SELECT d.bowler, d.match_id, d.innings_num,
           d.over_num, d.ball_num,
           d.runs_batter + d.extras_wides + d.extras_noballs AS runs_conceded,
           CASE WHEN d.extras_wides = 0 AND d.extras_noballs = 0 THEN 1 ELSE 0 END AS is_legal,
           CASE WHEN w.player_out IS NOT NULL
                     AND w.kind NOT IN ('run out','retired hurt','obstructing the field','retired out')
                THEN 1 ELSE 0 END AS is_wicket
    FROM deliveries d
    LEFT JOIN wickets w ON d.match_id = w.match_id AND d.innings_num = w.innings_num
                        AND d.over_num = w.over_num AND d.ball_num = w.ball_num
    JOIN matches m ON d.match_id = m.match_id
    WHERE d.bowler = '{cricsheet_name}' {where_event}
),
bowl_agg AS (
    SELECT bowler AS player_name,
           COUNT(DISTINCT match_id) AS matches,
           SUM(is_wicket) AS wickets,
           SUM(runs_conceded) AS runs_conceded,
           SUM(is_legal) AS legal_balls,
           ROUND(SUM(runs_conceded) * 1.0 / NULLIF(SUM(is_wicket), 0), 2) AS bowling_avg,
           ROUND(SUM(runs_conceded) * 6.0 / NULLIF(SUM(is_legal), 0), 2) AS economy,
           ROUND(SUM(is_legal) * 1.0 / NULLIF(SUM(is_wicket), 0), 2) AS bowling_sr
    FROM bowl_balls
    GROUP BY bowler
)
SELECT COALESCE(b.player_name, bw.player_name) AS player_name,
       COALESCE(b.matches, bw.matches) AS matches,
       b.innings AS batting_innings,
       b.total_runs, b.highest_score, b.centuries, b.fifties,
       b.balls_faced, b.strike_rate, b.batting_avg, b.dismissals,
       bw.wickets, bw.runs_conceded, bw.economy, bw.bowling_avg, bw.bowling_sr
FROM bat_agg b
FULL OUTER JOIN bowl_agg bw ON b.player_name = bw.player_name;"""

        try:
            con = self._get_connection()
            result = con.execute(sql).fetchall()
            cols = [desc[0] for desc in con.description]
            con.close()
        except Exception:
            return None

        if not result:
            return None

        rows = [list(r) for r in result]

        # Strip bowling columns if player didn't bowl (all bowling vals null)
        bowl_col_names = {"wickets", "runs_conceded", "economy", "bowling_avg", "bowling_sr"}
        bowl_indices = [i for i, c in enumerate(cols) if c in bowl_col_names]
        if bowl_indices and all(rows[0][i] is None or rows[0][i] == 0 for i in bowl_indices):
            keep = [i for i in range(len(cols)) if i not in set(bowl_indices)]
            cols = [cols[i] for i in keep]
            rows = [[row[i] for i in keep] for row in rows]

        # Use template narrative
        template_result = self._template_narrative(question, sql, cols, [tuple(rows[0])])

        if template_result:
            narrative, chart_config, context_summary, new_fact, display_hint = template_result
        else:
            # Fallback prose
            narrative = f"Career record for {canonical_name} in {event_label}."
            chart_config = None
            context_summary = f"Career record for {canonical_name} ({event_label})"
            new_fact = None
            display_hint = {"format": "stats", "stat_type": "allround"}

        resp = {
            "question": question,
            "sql": sql,
            "columns": cols,
            "rows": rows,
            "answer": narrative,
            "error": None,
            "chart_config": chart_config,
            "context_summary": context_summary,
            "new_fact": new_fact,
            "display_hint": display_hint,
            "sections": None,
            "model_used": "template",
            "cached": False,
            "candidates": None,
            "original_question": None,
            "profile": None,
        }

        # Cache this result
        self._cache_store(question, resp)
        return resp

    def ask(self, question: str, history: list[dict] | None = None) -> dict:
        """Full pipeline: question → SQL → execute → narrative answer.

        Returns dict with keys: question, sql, columns, rows, answer, error, chart_config, context_summary
        """
        history = history or []

        # Step 0: Resolve player names (zero LLM calls)
        player_matches = self._resolve_players(question)
        suppress_low_confidence_player_matches = self._should_suppress_low_confidence_player_matches(question)
        player_context_parts = []
        resolved_names: set[str] = set()  # track already-resolved canonical names

        def _build_player_filter(c: dict) -> str:
            """Build a [PLAYER FILTER] annotation with exact SQL WHERE clauses and context."""
            parts = [f'[PLAYER FILTER for "{c["canonical_name"]}"']
            kid = c.get("kaggle_player_id")
            cs = c.get("cricsheet_name", "")
            if kid:
                parts.append(f"  Kaggle filter: batsman = {kid}")
            elif c["canonical_name"]:
                parts.append(f"  Kaggle filter: player_name ILIKE '%{c['canonical_name']}%'")
            if cs:
                parts.append(f"  Cricsheet filter: batter = '{cs}'")
            team = (c.get("team") or "").strip()
            if team:
                parts.append(
                    f"  Context only: primary team = {team} (disambiguation only; do not add a team filter unless the user explicitly asks for it)"
                )
            parts.append("]")
            return "\n".join(parts)

        for pm in player_matches:
            token = pm["query_token"]
            cands = pm["candidates"]
            confidence = pm.get("confidence", "high")
            if confidence == "low" and suppress_low_confidence_player_matches:
                continue
            if len(cands) == 1:
                # Unambiguous — inject resolved name
                c = cands[0]
                player_context_parts.append(_build_player_filter(c))
                resolved_names.add(c["canonical_name"].lower())
            elif len(cands) > 1:
                # Check if the question contains enough specificity (full name match)
                exact = [c for c in cands if c["canonical_name"].lower() == token.lower()]
                if len(exact) == 1:
                    c = exact[0]
                    player_context_parts.append(_build_player_filter(c))
                    resolved_names.add(c["canonical_name"].lower())
                elif any(c["canonical_name"].lower() in resolved_names for c in cands):
                    # One of the candidates was already resolved by an earlier, more specific token.
                    # E.g. "Pat Cummins" resolved first, then bare "Cummins" encountered — skip it.
                    pass
                else:
                    # Filter out candidates already resolved by earlier tokens
                    remaining = [c for c in cands if c["canonical_name"].lower() not in resolved_names]
                    if len(remaining) == 1:
                        c = remaining[0]
                        player_context_parts.append(_build_player_filter(c))
                        resolved_names.add(c["canonical_name"].lower())
                    elif len(remaining) > 1:
                        # Ambiguous — return disambiguation MCQ
                        return self._build_disambiguation_response(question, token, remaining)

        # Augment question with resolved player context
        augmented_question = question
        # Build canonical→cricsheet name map for post-processing SQL
        player_name_map: dict[str, str] = {}
        if player_context_parts:
            augmented_question = question + "\n\n" + "\n".join(player_context_parts)
        # Collect all unambiguously resolved players for SQL name fixing
        for pm in player_matches:
            cands = pm["candidates"]
            if len(cands) == 1:
                c = cands[0]
                cn = c.get("canonical_name", "")
                cs = c.get("cricsheet_name", "")
                if cn and cs and cn != cs:
                    player_name_map[cn] = cs

        # Step 0b: Check if this is a "tell me about X" profile query
        profile_name = self._detect_profile_query(question)
        if profile_name and len(player_matches) >= 1:
            # Use the first resolved player's cricsheet_name
            for pm in player_matches:
                if pm["candidates"] and len(pm["candidates"]) == 1:
                    c = pm["candidates"][0]
                    profile_resp = self._build_profile_response(question, c["canonical_name"], c["cricsheet_name"])
                    if profile_resp:
                        return profile_resp
                    break

        if len(player_matches) == 1 and len(player_matches[0]["candidates"]) == 1:
            c = player_matches[0]["candidates"][0]
            recent_batting_resp = self._build_recent_player_batting_summary_response(
                question, c["cricsheet_name"], c["canonical_name"], history
            )
            if recent_batting_resp:
                return recent_batting_resp

        # Step 0b2: Career record template for "X's record/stats in IPL" queries
        if (self._RECORD_QUERY_PATTERN.search(question)
                and len(player_matches) == 1
                and len(player_matches[0]["candidates"]) == 1):
            c = player_matches[0]["candidates"][0]
            record_resp = self._build_career_record_response(
                question, c["cricsheet_name"], c["canonical_name"]
            )
            if record_resp:
                return record_resp

        team_phase_resp = self._build_team_phase_comparison_response(question, history)
        if team_phase_resp is not None:
            return team_phase_resp

        latest_scorecard_result = self._build_latest_match_scorecard_response(question)
        if latest_scorecard_result is not None:
            return latest_scorecard_result

        fielding_result = self._build_fielding_leader_response(question)
        if fielding_result is not None:
            return fielding_result

        followup_result = self._build_match_followup_response(question, history)
        if followup_result is not None:
            return followup_result

        bypass_cache = self._should_bypass_cache(question, history)

        # Step 0c: Check query cache (zero LLM calls for repeated questions)
        if not bypass_cache:
            cached = self._cache_lookup(question, history=history)
            if cached:
                return cached

        result = {
            "question": question,
            "sql": None,
            "columns": [],
            "rows": [],
            "answer": "",
            "error": None,
            "chart_config": None,
            "context_summary": None,
            "new_fact": None,
            "display_hint": None,
            "sections": None,
            "model_used": None,
        }

        # Step 1: Ask LLM to generate SQL or call a WASP tool
        try:
            messages = [{"role": "system", "content": self._get_sql_prompt(augmented_question)}]
            if history:
                for turn in history:
                    q = turn.get("question", "")
                    ctx = turn.get("context_summary", "")
                    prev_sql = turn.get("sql", "")
                    messages.append({"role": "user", "content": q})
                    assistant_ctx = ""
                    if ctx:
                        assistant_ctx += f"[Context: {ctx}]"
                    if prev_sql:
                        assistant_ctx += f"\n[Previous SQL: {prev_sql}]"
                    if assistant_ctx:
                        messages.append({"role": "assistant", "content": assistant_ctx.strip()})
            messages.append({"role": "user", "content": augmented_question})

            llm_msg = self._call_llm_with_tools(messages, temperature=0.1)

            # Check if the LLM chose to call a WASP tool
            if llm_msg.tool_calls:
                tc = llm_msg.tool_calls[0]
                tool_name = tc.function.name
                tool_args = json.loads(tc.function.arguments)
                tool_result = self._execute_tool_call(tool_name, tool_args)
                return self._build_tool_result_response(
                    question, tool_name, tool_args, tool_result, history
                )

            # Otherwise, treat as SQL generation (existing flow)
            sql = (llm_msg.content or "").strip()
            if sql.startswith("```"):
                lines = sql.split("\n")
                sql = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            sql = sql.strip()
            sql = self._extract_sql(sql)
            sql = self._apply_question_sql_guards(sql, augmented_question)
            if player_name_map:
                sql = self._fix_player_names_in_sql(sql, player_name_map)
            result["sql"] = sql
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RateLimitReached" in err_str or "rate" in err_str.lower():
                result["error"] = "Daily API limit reached. The system tried a backup model but it's also rate limited. Please try again later."
                result["answer"] = "I'm temporarily unable to process queries due to API limits. Please try again in a few hours."
            else:
                result["error"] = f"Failed to generate SQL: {e}"
                result["answer"] = "Sorry, I couldn't understand that question. Could you rephrase it?"
            return result

        # Step 1b: Detect non-SQL response (conversational/explanatory questions)
        # The LLM sometimes returns prose instead of SQL for "why" / "explain" questions.
        sql_trimmed = sql.strip().lstrip("(")
        _SQL_KEYWORDS = ("SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "EXPLAIN")
        if not any(sql_trimmed.upper().startswith(kw) for kw in _SQL_KEYWORDS):
            # LLM returned prose — treat it as a direct narrative answer
            result["sql"] = None
            result["answer"] = sql  # the "SQL" is actually the LLM's text answer
            result["display_hint"] = {"format": "stats", "stat_type": "match"}
            result["context_summary"] = "Conversational follow-up (no SQL needed)"
            result["model_used"] = self._last_model_used
            return result

        # Step 2: Execute SQL
        try:
            columns, rows = self._execute_sql(sql)
            result["columns"] = columns
            result["rows"] = self._prune_empty_metric_rows(columns, [list(r) for r in rows])
        except Exception as e:
            result["error"] = f"SQL execution failed: {e}"
            # Retry with compact prompt to stay within gpt-4o-mini token limits
            try:
                retry_sql = self._retry_sql(augmented_question, sql, str(e))
                # Extract SQL if the retry response contains prose mixed with SQL
                retry_sql = self._extract_sql(retry_sql)
                retry_sql = self._apply_question_sql_guards(retry_sql, augmented_question)
                result["sql"] = retry_sql
                columns, rows = self._execute_sql(retry_sql)
                result["columns"] = columns
                result["rows"] = self._prune_empty_metric_rows(columns, [list(r) for r in rows])
                result["error"] = None
            except Exception as e2:
                result["error"] = f"SQL retry also failed: {e2}"
                result["answer"] = f"I generated a query but it failed to execute. Error: {e}"
                return result

        # Step 3: Generate narrative (try template first to save LLM call)
        try:
            template_result = self._template_narrative(
                question, result["sql"], columns, [tuple(r) for r in result["rows"]]
            )
            if template_result is not None:
                narrative, chart_config, context_summary, new_fact, display_hint = template_result
                result["answer"] = narrative
                result["chart_config"] = chart_config
                result["context_summary"] = context_summary
                result["new_fact"] = new_fact
                result["display_hint"] = display_hint
                result["model_used"] = "template"
            else:
                # Fall back to LLM narrative for complex results
                data_text = self._format_results(columns, [tuple(r) for r in result["rows"]])
                narrative, chart_config, context_summary, new_fact, display_hint = self._generate_narrative(question, result["sql"], data_text)
                result["answer"] = narrative
                result["chart_config"] = chart_config
                result["context_summary"] = context_summary
                result["new_fact"] = new_fact
                result["display_hint"] = display_hint
                result["model_used"] = self._last_model_used

            # Step 4: Build scorecard if the result looks like a match card
            if not display_hint and len(result["rows"]) == 1:
                match_id = self._extract_match_id(result["columns"], result["rows"])
                if match_id:
                    result["display_hint"] = {"format": "scorecard"}
            scorecard = self._maybe_build_scorecard_sections(result)
            if scorecard:
                result["sections"] = scorecard
        except Exception as e:
            # If narrative fails, just return the raw data
            result["answer"] = self._format_results(columns, [tuple(r) for r in result["rows"]])

        # Step 5: Cache successful results
        if not result.get("error") and not bypass_cache:
            self._cache_store(question, result, history=history)

        return result

    # ── Scorecard support ──────────────────────────────────────────────────

    def _extract_match_id(self, columns: list[str], rows: list) -> int | str | None:
        """Extract a match ID from query results."""
        if not rows:
            return None
        for i, col in enumerate(columns):
            col_clean = col.lower().replace(' ', '_').replace('"', '')
            if col_clean in ('match_id', 'matchid', 'test_match_no'):
                val = rows[0][i]
                if val is not None:
                    try:
                        return int(val)
                    except (ValueError, TypeError):
                        return val
        # Fallback: if there's a column literally named "Match ID" (Kaggle)
        for i, col in enumerate(columns):
            if col.strip() == 'Match ID':
                val = rows[0][i]
                if val is not None:
                    try:
                        return int(val)
                    except (ValueError, TypeError):
                        return val
        return None

    def _detect_data_source(self, sql: str) -> str:
        """Detect whether the SQL uses kaggle or cricsheet tables."""
        return 'kaggle' if 'kaggle_' in sql.lower() else 'cricsheet'

    @staticmethod
    def _format_overs_from_balls(legal_balls: int | None) -> str:
        legal_balls = int(legal_balls or 0)
        return f"{legal_balls // 6}.{legal_balls % 6}"

    @staticmethod
    def _build_cricsheet_result_text(match_row: tuple) -> str:
        winner = match_row[9]
        result = match_row[10]
        by_runs = match_row[11]
        by_wickets = match_row[12]
        by_innings = match_row[13]
        method = match_row[14]

        if winner:
            if by_innings:
                if by_runs:
                    text = f"{winner} won by an innings and {int(by_runs)} runs"
                else:
                    text = f"{winner} won by an innings"
            elif by_runs:
                text = f"{winner} won by {int(by_runs)} runs"
            elif by_wickets:
                text = f"{winner} won by {int(by_wickets)} wickets"
            else:
                text = f"{winner} won"
        else:
            text = (result or "").replace("_", " ").strip()

        if method and method.lower() not in text.lower():
            text = f"{text} ({method})" if text else method
        return text

    @staticmethod
    def _scorecard_has_unresolved_players(scorecard: dict | None) -> bool:
        if not scorecard:
            return False
        for innings in scorecard.get("innings", []):
            for row in innings.get("batting", {}).get("rows", []):
                name = str(row[0] or "").strip()
                if name and _re.fullmatch(r"-?\d+(?:\.0+)?", name):
                    return True
        return False

    def _maybe_build_scorecard_sections(self, result: dict) -> dict | None:
        display_hint = result.get("display_hint") or {}
        is_scorecard_hint = display_hint.get("format") == "scorecard"
        columns = result.get("columns") or []
        rows = result.get("rows") or []
        is_match_row = (
            len(rows) == 1
            and any(
                c.lower().replace(' ', '_').replace('"', '') in ('match_id', 'matchid', 'test_match_no')
                or c.strip() == 'Match ID'
                for c in columns
            )
            and any(
                'match' in c.lower() and ('name' in c.lower() or 'winner' in c.lower())
                for c in columns
            )
        )
        if not (is_scorecard_hint or is_match_row):
            return None

        match_id = self._extract_match_id(columns, rows)
        if not match_id:
            return None

        data_source = self._detect_data_source(result.get("sql") or "")
        return self._build_scorecard(match_id, data_source)

    def _build_kaggle_scorecard(self, match_id) -> dict | None:
        """Build full scorecard sections for a match using Kaggle data."""
        con = self._get_connection()
        try:
            scorecard = {"type": "scorecard", "match_info": {}, "innings": []}

            match_row = con.execute('''
                SELECT "Match Name", "Match Start Date", "Match End Date",
                       "Team1 Name", "Team2 Name",
                       "Match Venue (Stadium)", "Match Venue (City)", "Match Venue (Country)",
                       "Toss Winner", "Toss Winner Choice",
                       "Match Winner", "Match Result Text",
                       "Innings1 Team1 Runs Scored", "Innings1 Team1 Wickets Fell", "Innings1 Team1 Extras Rec",
                       "Innings2 Team1 Runs Scored", "Innings2 Team1 Wickets Fell", "Innings2 Team1 Extras Rec",
                       "Innings1 Team2 Runs Scored", "Innings1 Team2 Wickets Fell", "Innings1 Team2 Extras Rec",
                       "Innings2 Team2 Runs Scored", "Innings2 Team2 Wickets Fell", "Innings2 Team2 Extras Rec"
                FROM kaggle_matches
                WHERE "Match ID" = ?
            ''', [match_id]).fetchone()
            if not match_row:
                return None

            team1 = match_row[3] or ""
            team2 = match_row[4] or ""

            scorecard["match_info"] = {
                "title": match_row[0] or "",
                "start_date": str(match_row[1]) if match_row[1] else "",
                "end_date": str(match_row[2]) if match_row[2] else "",
                "team1": team1,
                "team2": team2,
                "venue": ", ".join(filter(None, [match_row[5], match_row[6]])),
                "country": match_row[7] or "",
                "toss": f"{match_row[8] or '?'} won the toss and elected to {match_row[9] or '?'}",
                "result": match_row[11] or (f"{match_row[10]} won" if match_row[10] else ""),
            }

            innings_totals = {
                (team1, 1): {"runs": match_row[12], "wickets": match_row[13], "extras": match_row[14]},
                (team1, 2): {"runs": match_row[15], "wickets": match_row[16], "extras": match_row[17]},
                (team2, 1): {"runs": match_row[18], "wickets": match_row[19], "extras": match_row[20]},
                (team2, 2): {"runs": match_row[21], "wickets": match_row[22], "extras": match_row[23]},
            }

            innings_list = con.execute('''
                SELECT DISTINCT innings, team
                FROM kaggle_batting
                WHERE "Match ID" = ?
                ORDER BY innings
            ''', [match_id]).fetchall()

            team_inn_count: dict[str, int] = {}

            for inn_num, batting_team in innings_list:
                team_inn_count[batting_team] = team_inn_count.get(batting_team, 0) + 1
                team_inn_num = team_inn_count[batting_team]
                ordinal = "1st" if team_inn_num == 1 else "2nd"

                totals = innings_totals.get((batting_team, team_inn_num), {})
                total_runs = int(totals.get("runs") or 0)
                total_wickets = totals.get("wickets")
                total_wickets = int(total_wickets) if total_wickets is not None else None
                total_extras = int(totals.get("extras") or 0)

                innings_data: dict = {
                    "title": f"{batting_team} - {ordinal} Innings",
                    "team": batting_team,
                    "innings_num": inn_num,
                    "batting": {"columns": [], "rows": []},
                    "total": {"runs": total_runs, "wickets": total_wickets, "extras": total_extras},
                    "fow": [],
                    "bowling": {"columns": [], "rows": []},
                }

                bat_rows = con.execute('''
                    SELECT COALESCE(kp.player_name, CAST(b.batsman AS VARCHAR)) as batsman,
                           b.runs, b.balls, b.fours, b.sixes,
                           ROUND(b.strikeRate, 2) as strike_rate,
                           b.isOut as is_out,
                           b.wicketType as dismissal_type,
                           COALESCE(kp2.player_name, '') as bowler_name,
                           COALESCE(kp_f.player_name, b.fielders, '') as fielder
                    FROM kaggle_batting b
                    LEFT JOIN kaggle_players kp ON b.batsman = kp.player_id
                    LEFT JOIN kaggle_players kp2 ON TRY_CAST(b.bowler AS BIGINT) = kp2.player_id
                    LEFT JOIN kaggle_players kp_f ON TRY_CAST(
                        REGEXP_REPLACE(b.fielders, '[\\[\\]''"]', '', 'g') AS BIGINT
                    ) = kp_f.player_id
                    WHERE b."Match ID" = ? AND b.innings = ?
                    ORDER BY b.rowid
                ''', [match_id, inn_num]).fetchall()

                innings_data["batting"]["columns"] = [
                    "batsman", "runs", "balls", "fours", "sixes",
                    "strike_rate", "is_out", "dismissal_type", "bowler_name", "fielder"
                ]
                actual_bat = []
                dnb_names = []
                for r in bat_rows:
                    row = list(r)
                    dismissal = (row[7] or "").strip().upper()
                    if dismissal == "DNB" or (row[1] is None and row[2] is None and not row[6]):
                        dnb_names.append(row[0])
                    else:
                        actual_bat.append(row)
                innings_data["batting"]["rows"] = actual_bat
                innings_data["dnb"] = dnb_names

                fow_rows = con.execute('''
                    SELECT f.wicket, f.runs as team_score, f.over,
                           COALESCE(kp.player_name, CAST(f.player AS VARCHAR)) as player
                    FROM kaggle_fow f
                    LEFT JOIN kaggle_players kp ON TRY_CAST(f.player AS BIGINT) = kp.player_id
                    WHERE f."Match ID" = ? AND f.innings = ?
                    ORDER BY f.wicket
                ''', [match_id, inn_num]).fetchall()

                innings_data["fow"] = [
                    {
                        "wicket": int(r[0]) if r[0] is not None else 0,
                        "score": int(r[1]) if r[1] is not None else 0,
                        "over": float(r[2]) if r[2] is not None else 0,
                        "player": r[3] or "",
                    }
                    for r in fow_rows
                ]

                bowl_rows = con.execute('''
                    SELECT COALESCE(kp.player_name, CAST(b."bowler id" AS VARCHAR)) as bowler,
                           b.overs, b.maidens, b.conceded as runs, b.wickets,
                           ROUND(b.economy, 2) as economy
                    FROM kaggle_bowling b
                    LEFT JOIN kaggle_players kp ON b."bowler id" = kp.player_id
                    WHERE b."Match ID" = ? AND b.innings = ?
                    ORDER BY b.rowid
                ''', [match_id, inn_num]).fetchall()

                innings_data["bowling"]["columns"] = [
                    "bowler", "overs", "maidens", "runs", "wickets", "economy"
                ]
                innings_data["bowling"]["rows"] = [list(r) for r in bowl_rows]

                scorecard["innings"].append(innings_data)

            return scorecard
        except Exception as e:
            print(f"Scorecard build error: {e}")
            return None
        finally:
            con.close()

    def _build_cricsheet_scorecard(self, match_id) -> dict | None:
        """Build scorecard sections for a match directly from Cricsheet ball-by-ball tables."""
        con = self._get_connection()
        try:
            match_id = str(match_id)
            match_row = con.execute('''
                SELECT match_type, date_start, date_end, venue, city,
                       team1, team2, toss_winner, toss_decision,
                       outcome_winner, outcome_result, outcome_by_runs,
                       outcome_by_wickets, outcome_by_innings, outcome_method
                FROM matches
                WHERE match_id = ?
            ''', [match_id]).fetchone()
            if not match_row:
                return None

            team1 = match_row[5] or ""
            team2 = match_row[6] or ""
            scorecard = {
                "type": "scorecard",
                "match_info": {
                    "title": f"{team1} Vs {team2}".strip(),
                    "start_date": str(match_row[1]) if match_row[1] else "",
                    "end_date": str(match_row[2]) if match_row[2] else "",
                    "team1": team1,
                    "team2": team2,
                    "venue": ", ".join(filter(None, [match_row[3], match_row[4]])),
                    "country": "",
                    "toss": f"{match_row[7] or '?'} won the toss and elected to {match_row[8] or '?'}",
                    "result": self._build_cricsheet_result_text(match_row),
                },
                "innings": [],
            }

            innings_list = con.execute('''
                SELECT innings_num, batting_team
                FROM innings
                WHERE match_id = ?
                ORDER BY innings_num
            ''', [match_id]).fetchall()
            team_inn_count: dict[str, int] = {}

            for inn_num, batting_team in innings_list:
                team_inn_count[batting_team] = team_inn_count.get(batting_team, 0) + 1
                team_inn_num = team_inn_count[batting_team]
                ordinal = "1st" if team_inn_num == 1 else "2nd"

                totals_row = con.execute('''
                    SELECT
                        COALESCE(SUM(runs_total), 0) AS total_runs,
                        COALESCE(SUM(runs_extras), 0) AS total_extras,
                        COUNT(*) FILTER (WHERE LOWER(kind) <> 'retired hurt') AS wickets
                    FROM deliveries d
                    LEFT JOIN wickets w
                      ON d.match_id = w.match_id
                     AND d.innings_num = w.innings_num
                     AND d.over_num = w.over_num
                     AND d.ball_num = w.ball_num
                    WHERE d.match_id = ? AND d.innings_num = ?
                ''', [match_id, inn_num]).fetchone()

                batting_rows = con.execute('''
                    WITH batting AS (
                        SELECT batter AS batsman,
                               MIN(over_num * 1000 + ball_num) AS first_ball_key,
                               SUM(runs_batter) AS runs,
                               COUNT(*) FILTER (WHERE COALESCE(extras_wides, 0) = 0) AS balls,
                               COUNT(*) FILTER (WHERE runs_batter = 4 AND COALESCE(non_boundary, FALSE) = FALSE) AS fours,
                               COUNT(*) FILTER (WHERE runs_batter = 6) AS sixes
                        FROM deliveries
                        WHERE match_id = ? AND innings_num = ?
                        GROUP BY batter
                    ),
                    dismissals AS (
                        SELECT w.player_out AS batsman,
                               TRUE AS is_out,
                               LOWER(w.kind) AS dismissal_type,
                               COALESCE(d.bowler, '') AS bowler_name,
                               TRIM(BOTH ', ' FROM CONCAT(
                                   COALESCE(w.fielder1, ''),
                                   CASE WHEN w.fielder1 IS NOT NULL AND w.fielder2 IS NOT NULL THEN ', ' ELSE '' END,
                                   COALESCE(w.fielder2, '')
                               )) AS fielder
                        FROM wickets w
                        LEFT JOIN deliveries d
                          ON w.match_id = d.match_id
                         AND w.innings_num = d.innings_num
                         AND w.over_num = d.over_num
                         AND w.ball_num = d.ball_num
                        WHERE w.match_id = ? AND w.innings_num = ?
                    )
                    SELECT b.batsman, b.runs, b.balls, b.fours, b.sixes,
                           ROUND(b.runs * 100.0 / NULLIF(b.balls, 0), 2) AS strike_rate,
                           COALESCE(d.is_out, FALSE) AS is_out,
                           d.dismissal_type, d.bowler_name, d.fielder
                    FROM batting b
                    LEFT JOIN dismissals d ON b.batsman = d.batsman
                    ORDER BY b.first_ball_key, b.batsman
                ''', [match_id, inn_num, match_id, inn_num]).fetchall()

                fow_rows = con.execute('''
                    WITH wicket_events AS (
                        SELECT ROW_NUMBER() OVER (ORDER BY w.over_num, w.ball_num) AS wicket_num,
                               (
                                   SELECT COALESCE(SUM(d2.runs_total), 0)
                                   FROM deliveries d2
                                   WHERE d2.match_id = w.match_id
                                     AND d2.innings_num = w.innings_num
                                     AND (
                                         d2.over_num < w.over_num
                                         OR (d2.over_num = w.over_num AND d2.ball_num <= w.ball_num)
                                     )
                               ) AS team_score,
                               CONCAT(CAST(w.over_num AS VARCHAR), '.', CAST(w.ball_num AS VARCHAR)) AS over_text,
                               w.player_out
                        FROM wickets w
                        WHERE w.match_id = ? AND w.innings_num = ?
                          AND LOWER(w.kind) <> 'retired hurt'
                    )
                    SELECT wicket_num, team_score, over_text, player_out
                    FROM wicket_events
                    ORDER BY wicket_num
                ''', [match_id, inn_num]).fetchall()

                bowling_rows = con.execute('''
                    WITH over_runs AS (
                        SELECT bowler, over_num,
                               SUM(
                                   runs_batter
                                   + COALESCE(extras_wides, 0)
                                   + COALESCE(extras_noballs, 0)
                                   + COALESCE(extras_penalty, 0)
                               ) AS bowler_runs
                        FROM deliveries
                        WHERE match_id = ? AND innings_num = ?
                        GROUP BY bowler, over_num
                    ),
                    bowling AS (
                        SELECT bowler,
                               MIN(over_num * 1000 + ball_num) AS first_ball_key,
                               COUNT(*) FILTER (
                                   WHERE COALESCE(extras_wides, 0) = 0 AND COALESCE(extras_noballs, 0) = 0
                               ) AS legal_balls,
                               SUM(
                                   runs_batter
                                   + COALESCE(extras_wides, 0)
                                   + COALESCE(extras_noballs, 0)
                                   + COALESCE(extras_penalty, 0)
                               ) AS runs
                        FROM deliveries
                        WHERE match_id = ? AND innings_num = ?
                        GROUP BY bowler
                    ),
                    wicket_counts AS (
                        SELECT d.bowler, COUNT(*) AS wickets
                        FROM wickets w
                        JOIN deliveries d
                          ON w.match_id = d.match_id
                         AND w.innings_num = d.innings_num
                         AND w.over_num = d.over_num
                         AND w.ball_num = d.ball_num
                        WHERE w.match_id = ? AND w.innings_num = ?
                          AND LOWER(w.kind) NOT IN ('run out', 'retired hurt', 'obstructing the field', 'retired out')
                        GROUP BY d.bowler
                    ),
                    maidens AS (
                        SELECT bowler, COUNT(*) AS maidens
                        FROM over_runs
                        WHERE bowler_runs = 0
                        GROUP BY bowler
                    )
                    SELECT b.bowler, b.legal_balls,
                           COALESCE(m.maidens, 0) AS maidens,
                           b.runs,
                           COALESCE(w.wickets, 0) AS wickets,
                           ROUND(b.runs * 6.0 / NULLIF(b.legal_balls, 0), 2) AS economy
                    FROM bowling b
                    LEFT JOIN wicket_counts w ON b.bowler = w.bowler
                    LEFT JOIN maidens m ON b.bowler = m.bowler
                    ORDER BY b.first_ball_key, b.bowler
                ''', [match_id, inn_num, match_id, inn_num, match_id, inn_num]).fetchall()

                innings_data = {
                    "title": f"{batting_team} - {ordinal} Innings",
                    "team": batting_team,
                    "innings_num": inn_num,
                    "batting": {
                        "columns": [
                            "batsman", "runs", "balls", "fours", "sixes",
                            "strike_rate", "is_out", "dismissal_type", "bowler_name", "fielder"
                        ],
                        "rows": [list(r) for r in batting_rows],
                    },
                    "total": {
                        "runs": int(totals_row[0] or 0),
                        "wickets": int(totals_row[2]) if totals_row and totals_row[2] is not None else None,
                        "extras": int(totals_row[1] or 0),
                    },
                    "fow": [
                        {
                            "wicket": int(r[0]) if r[0] is not None else 0,
                            "score": int(r[1]) if r[1] is not None else 0,
                            "over": r[2] or "",
                            "player": r[3] or "",
                        }
                        for r in fow_rows
                    ],
                    "bowling": {
                        "columns": ["bowler", "overs", "maidens", "runs", "wickets", "economy"],
                        "rows": [
                            [
                                row[0],
                                self._format_overs_from_balls(row[1]),
                                row[2],
                                row[3],
                                row[4],
                                row[5],
                            ]
                            for row in bowling_rows
                        ],
                    },
                }
                scorecard["innings"].append(innings_data)

            return scorecard if scorecard["innings"] else None
        except Exception as e:
            print(f"Cricsheet scorecard build error: {e}")
            return None
        finally:
            con.close()

    def _build_scorecard(self, match_id, data_source: str = 'kaggle') -> dict | None:
        """Build full scorecard sections for a match, with Cricsheet fallback for degraded backfills."""
        if data_source == 'cricsheet':
            return self._build_cricsheet_scorecard(match_id)

        kaggle_scorecard = self._build_kaggle_scorecard(match_id)
        if kaggle_scorecard and not self._scorecard_has_unresolved_players(kaggle_scorecard):
            return kaggle_scorecard

        cricsheet_scorecard = self._build_cricsheet_scorecard(match_id)
        return cricsheet_scorecard or kaggle_scorecard

    def get_db_stats(self) -> dict:
        """Return row counts for all tables."""
        con = self._get_connection()
        stats = {}
        try:
            tables = con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
            for (table,) in tables:
                count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                stats[table] = count
        finally:
            con.close()
        return stats
