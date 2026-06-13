"""Cricket Statistician AI — query engine.

Translates natural-language cricket questions into DuckDB SQL,
executes them, and formats results via Google Gemini.
"""

import os
import ast
import json
import duckdb
from dotenv import load_dotenv
from google import genai

# Load environment variables from .env file
load_dotenv()

# --- Constants and Paths ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "db", "cricket.duckdb")
CACHE_PATH = os.path.join(BASE_DIR, "data", "db", "cache.duckdb")
KB_PATH = os.path.join(BASE_DIR, "data", "knowledge_base.json")

# --- Gemini API Configuration ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set!")

genai_client = genai.Client(api_key=GEMINI_API_KEY)

PRIMARY_MODEL_NAME = "gemini-2.5-pro"
FALLBACK_MODEL_NAME = "gemini-2.5-flash"

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

# --- Database Schema and Prompts (no changes needed here) ---
DB_SCHEMA = """
You have access to a DuckDB database...
...
"""
# (The rest of the long schema string is omitted for brevity but is included in the file)

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
SQL_PROMPT_HEADER = """You are a cricket statistics SQL expert. Given a natural-language cricket question, generate a DuckDB SQL query to answer it.
Return ONLY the generated SQL query, with no prose, no explanation, and no markdown code fences."""

NARRATIVE_PROMPT_HEADER = """You are a helpful cricket statistician. Given a user's question and the result of a SQL query as a JSON object, provide a concise, friendly, and insightful answer.
- Keep it brief (2-3 sentences).
- Frame the answer naturally, as if speaking to a fan.
- Always refer to the main player or team from the question.
- If the data is empty or contains no rows, just say "I couldn't find any data for that."
- Do not show the raw data table in your answer.
"""

SQL_RETRY_PROMPT = f"""You are a DuckDB SQL expert. The previous SQL query failed. Analyze the error message and the database schema to fix the query.
Return ONLY the corrected SQL, no explanation, no markdown fences.

Schema:
{DB_SCHEMA}
"""

