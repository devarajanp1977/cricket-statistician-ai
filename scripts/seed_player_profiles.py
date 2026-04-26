"""Seed player_profiles table from ESPN Cricinfo athlete API.

Fetches batting_style, bowling_style, playing_role, and more for every
Cricsheet player that has a key_cricinfo (ESPN ID) — 99.8% of all players.

Features:
    - Full seed: fetches players with key_cricinfo that are not seeded yet
  - Incremental refresh (--refresh): only new + stale active profiles
  - tqdm progress bar with ETA
  - Checkpoint/resume every 100 players
  - Retry with exponential backoff on HTTP errors
  - SportMonks CSV fallback for players without ESPN data
  - Coverage report at the end

Usage:
    python scripts/seed_player_profiles.py             # full seed (missing profiles only; resumes via checkpoint)
    python scripts/seed_player_profiles.py --refresh    # incremental: new + stale active
    python scripts/seed_player_profiles.py --report     # coverage report only (no fetching)
"""

import os
import sys
import json
import time
import logging
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timedelta

import duckdb

# Optional: tqdm for progress bar
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None
    print("Install tqdm for progress bars: pip install tqdm")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "db", "cricket.duckdb")
CHECKPOINT_PATH = os.path.join(BASE_DIR, "data", "player_profiles_checkpoint.json")
LOG_PATH = os.path.join(BASE_DIR, "data", "player_profiles_seed.log")
SPORTMONKS_CSV = os.path.join(
    BASE_DIR, "data", "raw", "kaggle",
    "Cricket Players Worldwide Dataset 🏏 export 2026-03-28 15-04-25.csv",
)

ESPN_URL = "http://core.espnuk.org/v2/sports/cricket/athletes/{}"
HEADERS = {"User-Agent": "Mozilla/5.0 (CricketStatistician/1.0)"}
DELAY = 0.3  # seconds between API calls
MAX_RETRIES = 3

# Set up logging
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("seed_profiles")


# ── Table creation ──────────────────────────────────────────────────────────

