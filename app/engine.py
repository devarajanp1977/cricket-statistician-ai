"""Cricket Statistician AI — query engine.

Translates natural-language cricket questions into DuckDB SQL,
executes them, and formats results via GPT-4.1 through GitHub Models API.
"""

import os
import json
import duckdb
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "db", "cricket.duckdb")
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
  obstructing the field), fielder1 (VARCHAR), fielder2 (VARCHAR)

TABLE: players (Cricsheet player register)
  cricsheet_id (VARCHAR PK), name (VARCHAR), unique_name (VARCHAR),
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

SQL_SYSTEM_PROMPT = f"""You are a cricket statistics SQL expert. Given a natural-language question about cricket, generate a DuckDB SQL query to answer it.

{DB_SCHEMA}

Rules:
1. Return ONLY the SQL query, no explanation, no markdown code fences.
2. Always limit results to 50 rows max unless the user asks for all.
3. Use appropriate aggregations (AVG, SUM, COUNT, etc.).
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
   e) bowling_average: runs_conceded / NULLIF(wickets, 0).
   f) economy: (runs_conceded * 6.0) / NULLIF(legal_balls, 0).
   g) bowling_strike_rate: legal_balls * 1.0 / NULLIF(wickets, 0).

6. Join tables as needed. The deliveries table is the main fact table for Cricsheet.
7. If the question is ambiguous, make reasonable assumptions and prefer international matches.
8. Use CTEs for complex queries to keep them readable.
9. FOLLOW-UP CONSISTENCY: When conversation history is provided, pay close attention to which
    tables and player name format were used in previous queries. If the context mentions
    "Cricsheet" or "deliveries", use the deliveries/matches/innings/wickets tables with
    Cricsheet player names (e.g. "V Kohli"). If the context mentions "Kaggle" or "scorecards",
    use kaggle_* tables with player_map for names. NEVER switch data sources mid-conversation
    unless the user explicitly asks for different data. If a previous SQL is provided in the
    context, use the same tables and joins as a starting point.
10. SMART STAT AUTO-INCLUDE — MANDATORY COLUMNS BY QUERY TYPE:
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
    - Cricsheet: striker ILIKE '%Smith%' is TOO BROAD — use striker ILIKE '%SPD Smith%'
    - When matching a well-known player, try common name variants:
      "Steve Smith" → player_name ILIKE '%Steven Smith%' OR player_name ILIKE '%Steve Smith%'
      "Viv Richards" → player_name ILIKE '%Viv Richards%' OR player_name ILIKE '%Vivian Richards%'
    - NEVER match just a surname like '%Smith%' — this returns ALL players with that surname.
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
        self.client = OpenAI(
            base_url="https://models.inference.ai.azure.com",
            api_key=GITHUB_TOKEN,
        )
        self._last_model_used: str | None = None

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
                response = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                )
                self._last_model_used = model
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

    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(self.db_path, read_only=True)

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
            {"query_token": str, "candidates": [{"canonical_name":..., "cricsheet_name":..., "team":..., "alias_type":...}]}
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

        # Try multi-word windows first (3, then 2), then single words
        matched_positions: set[int] = set()
        for window_size in (3, 2, 1):
            for i in range(len(words) - window_size + 1):
                if any(j in matched_positions for j in range(i, i + window_size)):
                    continue
                token = " ".join(words[i:i + window_size])
                if token.lower() in self._STOP_WORDS:
                    continue
                if len(token) < 3:
                    continue

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
                    for j in range(i, i + window_size):
                        matched_positions.add(j)

        con.close()

        # Build result: only tokens that actually matched players
        result = []
        for token, cands in candidates_by_token.items():
            if cands:
                result.append({"query_token": token, "candidates": cands})
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

    def _get_sql_prompt(self) -> str:
        """Build the full SQL system prompt with live knowledge facts."""
        facts = _load_knowledge_facts()
        if facts:
            return SQL_SYSTEM_PROMPT + f"\n\nCRICKET DOMAIN KNOWLEDGE (use these facts to write better queries):\n{facts}\n"
        return SQL_SYSTEM_PROMPT

    def _get_narrative_prompt(self) -> str:
        """Build the full narrative system prompt with live knowledge facts."""
        facts = _load_knowledge_facts()
        if facts:
            return NARRATIVE_SYSTEM_PROMPT + f"\n\nCRICKET DOMAIN KNOWLEDGE (use these facts for context and accuracy):\n{facts}\n"
        return NARRATIVE_SYSTEM_PROMPT

    def _generate_sql(self, question: str, history: list[dict] | None = None) -> str:
        """Ask GPT-4.1 to generate SQL for the question."""
        messages = [{"role": "system", "content": self._get_sql_prompt()}]
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

    def _execute_sql(self, sql: str) -> tuple[list[str], list[tuple]]:
        """Execute SQL and return (column_names, rows)."""
        con = self._get_connection()
        try:
            result = con.execute(sql)
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
            return columns, rows
        finally:
            con.close()

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

    # ── Profile detection patterns ─────────────────────────────────────────
    _PROFILE_PATTERNS = [
        _re.compile(r"^(?:tell\s+me\s+about|who\s+is|who\s+was|about|profile\s+(?:of|for)?|info\s+(?:on|about))\s+(.+)", _re.IGNORECASE),
        _re.compile(r"^(.+?)(?:\s+profile|\s+bio(?:graphy)?|\s+info(?:rmation)?)$", _re.IGNORECASE),
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
        """Look up player_profiles and return a profile card response if found."""
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
            con.close()

            if not row:
                return None

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
        except Exception:
            return None

    def ask(self, question: str, history: list[dict] | None = None) -> dict:
        """Full pipeline: question → SQL → execute → narrative answer.

        Returns dict with keys: question, sql, columns, rows, answer, error, chart_config, context_summary
        """
        # Step 0: Resolve player names (zero LLM calls)
        player_matches = self._resolve_players(question)
        player_context_parts = []
        resolved_names: set[str] = set()  # track already-resolved canonical names

        def _build_player_filter(c: dict) -> str:
            """Build a [PLAYER FILTER] annotation with exact SQL WHERE clauses."""
            parts = [f'[PLAYER FILTER for "{c["canonical_name"]}"']
            kid = c.get("kaggle_player_id")
            cs = c.get("cricsheet_name", "")
            if kid:
                parts.append(f"  Kaggle filter: batsman = {kid}")
            elif c["canonical_name"]:
                parts.append(f"  Kaggle filter: player_name ILIKE '%{c['canonical_name']}%'")
            if cs:
                parts.append(f"  Cricsheet filter: batter = '{cs}'")
            parts.append(f"  Team: {c.get('team', '')}]")
            return "\n".join(parts)

        for pm in player_matches:
            token = pm["query_token"]
            cands = pm["candidates"]
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
        if player_context_parts:
            augmented_question = question + "\n\n" + "\n".join(player_context_parts)

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

        # Step 1: Generate SQL
        try:
            sql = self._generate_sql(augmented_question, history=history)
            sql = self._extract_sql(sql)
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
            result["rows"] = [list(r) for r in rows]
        except Exception as e:
            result["error"] = f"SQL execution failed: {e}"
            # Try once more with error context (use augmented_question to keep schema/player context)
            try:
                retry_sql = self._generate_sql(
                    f"{augmented_question}\n\nPrevious SQL failed with error: {e}\nPrevious SQL was: {sql}\nPlease fix the query. Return ONLY the corrected SQL, no explanation."
                )
                # Extract SQL if the retry response contains prose mixed with SQL
                retry_sql = self._extract_sql(retry_sql)
                result["sql"] = retry_sql
                columns, rows = self._execute_sql(retry_sql)
                result["columns"] = columns
                result["rows"] = [list(r) for r in rows]
                result["error"] = None
            except Exception as e2:
                result["error"] = f"SQL retry also failed: {e2}"
                result["answer"] = f"I generated a query but it failed to execute. Error: {e}"
                return result

        # Step 3: Generate narrative
        try:
            data_text = self._format_results(columns, [tuple(r) for r in result["rows"]])
            narrative, chart_config, context_summary, new_fact, display_hint = self._generate_narrative(question, result["sql"], data_text)
            result["answer"] = narrative
            result["chart_config"] = chart_config
            result["context_summary"] = context_summary
            result["new_fact"] = new_fact
            result["display_hint"] = display_hint
            result["model_used"] = self._last_model_used

            # Step 4: Build scorecard if display_hint says so
            is_scorecard_hint = display_hint and display_hint.get("format") == "scorecard"
            # Also auto-detect: single-row result from kaggle_matches with match-level columns
            is_match_row = (
                len(result["rows"]) == 1
                and any(c.lower().replace(' ', '_').replace('"', '') == 'match_id' or c.strip() == 'Match ID'
                        for c in result["columns"])
                and any('match' in c.lower() and ('name' in c.lower() or 'winner' in c.lower())
                        for c in result["columns"])
            )
            if is_scorecard_hint or is_match_row:
                if not display_hint:
                    display_hint = {"format": "scorecard"}
                    result["display_hint"] = display_hint
                match_id = self._extract_match_id(
                    result["columns"], result["rows"]
                )
                if match_id:
                    data_source = self._detect_data_source(result["sql"] or "")
                    scorecard = self._build_scorecard(match_id, data_source)
                    if scorecard:
                        result["sections"] = scorecard
        except Exception as e:
            # If narrative fails, just return the raw data
            result["answer"] = self._format_results(columns, [tuple(r) for r in result["rows"]])

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

    def _build_scorecard(self, match_id, data_source: str = 'kaggle') -> dict | None:
        """Build full scorecard sections for a match using Kaggle data."""
        if data_source != 'kaggle':
            return None  # Cricsheet scorecard aggregation not yet supported

        con = self._get_connection()
        try:
            scorecard = {"type": "scorecard", "match_info": {}, "innings": []}

            # ── Match info + innings totals ──
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

            # Build lookup: (team_name, team_innings_num) → {runs, wickets, extras}
            innings_totals = {
                (team1, 1): {"runs": match_row[12], "wickets": match_row[13], "extras": match_row[14]},
                (team1, 2): {"runs": match_row[15], "wickets": match_row[16], "extras": match_row[17]},
                (team2, 1): {"runs": match_row[18], "wickets": match_row[19], "extras": match_row[20]},
                (team2, 2): {"runs": match_row[21], "wickets": match_row[22], "extras": match_row[23]},
            }

            # ── Chronological innings from batting data ──
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

                # ── Batting ──
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
                        REGEXP_REPLACE(b.fielders, '[\[\]''"]', '', 'g') AS BIGINT
                    ) = kp_f.player_id
                    WHERE b."Match ID" = ? AND b.innings = ?
                    ORDER BY b.rowid
                ''', [match_id, inn_num]).fetchall()

                innings_data["batting"]["columns"] = [
                    "batsman", "runs", "balls", "fours", "sixes",
                    "strike_rate", "is_out", "dismissal_type", "bowler_name", "fielder"
                ]
                # Separate actual batsmen from Did Not Bat
                actual_bat = []
                dnb_names = []
                for r in bat_rows:
                    row = list(r)
                    dismissal = (row[7] or "").strip().upper()
                    if dismissal == "DNB" or (row[1] is None and row[2] is None and not row[6]):
                        dnb_names.append(row[0])  # batsman name
                    else:
                        actual_bat.append(row)
                innings_data["batting"]["rows"] = actual_bat
                innings_data["dnb"] = dnb_names

                # ── Fall of Wickets ──
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

                # ── Bowling ──
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