class Engine:
    """The main query processing engine for the Cricket Statistician AI."""

    def __init__(self, db_path=DB_PATH):
        """Initialize the database connection and the Gemini models."""
        self.db = duckdb.connect(database=db_path, read_only=True)
        self.cache_db = duckdb.connect(database=CACHE_PATH, read_only=False)
        self.cache_db.execute("""
            CREATE TABLE IF NOT EXISTS query_cache (
                query_hash TEXT PRIMARY KEY,
                query_text TEXT,
                sql_query TEXT,
                result_json TEXT,
                narrative_html TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Initialize Gemini models
        
        
        print(f"✅ Engine initialized. Primary model: {PRIMARY_MODEL_NAME}, Fallback: {FALLBACK_MODEL_NAME}")

    def get_cached_result(self, query: str):
        """Check for a cached result for the given query."""
        query_hash = self._get_query_hash(query)
        result = self.cache_db.execute("SELECT sql_query, result_json, narrative_html FROM query_cache WHERE query_hash = ?", [query_hash]).fetchone()
        return result if result else (None, None, None)

    def cache_result(self, query: str, sql: str, result_json: str, narrative_html: str):
        """Cache a successful query and its results."""
        query_hash = self._get_query_hash(query)
        self.cache_db.execute(
            "INSERT OR REPLACE INTO query_cache (query_hash, query_text, sql_query, result_json, narrative_html) VALUES (?, ?, ?, ?, ?)",
            [query_hash, query, sql, result_json, narrative_html]
        )

    def _get_query_hash(self, query: str) -> str:
        """Generate a stable hash for a query string."""
        return hashlib.sha256(query.encode()).hexdigest()

    def _query_llm(self, model_name: str, prompt: str):
        """Internal: Query a specific Gemini model."""
        global _llm_call_count
        _llm_call_count += 1
        try:
            config = genai.types.GenerateContentConfig(
                safety_settings=[
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                ]
            )
            response = genai_client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config
            )
            return response.text.strip()
        except Exception as e:
            print(f"Error querying model {model_name}: {e}")
            return None

    def _query_llm_with_fallback(self, prompt: str):
        """Query LLM with automatic fallback to a secondary model."""
        # Try primary model
        sql_or_error = self._query_llm(PRIMARY_MODEL_NAME, prompt)
        if sql_or_error:
            return sql_or_error, PRIMARY_MODEL_NAME

        # If primary fails, try fallback
        print(f"Primary model ({PRIMARY_MODEL_NAME}) failed. Trying fallback...")
        sql_or_error = self._query_llm(FALLBACK_MODEL_NAME, prompt)
        if sql_or_error:
            return sql_or_error, FALLBACK_MODEL_NAME

        # If all models fail
        return None, None
        
    def _clean_sql_from_response(self, llm_response: str) -> str:
        """Cleans up the raw LLM response to isolate the SQL query."""
        if not llm_response:
            return ""
        # Remove markdown fences and "sql" language identifier
        cleaned = llm_response.replace("```sql", "").replace("```", "").strip()
        return cleaned

    def get_sql(self, query: str):
        """Generate SQL from a natural language query using Gemini."""
        knowledge_facts = _load_knowledge_facts()
        prompt = (
            f"{SQL_PROMPT_HEADER}\n\n"
            f"Here is the database schema:\n{DB_SCHEMA}\n\n"
            f"Consider these additional facts and rules:\n{knowledge_facts}\n\n"
            f"Generate a DuckDB SQL query for the following question:\n"
            f'--> "{query}"'
        )
        
        raw_sql, model_used = self._query_llm_with_fallback(prompt)
        
        if not raw_sql:
            return "ERROR: LLM API call failed with all models.", "", ""
            
        sql_query = self._clean_sql_from_response(raw_sql)
        return sql_query, model_used, prompt

    def execute_sql(self, sql: str):
        """Execute a SQL query and return the result as a JSON string."""
        try:
            result = self.db.execute(sql).fetchdf()
            # Convert to JSON, handling potential date/time format issues
            return result.to_json(orient="records", date_format="iso")
        except Exception as e:
            return f"ERROR: {str(e)}"

    def get_narrative(self, query: str, result_json: str):
        """Generate a natural language narrative from the query result."""
        prompt = (
            f"{NARRATIVE_PROMPT_HEADER}\n\n"
            f'Original question: "{query}"\n\n'
            f"Query result (JSON):\n{result_json}\n\n"
            f"Narrative:"
        )
        # Use the faster/cheaper model for narrative generation
        narrative, model_used = self._query_llm_with_fallback(prompt)
        return narrative if narrative else "Sorry, I couldn't generate a summary for that.", model_used

    def get_sql_and_execute(self, query: str):
        """The main end-to-end function."""
        # 1. Check cache first
        cached_sql, cached_json, cached_narrative = self.get_cached_result(query)
        if cached_sql:
            print("✅ Returning response from cache.")
            return {
                "sql": cached_sql,
                "result_json": cached_json,
                "narrative_html": cached_narrative,
                "model": "cached",
                "prompt": ""
            }

        # 2. Generate SQL
        sql, model, prompt = self.get_sql(query)
        if sql.startswith("ERROR:"):
            return {"error": sql}

        # 3. Execute SQL
        result_json = self.execute_sql(sql)

        # 4. Handle SQL execution errors (with LLM retry)
        if isinstance(result_json, str) and result_json.startswith("ERROR:"):
            print(f"Initial SQL failed: {result_json}. Attempting to fix with LLM...")
            retry_prompt = f"{SQL_RETRY_PROMPT}\n\nFAILED QUERY:\n{sql}\n\nERROR:\n{result_json}\n\nCORRECTED SQL:"
            
            fixed_sql, model_used_for_fix = self._query_llm_with_fallback(retry_prompt)
            if not fixed_sql:
                 return {"error": "SQL query failed and the LLM could not fix it.", "sql": sql, "prompt": prompt}
            
            sql = self._clean_sql_from_response(fixed_sql)
            result_json = self.execute_sql(sql)

            # If it still fails, return error
            if isinstance(result_json, str) and result_json.startswith("ERROR:"):
                return {"error": f"SQL query failed even after retry: {result_json}", "sql": sql, "prompt": retry_prompt}

        # 5. Generate narrative
        narrative, _ = self.get_narrative(query, result_json)

        # 6. Cache and return result
        self.cache_result(query, sql, result_json, narrative)
        
        return {
            "sql": sql,
            "result_json": result_json,
            "narrative_html": narrative,
            "model": model,
            "prompt": prompt
        }