def create_table(con: duckdb.DuckDBPyConnection):
    """Create player_profiles table if it doesn't exist."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS player_profiles (
            cricsheet_id   VARCHAR PRIMARY KEY,
            cricinfo_id    VARCHAR,
            full_name      VARCHAR,
            first_name     VARCHAR,
            last_name      VARCHAR,
            display_name   VARCHAR,
            batting_style  VARCHAR,
            bowling_style  VARCHAR,
            playing_role   VARCHAR,
            country        VARCHAR,
            dob            DATE,
            debut_year     INTEGER,
            is_active      BOOLEAN,
            gender         VARCHAR,
            birth_place    VARCHAR,
            jersey_number  VARCHAR,
            major_teams    VARCHAR,
            headshot_url   VARCHAR,
            seeded_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


# ── ESPN API fetch ──────────────────────────────────────────────────────────

def fetch_espn_profile(cricinfo_id: str) -> dict | None:
    """Fetch player profile from ESPN API with retry + backoff."""
    url = ESPN_URL.format(cricinfo_id)
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            resp = urllib.request.urlopen(req, timeout=15)
            return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                log.warning("ESPN 404 for cricinfo_id=%s", cricinfo_id)
                return None
            if e.code in (429, 503):
                wait = (2 ** attempt) * 2
                log.warning("ESPN %d for id=%s, waiting %ds", e.code, cricinfo_id, wait)
                time.sleep(wait)
                continue
            log.error("ESPN HTTP %d for id=%s: %s", e.code, cricinfo_id, str(e))
            return None
        except Exception as e:
            wait = (2 ** attempt)
            log.error("ESPN error for id=%s attempt %d: %s", cricinfo_id, attempt + 1, str(e))
            time.sleep(wait)
    return None


def parse_espn_data(data: dict) -> dict:
    """Parse ESPN JSON into a flat dict for insertion."""
    styles = data.get("style", [])
    batting_style = None
    bowling_style = None
    for s in styles:
        if s.get("type") == "batting":
            batting_style = s.get("description")
        elif s.get("type") == "bowling":
            bowling_style = s.get("description")

    position = data.get("position") or {}
    birth_place = data.get("birthPlace") or {}
    bp_parts = [birth_place.get("city", ""), birth_place.get("country", "")]
    bp_str = ", ".join(p for p in bp_parts if p) or None

    country = None
    country_ref = data.get("country")
    if isinstance(country_ref, dict):
        country = country_ref.get("name") or country_ref.get("abbreviation")

    headshot = data.get("headshot") or {}
    headshot_url = headshot.get("href")

    major_teams_str = None
    mt = data.get("majorTeams")
    if mt:
        major_teams_str = mt if isinstance(mt, str) else str(mt)

    dob_str = data.get("dateOfBirth")
    dob = None
    if dob_str:
        try:
            dob = dob_str[:10]  # "1988-11-05T00:00Z" → "1988-11-05"
        except Exception:
            pass

    return {
        "full_name": data.get("fullName"),
        "first_name": data.get("firstName"),
        "last_name": data.get("lastName"),
        "display_name": data.get("displayName"),
        "batting_style": batting_style,
        "bowling_style": bowling_style,
        "playing_role": position.get("name") if isinstance(position, dict) else None,
        "country": country,
        "dob": dob,
        "debut_year": data.get("debutYear"),
        "is_active": data.get("isActive"),
        "gender": data.get("gender"),
        "birth_place": bp_str,
        "jersey_number": str(data.get("jersey")) if data.get("jersey") else None,
        "major_teams": major_teams_str,
        "headshot_url": headshot_url,
    }


# ── Checkpoint management ───────────────────────────────────────────────────

def load_checkpoint() -> set:
    """Load set of already-processed cricsheet_ids from checkpoint file."""
    if os.path.exists(CHECKPOINT_PATH):
        try:
            with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return set(data.get("done", []))
        except Exception:
            pass
    return set()


def save_checkpoint(done_ids: set):
    """Save checkpoint."""
    with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
        json.dump({"done": list(done_ids), "updated": datetime.now().isoformat()}, f)


# ── SportMonks CSV fallback ─────────────────────────────────────────────────

def load_sportmonks_lookup() -> dict:
    """Load SportMonks CSV into a name→profile lookup for fallback matching."""
    import csv
    lookup = {}
    if not os.path.exists(SPORTMONKS_CSV):
        return lookup
    try:
        with open(SPORTMONKS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("fullname") or "").strip()
                if name:
                    lookup[name.lower()] = {
                        "batting_style": _normalize_style(row.get("battingstyle", "")),
                        "bowling_style": _normalize_style(row.get("bowlingstyle", "")),
                        "playing_role": row.get("position", ""),
                        "country": row.get("country_name", ""),
                        "gender": "male" if row.get("gender") == "m" else "female" if row.get("gender") == "f" else None,
                        "dob": _parse_sportmonks_dob(row.get("dateofbirth", "")),
                    }
    except Exception as e:
        log.warning("Failed to load SportMonks CSV: %s", e)
    return lookup


def _normalize_style(val: str) -> str | None:
    """Convert SportMonks slug to readable style: 'right-hand-bat' → 'Right-hand bat'."""
    if not val or not val.strip():
        return None
    return val.replace("-", " ").strip().capitalize()


def _parse_sportmonks_dob(val: str) -> str | None:
    """Parse SportMonks DOB format 'DD-MM-YYYY' → 'YYYY-MM-DD'."""
    if not val or not val.strip():
        return None
    try:
        parts = val.strip().split("-")
        if len(parts) == 3:
            return f"{parts[2]}-{parts[1]}-{parts[0]}"
    except Exception:
        pass
    return None


# ── Main seed logic ─────────────────────────────────────────────────────────

def get_players_to_fetch(con: duckdb.DuckDBPyConnection, refresh: bool = False) -> list[tuple]:
    """Get list of (cricsheet_id, key_cricinfo, name) to fetch.

    Full mode: all players with key_cricinfo not already in player_profiles
    Refresh mode: new players + stale active profiles (>90 days old)
    """
    if refresh:
        # New players not yet in profiles
        new_players = con.execute("""
            SELECT p.cricsheet_id, p.key_cricinfo, p.name
            FROM players p
            WHERE p.key_cricinfo IS NOT NULL
              AND p.cricsheet_id NOT IN (SELECT cricsheet_id FROM player_profiles)
        """).fetchall()
        # Stale active profiles
        stale = con.execute("""
            SELECT p.cricsheet_id, p.key_cricinfo, p.name
            FROM players p
            JOIN player_profiles pp ON p.cricsheet_id = pp.cricsheet_id
            WHERE pp.is_active = true
              AND pp.updated_at < CURRENT_TIMESTAMP - INTERVAL '90 DAYS'
        """).fetchall()
        combined = {r[0]: r for r in new_players}
        for r in stale:
            combined[r[0]] = r
        return list(combined.values())
    else:
        # Full seed: only players not yet present in player_profiles
        return con.execute("""
            SELECT p.cricsheet_id, p.key_cricinfo, p.name
            FROM players p
            WHERE p.key_cricinfo IS NOT NULL
              AND p.cricsheet_id NOT IN (SELECT cricsheet_id FROM player_profiles)
            ORDER BY p.cricsheet_id
        """).fetchall()


def upsert_profile(con: duckdb.DuckDBPyConnection, cricsheet_id: str, cricinfo_id: str, profile: dict):
    """Insert or update a player profile."""
    now = datetime.now().isoformat()
    con.execute("""
        INSERT INTO player_profiles (
            cricsheet_id, cricinfo_id, full_name, first_name, last_name, display_name,
            batting_style, bowling_style, playing_role, country, dob, debut_year,
            is_active, gender, birth_place, jersey_number, major_teams, headshot_url,
            seeded_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (cricsheet_id) DO UPDATE SET
            full_name = EXCLUDED.full_name,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            display_name = EXCLUDED.display_name,
            batting_style = COALESCE(EXCLUDED.batting_style, player_profiles.batting_style),
            bowling_style = COALESCE(EXCLUDED.bowling_style, player_profiles.bowling_style),
            playing_role = COALESCE(EXCLUDED.playing_role, player_profiles.playing_role),
            country = COALESCE(EXCLUDED.country, player_profiles.country),
            dob = COALESCE(EXCLUDED.dob, player_profiles.dob),
            debut_year = COALESCE(EXCLUDED.debut_year, player_profiles.debut_year),
            is_active = COALESCE(EXCLUDED.is_active, player_profiles.is_active),
            gender = COALESCE(EXCLUDED.gender, player_profiles.gender),
            birth_place = COALESCE(EXCLUDED.birth_place, player_profiles.birth_place),
            jersey_number = COALESCE(EXCLUDED.jersey_number, player_profiles.jersey_number),
            major_teams = COALESCE(EXCLUDED.major_teams, player_profiles.major_teams),
            headshot_url = COALESCE(EXCLUDED.headshot_url, player_profiles.headshot_url),
            updated_at = EXCLUDED.updated_at
    """, [
        cricsheet_id, cricinfo_id,
        profile.get("full_name"), profile.get("first_name"), profile.get("last_name"),
        profile.get("display_name"), profile.get("batting_style"), profile.get("bowling_style"),
        profile.get("playing_role"), profile.get("country"),
        profile.get("dob"), profile.get("debut_year"),
        profile.get("is_active"), profile.get("gender"),
        profile.get("birth_place"), profile.get("jersey_number"),
        profile.get("major_teams"), profile.get("headshot_url"),
        now, now,
    ])


def sportmonks_fallback(con: duckdb.DuckDBPyConnection, sm_lookup: dict):
    """Fill gaps in player_profiles using SportMonks CSV data (name matching)."""
    # Get profiles missing batting_style
    gaps = con.execute("""
        SELECT pp.cricsheet_id, pp.full_name, pp.display_name
        FROM player_profiles pp
        WHERE pp.batting_style IS NULL
    """).fetchall()

    filled = 0
    for cs_id, full_name, display_name in gaps:
        # Try matching by full_name or display_name
        for name in [full_name, display_name]:
            if name and name.lower() in sm_lookup:
                sm = sm_lookup[name.lower()]
                updates = []
                params = []
                if sm.get("batting_style"):
                    updates.append("batting_style = ?")
                    params.append(sm["batting_style"])
                if sm.get("bowling_style"):
                    updates.append("bowling_style = ?")
                    params.append(sm["bowling_style"])
                if sm.get("playing_role"):
                    updates.append("playing_role = COALESCE(playing_role, ?)")
                    params.append(sm["playing_role"])
                if sm.get("country"):
                    updates.append("country = COALESCE(country, ?)")
                    params.append(sm["country"])
                if updates:
                    params.append(cs_id)
                    con.execute(
                        f"UPDATE player_profiles SET {', '.join(updates)} WHERE cricsheet_id = ?",
                        params,
                    )
                    filled += 1
                break
    return filled


def print_report(con: duckdb.DuckDBPyConnection):
    """Print coverage report."""
    total = con.execute("SELECT COUNT(*) FROM player_profiles").fetchone()[0]
    if total == 0:
        print("player_profiles table is empty.")
        return

    print(f"\n{'='*60}")
    print(f"  PLAYER PROFILES COVERAGE REPORT")
    print(f"{'='*60}")
    print(f"  Total profiles: {total:,}")

    fields = [
        ("batting_style", "Batting Style"),
        ("bowling_style", "Bowling Style"),
        ("playing_role", "Playing Role"),
        ("country", "Country"),
        ("dob", "Date of Birth"),
        ("debut_year", "Debut Year"),
        ("headshot_url", "Headshot URL"),
        ("major_teams", "Major Teams"),
        ("is_active", "Active Status"),
    ]
    for col, label in fields:
        filled = con.execute(
            f"SELECT COUNT(*) FROM player_profiles WHERE {col} IS NOT NULL"
        ).fetchone()[0]
        pct = 100 * filled / total
        bar = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
        print(f"  {label:20s} {bar} {filled:>6,}/{total:,} ({pct:.1f}%)")

    # Cricsheet coverage
    try:
        cs_total = con.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        cs_covered = con.execute("""
            SELECT COUNT(*) FROM players p
            WHERE p.cricsheet_id IN (SELECT cricsheet_id FROM player_profiles)
        """).fetchone()[0]
        print(f"\n  Cricsheet Players Coverage: {cs_covered:,}/{cs_total:,} ({100*cs_covered/cs_total:.1f}%)")
    except Exception:
        pass

    # Active player coverage (appear in deliveries)
    try:
        active = con.execute("""
            SELECT COUNT(DISTINCT p.cricsheet_id) FROM players p
            JOIN player_profiles pp ON p.cricsheet_id = pp.cricsheet_id
            WHERE pp.batting_style IS NOT NULL
              AND (p.name IN (SELECT DISTINCT batter FROM deliveries)
                   OR p.name IN (SELECT DISTINCT bowler FROM deliveries))
        """).fetchone()[0]
        total_active = con.execute("""
            SELECT COUNT(DISTINCT name) FROM (
                SELECT DISTINCT batter AS name FROM deliveries
                UNION
                SELECT DISTINCT bowler AS name FROM deliveries
            )
        """).fetchone()[0]
        print(f"  Active Players with Batting Style: {active:,}/{total_active:,} ({100*active/total_active:.1f}%)")
    except Exception:
        pass

    # Style value distribution
    print(f"\n  {'─'*50}")
    print(f"  Batting Style Distribution:")
    rows = con.execute("""
        SELECT batting_style, COUNT(*) c FROM player_profiles
        WHERE batting_style IS NOT NULL GROUP BY batting_style ORDER BY c DESC
    """).fetchall()
    for style, cnt in rows:
        print(f"    {style}: {cnt:,}")

    print(f"\n  Playing Role Distribution:")
    rows = con.execute("""
        SELECT playing_role, COUNT(*) c FROM player_profiles
        WHERE playing_role IS NOT NULL GROUP BY playing_role ORDER BY c DESC LIMIT 10
    """).fetchall()
    for role, cnt in rows:
        print(f"    {role}: {cnt:,}")

    print(f"{'='*60}\n")


def seed(refresh: bool = False, report_only: bool = False):
    """Main seed function."""
    con = duckdb.connect(DB_PATH)
    create_table(con)

    if report_only:
        print_report(con)
        con.close()
        return

    players = get_players_to_fetch(con, refresh=refresh)
    mode = "refresh" if refresh else "full seed"
    print(f"Player profiles {mode}: {len(players):,} players to process")
    log.info("Starting %s: %d players", mode, len(players))

    if not players:
        print("Nothing to fetch. All profiles are up to date.")
        print_report(con)
        con.close()
        return

    # Release the connection now — we'll reconnect in short bursts for writes
    con.close()

    # Load checkpoint for resume support (full seed only)
    done_ids = load_checkpoint() if not refresh else set()
    remaining = [(cs_id, ci_id, name) for cs_id, ci_id, name in players if cs_id not in done_ids]
    if len(remaining) < len(players):
        print(f"  Resuming from checkpoint: {len(players) - len(remaining):,} already done, {len(remaining):,} remaining")

    # Progress bar
    iterator = remaining
    if tqdm:
        iterator = tqdm(remaining, desc="Fetching profiles", unit="player",
                        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]")

    errors = 0
    fetched = 0
    batch_done = set(done_ids)
    pending_writes: list[tuple] = []  # (cricsheet_id, cricinfo_id, profile_dict)
    BATCH_SIZE = 50  # flush to DB every N players

    for i, (cricsheet_id, cricinfo_id, name) in enumerate(iterator):
        # Fetch from ESPN
        data = fetch_espn_profile(cricinfo_id)
        if data:
            profile = parse_espn_data(data)
            pending_writes.append((cricsheet_id, cricinfo_id, profile))
            fetched += 1
        else:
            # Minimal row so we don't re-fetch next time
            pending_writes.append((cricsheet_id, cricinfo_id, {
                "full_name": name,
                "display_name": name,
            }))
            errors += 1

        batch_done.add(cricsheet_id)

        # Flush batch to DB — short connection, released immediately
        if len(pending_writes) >= BATCH_SIZE or (i + 1) == len(remaining):
            try:
                wcon = duckdb.connect(DB_PATH)
                for cs_id, ci_id, prof in pending_writes:
                    upsert_profile(wcon, cs_id, ci_id, prof)
                wcon.close()
            except Exception as e:
                log.error("Batch write failed: %s", e)
            pending_writes.clear()
            save_checkpoint(batch_done)

        # Update progress bar postfix
        if tqdm and hasattr(iterator, 'set_postfix'):
            iterator.set_postfix(fetched=fetched, errors=errors)

        # Rate limit
        time.sleep(DELAY)

    # Final checkpoint
    save_checkpoint(batch_done)

    print(f"\nESPN fetch complete: {fetched:,} fetched, {errors:,} errors")
    log.info("ESPN complete: %d fetched, %d errors", fetched, errors)

    # SportMonks fallback
    print("Running SportMonks fallback for missing batting_style...")
    sm_lookup = load_sportmonks_lookup()
    if sm_lookup:
        fcon = duckdb.connect(DB_PATH)
        filled = sportmonks_fallback(fcon, sm_lookup)
        fcon.close()
        print(f"  SportMonks filled {filled:,} additional profiles")
        log.info("SportMonks fallback filled %d profiles", filled)
    else:
        print("  SportMonks CSV not found, skipping fallback")

    # Final report
    rcon = duckdb.connect(DB_PATH, read_only=True)
    print_report(rcon)
    rcon.close()

    # Cleanup checkpoint on successful full seed
    if not refresh and os.path.exists(CHECKPOINT_PATH):
        os.remove(CHECKPOINT_PATH)
        print("Checkpoint file cleaned up.")

    print("Done!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed player profiles from ESPN Cricinfo")
    parser.add_argument("--refresh", action="store_true", help="Incremental: new + stale active profiles only")
    parser.add_argument("--report", action="store_true", help="Coverage report only (no fetching)")
    args = parser.parse_args()
    seed(refresh=args.refresh, report_only=args.report)
